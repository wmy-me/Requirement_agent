"""MinIO/S3 对象存储适配器，用于保存需求附件（PDF/Word/图片等）。

支持两级存储策略：
1. 优先走 MinIO/S3（`_client` 就绪时），返回 `s3://bucket/key` URI；
2. MinIO 未配置或调用失败时，自动降级写入本地 `storage/uploads/` 目录并返回 `file://` URI。

降级是运行时容错设计：附件入库不因对象存储不可用而失败。
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import settings

try:
    from minio import Minio
except ImportError:  # pragma: no cover - optional dependency for local dev
    Minio = None


@dataclass(slots=True)
class StoredObject:
    """一次上传产物的元信息：落在哪个 bucket/key、URI、大小与内容 SHA-256 校验和。"""

    bucket: str
    object_key: str
    uri: str
    size: int
    checksum: str


class ObjectStorage:
    """需求附件存储适配器（MinIO 优先、本地降级）。"""

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
    ) -> None:
        self.endpoint = endpoint or settings.minio_endpoint
        self.access_key = access_key if access_key is not None else settings.minio_access_key
        self.secret_key = secret_key if secret_key is not None else settings.minio_secret_key.get_secret_value()
        self.bucket = bucket or settings.minio_bucket
        # 本地回退目录：$PROJECT_ROOT/storage/uploads，首次访问自动创建
        self.local_root = Path(__file__).resolve().parents[3] / "storage" / "uploads"
        self.local_root.mkdir(parents=True, exist_ok=True)
        self._client = None
        # 仅当配置齐全且驱动可导入时才尝试连接 MinIO；失败则静默降级本地
        if self._require_configuration() and Minio is not None:
            try:
                self._client = Minio(
                    endpoint=self.endpoint,
                    access_key=self.access_key,
                    secret_key=self.secret_key,
                    secure=False,
                )
                self._ensure_bucket(self.bucket)
            except Exception:
                self._client = None

    def _require_configuration(self) -> bool:
        """判断是否具备连接 MinIO 的必要配置项（endpoint/ak/sk/bucket 全齐）。"""
        if self.endpoint and self.access_key and self.secret_key and self.bucket:
            return True
        return False

    def _ensure_bucket(self, bucket_name: str) -> None:
        """确保 bucket 存在（MinIO 未启用时为空操作）。"""
        if self._client is None:
            return
        if not self._client.bucket_exists(bucket_name):
            self._client.make_bucket(bucket_name)

    def upload(self, file_name: str, payload: bytes, *, bucket: str | None = None) -> StoredObject:
        """上传原始字节流到对象存储（或本地回退），返回 `StoredObject`。

        始终计算 `payload` 的 SHA-256 作为校验和，供后续去重/一致性核对。
        """
        actual_bucket = bucket or self.bucket
        object_key = self._build_object_key(file_name)
        checksum = hashlib.sha256(payload).hexdigest()

        if self._client is not None:
            try:
                self._ensure_bucket(actual_bucket)
                content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
                with io.BytesIO(payload) as stream:
                    self._client.put_object(
                        actual_bucket,
                        object_key,
                        stream,
                        length=len(payload),
                        content_type=content_type,
                    )
                uri = f"s3://{actual_bucket}/{object_key}"
                return StoredObject(
                    bucket=actual_bucket,
                    object_key=object_key,
                    uri=uri,
                    size=len(payload),
                    checksum=checksum,
                )
            except Exception:
                # MinIO 运行时异常：置空 client，本次与后续请求均走本地回退
                self._client = None

        # —— 本地回退：写入 storage/uploads 并用 file:// URI 表达 ——
        local_path = self.local_root / object_key.replace("/", "_")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(payload)
        uri = f"file://{local_path}"
        return StoredObject(
            bucket=actual_bucket,
            object_key=object_key,
            uri=uri,
            size=len(payload),
            checksum=checksum,
        )

    def _build_object_key(self, file_name: str) -> str:
        """构造对象键：`uploads/{文件名}-{名称sha1前12位}{后缀}`，避免同名覆盖与路径穿越。"""
        suffix = Path(file_name).suffix or ".bin"
        stem = Path(file_name).stem or "attachment"
        key = f"{stem}-{hashlib.sha1(file_name.encode('utf-8')).hexdigest()[:12]}{suffix}"
        return f"uploads/{key}"