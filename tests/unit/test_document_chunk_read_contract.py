"""Document-detail read contracts: original chunks and real indexing state."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from requirement_agent.api.routes import documents


class _DocumentReadRepo:
    def get_chunk_page(self, document_id: int, *, limit: int, offset: int):
        assert (document_id, limit, offset) == (9, 2, 2)
        return {
            "items": [{"id": "chunk", "document_id": "9", "chunk_index": 3,
                       "chunk_text": "数据库原文", "char_count": 5}],
            "total": 7, "limit": limit, "offset": offset,
        }

    def get_index_status(self, document_id: int):
        if document_id == 404:
            return None
        return {"document_id": str(document_id), "index_status": "ready", "total_chunks": 7,
                "embedded_chunks": 7, "embedding_models": ["test-embedding"],
                "embedding_dimensions": [4], "chunking": {"source": "fixed-slice", "chunk_size": 600, "overlap": 120},
                "outbox": {"status": "completed"}}


def test_chunk_endpoint_keeps_original_text_and_exposes_total(monkeypatch) -> None:
    monkeypatch.setattr(documents, "document_repo", _DocumentReadRepo())

    payload = asyncio.run(documents.get_document_chunks(9, limit=2, offset=2))

    assert payload["document_id"] == "9"
    assert payload["total"] == 7
    assert payload["items"][0]["chunk_text"] == "数据库原文"


def test_index_status_is_not_confused_with_document_version_status(monkeypatch) -> None:
    monkeypatch.setattr(documents, "document_repo", _DocumentReadRepo())

    payload = asyncio.run(documents.get_document_index_status(9))

    assert payload["index_status"] == "ready"
    assert payload["embedded_chunks"] == payload["total_chunks"]


def test_index_status_returns_404_only_for_missing_document(monkeypatch) -> None:
    monkeypatch.setattr(documents, "document_repo", _DocumentReadRepo())

    with pytest.raises(HTTPException) as error:
        asyncio.run(documents.get_document_index_status(404))

    assert error.value.status_code == 404
