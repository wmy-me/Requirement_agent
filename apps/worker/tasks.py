"""Outbox-based async tasks and retry workers."""

from __future__ import annotations

from fastapi import FastAPI

from src.infrastructure.worker.tasks import EmbeddingTask

app = FastAPI(
    title="Requirement Agent Worker",
    version="0.1.0",
    description="Outbox worker and async task entrypoint.",
)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


def enqueue_embedding_sync(*, requirement_key: str = "REQ-000001", content: str = "") -> str:
    """Queue a background embedding synchronization job."""
    task = EmbeddingTask()
    return task.enqueue(requirement_key=requirement_key, content=content)
