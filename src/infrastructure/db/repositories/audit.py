"""审计 Repository：`AuditRepository` 事件留痕（谁·何时·对什么·结果）。"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.common.snowflake import new_id
from src.common.time import as_display_iso
from src.domain.requirement import AuditEvent
from src.infrastructure.db.session import SessionLocal


class AuditRepository:
    """Persistence boundary for audit event records（审计事件写入/查询）。"""

    def record(self, event: AuditEvent, session: Session | None = None) -> AuditEvent:
        """写入一条审计事件（谁·何时·对什么做了什么·前后数据·结果），用于全程留痕与合规回溯。"""
        owns_session = session is None
        session = session or SessionLocal()
        try:
            session.execute(
                text(
                    """
                    INSERT INTO audit_event (
                        id, trace_id, event_type, aggregate_type, aggregate_id, actor_type, actor_id,
                        before_data, after_data, result_status, error_code
                    ) VALUES (
                        :id, :trace_id, :event_type, :aggregate_type, :aggregate_id, :actor_type, :actor_id,
                        :before_data, :after_data, :result_status, :error_code
                    )
                    """
                ),
                {
                    "id": new_id(),
                    "trace_id": event.trace_id,
                    "event_type": event.event_type,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": event.aggregate_id,
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "before_data": json.dumps(event.before_data or {}),
                    "after_data": json.dumps(event.after_data or {}),
                    "result_status": event.result_status,
                    "error_code": event.error_code,
                },
            )
            if owns_session:
                session.commit()
                session.close()
            return event
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def list(self) -> list[AuditEvent]:
        """按时间倒序返回最近 100 条审计事件（AuditEvent 对象）。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT trace_id, event_type, aggregate_type, aggregate_id, actor_type, actor_id, before_data, after_data, result_status, error_code FROM audit_event ORDER BY created_at DESC LIMIT 100"
                )
            ).mappings().all()
        return [
            AuditEvent(
                trace_id=str(item["trace_id"]),
                event_type=str(item["event_type"]),
                aggregate_type=str(item["aggregate_type"]),
                aggregate_id=item["aggregate_id"],
                actor_type=str(item["actor_type"]),
                actor_id=item["actor_id"],
                before_data=dict(item["before_data"] or {}),
                after_data=dict(item["after_data"] or {}),
                result_status=str(item["result_status"]),
                error_code=item["error_code"],
            )
            for item in rows
        ]

    def list_dicts(self, limit: int = 50) -> list[dict[str, object]]:
        """按时间倒序返回审计事件 dict 列表，供审计面板/回放展示。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT trace_id, event_type, aggregate_type, aggregate_id, actor_type,
                           actor_id, before_data, after_data, result_status, error_code, created_at
                    FROM audit_event
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [
            {
                "trace_id": row["trace_id"],
                "event_type": row["event_type"],
                "aggregate_type": row["aggregate_type"],
                "aggregate_id": row["aggregate_id"],
                "actor_type": row["actor_type"],
                "actor_id": row["actor_id"],
                "before_data": dict(row["before_data"] or {}),
                "after_data": dict(row["after_data"] or {}),
                "result_status": row["result_status"],
                "error_code": row["error_code"],
                "created_at": as_display_iso(row["created_at"]),
            }
            for row in rows
        ]

