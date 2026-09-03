"""LLM embedding service adapter."""

from __future__ import annotations


class EmbeddingService:
    """Wraps the model used to convert requirement text into vectors."""

    def embed(self, text: str) -> list[float]:
        return [0.0] * 1536
