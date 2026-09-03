"""MinIO/S3 adapter for requirement attachments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class StoredObject:
    bucket: str
    object_key: str
    uri: str
    size: int
    checksum: str


class ObjectStorage:
    """Stores attachments such as PDFs, Word docs, and screenshots."""

    def __init__(self, endpoint: str = "localhost:9000", access_key: str = "minioadmin", secret_key: str = "minioadmin") -> None:
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = "requirement-attachments"

    def upload(self, file_name: str, payload: bytes, *, bucket: str | None = None) -> StoredObject:
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
