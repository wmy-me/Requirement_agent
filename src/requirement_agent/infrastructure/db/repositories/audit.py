"""审计 Repository：`AuditRepository` 事件留痕（谁·何时·对什么·结果）。"""

from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.common.snowflake import new_id, to_sid
from requirement_agent.common.time import as_display_iso
from requirement_agent.domain.requirement import AuditEvent
from requirement_agent.infrastructure.db.session import SessionLocal

#: 审计载荷里**按名字**判定为 id 的键。
_AUDIT_ID_KEYS = ("id",)
_AUDIT_ID_SUFFIX = "_id"


def _stringify_audit_payload(payload: object) -> dict[str, object]:
    """把审计载荷里的雪花 id 字符串化（递归、**按键名**判定）。

    ## 为什么这里可以用「按名字」的规则

    `before_data` / `after_data` 是**任意聚合的快照**（`{"source_id": …, "version": …}`），
    写进来的时候没有统一的形状，所以没法像别的端点那样逐字段列举。
    按「键名是 `id` 或以 `_id` 结尾」递归转换，是能覆盖它的唯一办法。

    ## 它与契约禁止的那个「全局 JSON 编码器」不是一回事

    契约 §10-T1 第 3 条反对的是**按值大小**判断的编码器 —— 那会让同一个字段
    在小 id 时是 number、大 id 时是 string，正是 §8.1 警告的「同一数组里两种元素形状」。
    这里判据是**键名**：同一个键在任何一行、任何时候写的数据上都是同一种类型，
    所以那个问题不存在。

    顺带一提，UUID 字符串经 `to_sid` 原样返回（它只做 `str().strip()`），
    所以 `conversation_id`、`trace_id`、`run_id` 这类不会被改坏。
    """

    def walk(node: object, depth: int = 0) -> object:
        if depth > 6:  # 防御性深度上限：审计载荷不该有这么深，有也不该让本函数爆栈
            return node
        if isinstance(node, Mapping):
            return {
                str(key): (
                    to_sid(value)
                    if str(key) in _AUDIT_ID_KEYS or str(key).endswith(_AUDIT_ID_SUFFIX)
                    else walk(value, depth + 1)
                )
                for key, value in node.items()
            }
        if isinstance(node, (list, tuple)):
            return [walk(item, depth + 1) for item in node]
        return node

    result = walk(payload)
    return result if isinstance(result, dict) else {}


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
                "before_data": _stringify_audit_payload(row["before_data"]),
                "after_data": _stringify_audit_payload(row["after_data"]),
                "result_status": row["result_status"],
                "error_code": row["error_code"],
                "created_at": as_display_iso(row["created_at"]),
            }
            for row in rows
        ]

