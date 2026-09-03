"""pgvector repository adapter."""

from __future__ import annotations


class RequirementVectorRepository:
    """Uses pgvector for semantic retrieval of requirement embeddings."""

    def search(self, query_vector: list[float], limit: int = 10) -> list[dict[str, object]]:
        return [{"query_vector": query_vector, "limit": limit}]
