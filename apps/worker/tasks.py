"""Outbox-based async tasks and retry workers."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="Requirement Agent Worker",
    version="0.1.0",
    description="Outbox worker and async task entrypoint.",
)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


def enqueue_embedding_sync() -> str:
    """Placeholder for embedding synchronization.

    Real implementation should read from the Outbox table and synchronize
    requirement vectors to pgvector after approval.
    """
    return "embedding_sync_enqueued"
