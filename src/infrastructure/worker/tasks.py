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

    def enqueue(self, *, requirement_id: int, requirement_key: str, content: str) -> str:
        event = self.outbox_repo.enqueue(
            aggregate_type="requirement_master",
            aggregate_id=requirement_key,
            event_type="embedding_sync",
            payload={
                "requirement_id": requirement_id,
                "requirement_key": requirement_key,
                "content": content,
            },
        )
        return f"queued:{event.id}:{event.aggregate_id}:{event.event_type}"

    def process_pending(self, *, limit: int = 20, max_retries: int = 3) -> list[str]:
        results: list[str] = []
        claim = getattr(self.outbox_repo, "claim_pending", None)
        events = claim(limit=limit, event_type="embedding_sync") if claim else self.outbox_repo.list_pending(limit)
        for event in events:
            try:
                requirement_id = int(event.payload["requirement_id"])
                content = str(event.payload.get("content") or "")
                vector = self.embedding_service.embed(content)
                self.vector_repo.upsert(requirement_id, vector, source_text=content)
                self.outbox_repo.mark_done(event)
                results.append(f"processed:{event.id}:{event.aggregate_id}")
            except Exception as exc:
                failed = self.outbox_repo.mark_failed(event, error=str(exc), max_retries=max_retries)
                results.append(f"{failed.status}:{event.id}:{event.aggregate_id}")
        return results
