"""Outbox-based async tasks and retry workers."""

from __future__ import annotations

from fastapi import FastAPI

from requirement_agent.infrastructure.worker.tasks import DocumentChunkingTask, EmbeddingTask
from requirement_agent.infrastructure.worker.outbox import OutboxRepository

app = FastAPI(
    title="Requirement Agent Worker",
    version="0.1.0",
    description="Outbox worker and async task entrypoint.",
)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tasks/embedding/process")
async def process_embedding_events(limit: int = 20) -> dict[str, object]:
    task = EmbeddingTask()
    results = task.process_pending(limit=limit)
    return {"status": "ok", "results": results}


@app.post("/tasks/document-chunk/process")
async def process_document_chunk_events(limit: int = 20) -> dict[str, object]:
    task = DocumentChunkingTask()
    results = task.process_pending(limit=limit)
    return {"status": "ok", "results": results}


@app.get("/tasks/dead-letter")
async def list_dead_letter_events(limit: int = 50) -> dict[str, object]:
    events = OutboxRepository().list_dead_letters(limit=limit)
    return {
        "items": [
            {
                "id": event.id,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "retries": event.retries,
                "status": event.status,
                "last_error": event.last_error,
            }
            for event in events
        ]
    }


def enqueue_embedding_sync(*, requirement_id: int = 1, requirement_key: str = "REQ-000001", content: str = "") -> str:
    """Queue a background embedding synchronization job."""
    task = EmbeddingTask()
    return task.enqueue(requirement_id=requirement_id, requirement_key=requirement_key, content=content)


def enqueue_document_chunk_sync(*, document_id: int, content: str, chunk_size: int = 600, overlap: int = 120) -> str:
    """Queue a background document chunking job."""
    task = DocumentChunkingTask()
    return task.enqueue(document_id=document_id, content=content, chunk_size=chunk_size, overlap=overlap)
