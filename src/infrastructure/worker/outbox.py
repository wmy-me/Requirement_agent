"""Database-backed outbox event handling for async requirement workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.infrastructure.db.session import SessionLocal


@dataclass(slots=True)
class OutboxEvent:
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    retries: int = 0
    status: str = "pending"
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OutboxRepository:
    """Persists outbox events and provides worker-safe claim/retry transitions."""

    def enqueue(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        session: Session | None = None,
    ) -> OutboxEvent:
        owns_session = session is None
        session = session or SessionLocal()
        event_payload = payload or {}
        try:
            row = session.execute(
                text(
                    """
                    INSERT INTO outbox_event (aggregate_type, aggregate_id, event_type, payload, status)
                    VALUES (:aggregate_type, :aggregate_id, :event_type, CAST(:payload AS JSONB), 'pending')
                    RETURNING id, aggregate_type, aggregate_id, event_type, payload, status,
                              retry_count, last_error, created_at
                    """
                ),
                {
                    "aggregate_type": aggregate_type,
                    "aggregate_id": aggregate_id,
                    "event_type": event_type,
                    "payload": json.dumps(event_payload),
                },
            ).mappings().one()
            if owns_session:
                session.commit()
                session.close()
            return self._from_row(row)
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def claim_pending(self, *, limit: int = 20, event_type: str | None = None) -> list[OutboxEvent]:
        event_filter = "AND event_type = :event_type" if event_type else ""
        values: dict[str, object] = {"limit": limit}
        if event_type:
            values["event_type"] = event_type
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    f"""
                    WITH candidates AS (
                        SELECT id
                        FROM outbox_event
                        WHERE status = 'pending' {event_filter}
                        ORDER BY created_at ASC
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE outbox_event e
                    SET status = 'processing',
                        locked_at = NOW(),
                        updated_at = NOW()
                    FROM candidates
                    WHERE e.id = candidates.id
                    RETURNING e.id, e.aggregate_type, e.aggregate_id, e.event_type, e.payload,
                              e.status, e.retry_count, e.last_error, e.created_at
                    """
                ),
                values,
            ).mappings().all()
            session.commit()
        return [self._from_row(row) for row in rows]

    def list_pending(self, limit: int = 20) -> list[OutboxEvent]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, aggregate_type, aggregate_id, event_type, payload, status,
                           retry_count, last_error, created_at
                    FROM outbox_event
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [self._from_row(row) for row in rows]

    def mark_done(self, event: OutboxEvent, session: Session | None = None) -> OutboxEvent:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    UPDATE outbox_event
                    SET status = 'completed',
                        last_error = NULL,
                        locked_at = NULL,
                        updated_at = NOW()
                    WHERE id = :id
                    RETURNING id, aggregate_type, aggregate_id, event_type, payload, status,
                              retry_count, last_error, created_at
                    """
                ),
                {"id": event.id},
            ).mappings().one()
            if owns_session:
                session.commit()
                session.close()
            return self._from_row(row)
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def mark_failed(
        self,
        event: OutboxEvent,
        *,
        error: str,
        max_retries: int = 3,
        session: Session | None = None,
    ) -> OutboxEvent:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            next_retry_count = event.retries + 1
            next_status = "dead_letter" if next_retry_count >= max_retries else "pending"
            row = session.execute(
                text(
                    """
                    UPDATE outbox_event
                    SET status = :status,
                        retry_count = :retry_count,
                        last_error = :last_error,
                        locked_at = NULL,
                        updated_at = NOW()
                    WHERE id = :id
                    RETURNING id, aggregate_type, aggregate_id, event_type, payload, status,
                              retry_count, last_error, created_at
                    """
                ),
                {
                    "id": event.id,
                    "status": next_status,
                    "retry_count": next_retry_count,
                    "last_error": error[:2_000],
                },
            ).mappings().one()
            if owns_session:
                session.commit()
                session.close()
            return self._from_row(row)
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def list_dead_letters(self, limit: int = 50) -> list[OutboxEvent]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, aggregate_type, aggregate_id, event_type, payload, status,
                           retry_count, last_error, created_at
                    FROM outbox_event
                    WHERE status = 'dead_letter'
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: Any) -> OutboxEvent:
        return OutboxEvent(
            id=int(row["id"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"] or ""),
            event_type=str(row["event_type"]),
            payload=dict(row["payload"] or {}),
            retries=int(row["retry_count"]),
            status=str(row["status"]),
            last_error=row["last_error"],
            created_at=row["created_at"],
        )
