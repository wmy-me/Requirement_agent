"""Database-backed outbox event handling for async requirement workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.common.snowflake import new_id
from src.common.time import utc_now
from src.config.settings import settings
from src.infrastructure.db.session import SessionLocal


@dataclass(slots=True)
class OutboxEvent:
    """Outbox 事件：与业务写同一事务入队，供 worker 异步处理（如 embedding 同步）。"""

    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    retries: int = 0
    status: str = "pending"
    last_error: str | None = None
    created_at: datetime = field(default_factory=utc_now)


class OutboxRepository:
    """持久化 outbox 事件，提供 worker 安全认领/重试/死信流转。

    事务约定：与业务状态更新共用同一 session 入队，保证“业务提交成功即事件已持久化”。
    """

    def __init__(self, stale_timeout_seconds: int | None = None) -> None:
        """构造仓库；`stale_timeout_seconds` 缺省取 settings.outbox_stale_timeout_seconds。"""
        self.stale_timeout_seconds = (
            settings.outbox_stale_timeout_seconds if stale_timeout_seconds is None else stale_timeout_seconds
        )

    def enqueue(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        session: Session | None = None,
    ) -> OutboxEvent:
        """写入一条 pending 事件；传入 session 时与调用方业务同事务，否则自建。"""
        owns_session = session is None
        session = session or SessionLocal()
        event_payload = payload or {}
        try:
            row = session.execute(
                text(
                    """
                    INSERT INTO outbox_event (id, aggregate_type, aggregate_id, event_type, payload, status)
                    VALUES (:id, :aggregate_type, :aggregate_id, :event_type, CAST(:payload AS JSONB), 'pending')
                    RETURNING id, aggregate_type, aggregate_id, event_type, payload, status,
                              retry_count, last_error, created_at
                    """
                ),
                {
                    "id": new_id(),
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

    def claim_pending(
        self,
        *,
        limit: int = 20,
        event_type: str | None = None,
        stale_timeout_seconds: int | None = None,
    ) -> list[OutboxEvent]:
        """以 `FOR UPDATE SKIP LOCKED` 认领待处理事件并置为 processing。

        供多个 worker 并发安全消费：一条事件只被一个 worker 领走；失败后按
        mark_failed 重试或转 dead_letter。

        顺带回收“认领后崩溃/卡死”的事件：`locked_at` 早于 stale_timeout_seconds 的
        processing 事件会被重新认领（retry_count 递增），避免任务永久卡死在处理中。
        """
        timeout = self.stale_timeout_seconds if stale_timeout_seconds is None else stale_timeout_seconds
        stale_cutoff = utc_now() - timedelta(seconds=timeout)
        event_filter = "AND event_type = :event_type" if event_type else ""
        values: dict[str, object] = {"limit": limit, "stale_cutoff": stale_cutoff}
        if event_type:
            values["event_type"] = event_type
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    f"""
                    WITH candidates AS (
                        SELECT id
                        FROM outbox_event
                        WHERE (
                            status = 'pending'
                            OR (status = 'processing' AND locked_at < :stale_cutoff)
                        )
                        {event_filter}
                        ORDER BY created_at ASC
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE outbox_event e
                    SET status = 'processing',
                        retry_count = e.retry_count
                            + CASE WHEN e.status = 'processing' THEN 1 ELSE 0 END,
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
        """返回待处理（status='pending'）事件，按创建时间升序。

        与 `claim_pending` 不同，本方法**不加锁**，仅用于只读排查/展示，
        不推荐作为 worker 消费入口（消费应走 claim_pending 保证并发安全）。
        """
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
        """事件执行成功：置 completed，清空 last_error 与 locked_at。

        幂等：重复调用同一 event 只是再置一次 completed，无害。
        """
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
        """事件执行失败：retry_count+1；未达上限回 pending 以重试，达到上限转 dead_letter。"""
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
        """返回已死亡（status='dead_letter'）事件，按最近更新时间倒序，供人工介入排查。"""
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
