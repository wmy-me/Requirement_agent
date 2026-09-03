"""Async tasks for embedding generation and vector synchronization."""

from __future__ import annotations

from src.infrastructure.embedding.embedding_service import EmbeddingService
from src.infrastructure.vector.pgvector_repository import RequirementVectorRepository
from src.infrastructure.worker.outbox import OutboxRepository


class EmbeddingTask:
    """Background task that converts requirement text to a vector and updates the search index."""

    def __init__(
        self,
        outbox_repo: OutboxRepository | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_repo: RequirementVectorRepository | None = None,
    ) -> None:
        self.outbox_repo = outbox_repo or OutboxRepository()
        self.embedding_service = embedding_service or EmbeddingService()
        self.vector_repo = vector_repo or RequirementVectorRepository()

    def enqueue(self, *, requirement_key: str, content: str) -> str:
        event = self.outbox_repo.enqueue(
            aggregate_type="requirement_master",
            aggregate_id=requirement_key,
            event_type="embedding_sync",
            payload={"requirement_key": requirement_key, "content": content},
        )
        return f"queued:{event.aggregate_id}:{event.event_type}"

    def process_pending(self) -> list[str]:
        results: list[str] = []
        for event in self.outbox_repo.list_pending():
            content = str(event.payload.get("content") or "")
            vector = self.embedding_service.embed(content)
            self.vector_repo.search(vector, limit=1)
            self.outbox_repo.mark_done(event)
            results.append(f"processed:{event.aggregate_id}")
        return results
