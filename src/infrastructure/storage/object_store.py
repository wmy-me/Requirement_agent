"""MinIO/S3 adapter stub."""

from __future__ import annotations


class ObjectStorage:
    """Stores attachments such as PDFs, Word docs, and screenshots."""

    def upload(self, file_name: str, payload: bytes) -> str:
        return f"s3://requirements/{file_name}"
