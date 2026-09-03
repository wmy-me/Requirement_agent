"""Outbox-based async tasks and retry workers."""

from __future__ import annotations


def enqueue_embedding_sync() -> str:
    """Placeholder for embedding synchronization.

    Real implementation should read from the Outbox table and synchronize
    requirement vectors to pgvector after approval.
    """
    return "embedding_sync_enqueued"
