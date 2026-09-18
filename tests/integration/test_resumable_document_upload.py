import hashlib

import pytest
from sqlalchemy import text

from requirement_agent.infrastructure.db.repositories.document import DocumentAssetRepository
from requirement_agent.infrastructure.db.session import SessionLocal


def test_resumable_upload_validates_chunks_and_completion() -> None:
    repo = DocumentAssetRepository()
    payload = b"abcdef"
    upload = repo.create_upload_session(file_name="resume.txt", content_type="text/plain", total_size=len(payload), total_chunks=2, checksum=hashlib.sha256(payload).hexdigest())
    try:
        repo.put_upload_chunk(upload_id=str(upload["upload_id"]), chunk_index=1, payload=b"def", checksum=hashlib.sha256(b"def").hexdigest())
        with pytest.raises(ValueError, match="incomplete"):
            repo.complete_upload(str(upload["upload_id"]))
        repo.put_upload_chunk(upload_id=str(upload["upload_id"]), chunk_index=0, payload=b"abc", checksum=hashlib.sha256(b"abc").hexdigest())
        state, assembled = repo.complete_upload(str(upload["upload_id"])) or ({}, b"")
        assert assembled == payload
        assert state["received_chunks"] == [0, 1]
    finally:
        with SessionLocal() as session:
            session.execute(text("DELETE FROM document_upload_session WHERE id=CAST(:id AS UUID)"), {"id": upload["upload_id"]})
            session.commit()
