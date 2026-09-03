"""Outbox event handling for background embedding synchronization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class OutboxEvent:
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    retries: int = 0
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OutboxRepository:
    """In-memory repository for outbox event workflow simulation."""

    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []

    def enqueue(self, *, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict[str, Any] | None = None) -> OutboxEvent:
        event = OutboxEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload or {},
        )
        self.events.append(event)
        return event

    def list_pending(self) -> list[OutboxEvent]:
        return [event for event in self.events if event.status == "pending"]

    def mark_done(self, event: OutboxEvent) -> OutboxEvent:
        event.status = "done"
        return event

    def mark_failed(self, event: OutboxEvent, *, retries: int | None = None) -> OutboxEvent:
        event.retries = retries if retries is not None else event.retries + 1
        event.status = "failed" if event.retries >= 3 else "pending"
        return event
