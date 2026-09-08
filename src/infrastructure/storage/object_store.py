"""MinIO/S3 adapter for requirement attachments."""

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
    bucket: str
    object_key: str
    uri: str
    size: int
    checksum: str


class ObjectStorage:
    """Stores attachments such as PDFs, Word docs, and screenshots."""

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
        self.local_root = Path(__file__).resolve().parents[3] / "storage" / "uploads"
        self.local_root.mkdir(parents=True, exist_ok=True)
        self._client = None
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
        if self.endpoint and self.access_key and self.secret_key and self.bucket:
            return True
        return False

    def _ensure_bucket(self, bucket_name: str) -> None:
        if self._client is None:
            return
        if not self._client.bucket_exists(bucket_name):
            self._client.make_bucket(bucket_name)

    def upload(self, file_name: str, payload: bytes, *, bucket: str | None = None) -> StoredObject:
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
                self._client = None

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
        suffix = Path(file_name).suffix or ".bin"
        stem = Path(file_name).stem or "attachment"
        key = f"{stem}-{hashlib.sha1(file_name.encode('utf-8')).hexdigest()[:12]}{suffix}"
        return f"uploads/{key}"
