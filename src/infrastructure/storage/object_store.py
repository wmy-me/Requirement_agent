"""MinIO/S3 adapter for requirement attachments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import settings


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

    def _require_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("MINIO_ENDPOINT", self.endpoint),
                ("MINIO_ACCESS_KEY", self.access_key),
                ("MINIO_SECRET_KEY", self.secret_key),
                ("MINIO_BUCKET", self.bucket),
            )
            if not value.strip()
        ]
        if missing:
            raise RuntimeError(f"Missing object storage configuration: {', '.join(missing)}")

    def upload(self, file_name: str, payload: bytes, *, bucket: str | None = None) -> StoredObject:
        self._require_configuration()
        actual_bucket = bucket or self.bucket
        object_key = self._build_object_key(file_name)
        checksum = hashlib.sha256(payload).hexdigest()
        uri = f"s3://{actual_bucket}/{object_key}"
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
