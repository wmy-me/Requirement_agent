"""会话 Repository：`ChatRepository` 多轮对话/消息/Agent run 持久化。

含 `client_message_id` 幂等（断线重发回放已完成结果）与 run 状态机。
"""

from __future__ import annotations

import json

from sqlalchemy import text

from src.common.time import as_display_iso
from src.infrastructure.db.session import SessionLocal


class ChatRepository:
    """Persistence for multi-session chat state and run tracking."""

    def create_conversation(
        self,
        *,
        actor_id: str = "api-user",
        title: str | None = None,
        summary: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """新建一条对话（默认标题“新对话”），返回规范化后的对话行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_conversation (actor_id, title, summary, meta)
                    VALUES (:actor_id, :title, :summary, CAST(:meta AS JSONB))
                    RETURNING id, actor_id, title, summary, status, meta, created_at, updated_at
                    """
                ),
                {
                    "actor_id": actor_id,
                    "title": (title or "新对话").strip() or "新对话",
                    "summary": summary,
                    "meta": json.dumps(meta or {}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_conversation_row(row)

    def get_conversation(self, conversation_id: str, actor_id: str = "api-user") -> dict[str, object] | None:
        """按 id 与 actor 取对话；不存在返回 None。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, actor_id, title, summary, status, meta, created_at, updated_at
                    FROM agent_conversation
                    WHERE id = CAST(:conversation_id AS UUID) AND actor_id = :actor_id
                    """
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().first()
        return self._normalize_conversation_row(row) if row else None

    def list_conversations(self, actor_id: str = "api-user", limit: int = 20, offset: int = 0) -> list[dict[str, object]]:
        """按 actor 列出对话（含消息数），最近更新优先，支持分页。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT c.id, c.actor_id, c.title, c.summary, c.status, c.meta, c.created_at, c.updated_at,
                           COUNT(m.id) AS message_count
                    FROM agent_conversation c
                    LEFT JOIN agent_message m ON m.conversation_id = c.id
                    WHERE c.actor_id = :actor_id
                    GROUP BY c.id, c.actor_id, c.title, c.summary, c.status, c.meta, c.created_at, c.updated_at
                    ORDER BY c.updated_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"actor_id": actor_id, "limit": limit, "offset": offset},
            ).mappings().all()
        return [self._normalize_conversation_row(row, message_count=row["message_count"]) for row in rows]

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        status: str | None = None,
        meta: dict[str, object] | None = None,
        actor_id: str = "api-user",
    ) -> dict[str, object] | None:
        """按需更新对话的 title/summary/status/meta 字段，返回更新后的对话行。"""
        fields: list[str] = []
        values: dict[str, object] = {"conversation_id": conversation_id, "actor_id": actor_id}
        if title is not None:
            fields.append("title = :title")
            values["title"] = title.strip() or "新对话"
        if summary is not None:
            fields.append("summary = :summary")
            values["summary"] = summary
        if status is not None:
            fields.append("status = :status")
            values["status"] = status
        if meta is not None:
            fields.append("meta = CAST(:meta AS JSONB)")
            values["meta"] = json.dumps(meta)
        if not fields:
            return self.get_conversation(conversation_id, actor_id=actor_id)
        sql = "UPDATE agent_conversation SET " + ", ".join(fields) + ", updated_at = NOW() WHERE id = CAST(:conversation_id AS UUID) AND actor_id = :actor_id RETURNING id, actor_id, title, summary, status, meta, created_at, updated_at"
        with SessionLocal() as session:
            row = session.execute(text(sql), values).mappings().first()
            session.commit()
        return self._normalize_conversation_row(row) if row else None

    def delete_conversation(self, conversation_id: str, actor_id: str = "api-user") -> bool:
        """按 id 与 actor 删除一条对话，返回是否实际删除。"""
        with SessionLocal() as session:
            result = session.execute(
                text(
                    "DELETE FROM agent_conversation WHERE id = CAST(:conversation_id AS UUID) AND actor_id = :actor_id"
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            )
            session.commit()
        return result.rowcount > 0

    def get_messages(self, conversation_id: str) -> list[dict[str, object]]:
        """按对话 id 取全部消息（时间升序），返回规范化消息列表。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    FROM agent_message
                    WHERE conversation_id = CAST(:conversation_id AS UUID)
                    ORDER BY created_at ASC
                    """
                ),
                {"conversation_id": conversation_id},
            ).mappings().all()
        return [self._normalize_message_row(row) for row in rows]

    def upsert_user_message(
        self,
        *,
        conversation_id: str,
        content: str,
        actor_id: str = "api-user",
        client_message_id: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """写入一条用户消息（按 client_message_id 幂等），返回规范化后的消息行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_message (conversation_id, role, content, client_message_id, meta)
                    VALUES (CAST(:conversation_id AS UUID), 'user', :content, :client_message_id, CAST(:meta AS JSONB))
                    ON CONFLICT (conversation_id, client_message_id) WHERE client_message_id IS NOT NULL DO UPDATE SET
                        content = EXCLUDED.content,
                        meta = EXCLUDED.meta
                    RETURNING id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "content": content,
                    "client_message_id": client_message_id,
                    "meta": json.dumps({**(meta or {}), "actor_id": actor_id}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_message_row(row)

    def create_run(
        self,
        *,
        conversation_id: str,
        client_message_id: str | None,
        status: str = "running",
        error: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """创建/更新一次 Agent run（按 client_message_id 幂等），返回规范化后的 run 行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_run (conversation_id, client_message_id, status, error, meta)
                    VALUES (CAST(:conversation_id AS UUID), :client_message_id, :status, :error, CAST(:meta AS JSONB))
                    ON CONFLICT (conversation_id, client_message_id) WHERE client_message_id IS NOT NULL DO UPDATE SET
                        status = EXCLUDED.status,
                        error = EXCLUDED.error,
                        meta = EXCLUDED.meta,
                        updated_at = NOW()
                    RETURNING id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "client_message_id": client_message_id,
                    "status": status,
                    "error": error,
                    "meta": json.dumps(meta or {}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_run_row(row)

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        error: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """更新一次 run 的状态/错误/元数据，返回更新后的 run 行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    UPDATE agent_run
                    SET status = :status,
                        error = :error,
                        meta = CAST(:meta AS JSONB),
                        updated_at = NOW()
                    WHERE run_id = CAST(:run_id AS UUID)
                    RETURNING id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    """
                ),
                {"run_id": run_id, "status": status, "error": error, "meta": json.dumps(meta or {})},
            ).mappings().first()
            session.commit()
        return self._normalize_run_row(row) if row else None

    def get_run(self, run_id: str) -> dict[str, object] | None:
        """按 run_id 取 run；不存在返回 None。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    FROM agent_run
                    WHERE run_id = CAST(:run_id AS UUID)
                    """
                ),
                {"run_id": run_id},
            ).mappings().first()
        return self._normalize_run_row(row) if row else None

    def get_run_by_client(self, conversation_id: str, client_message_id: str) -> dict[str, object] | None:
        """按 (conversation, client_message_id) 取最近一次 run（幂等重放用）。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    FROM agent_run
                    WHERE conversation_id = CAST(:conversation_id AS UUID) AND client_message_id = :client_message_id
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id, "client_message_id": client_message_id},
            ).mappings().first()
        return self._normalize_run_row(row) if row else None

    def get_assistant_message_for_run(self, run_id: str) -> dict[str, object] | None:
        """返回某次 run 的 assistant 消息（用于断连后重放落库结果，避免重算/重复）。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    FROM agent_message
                    WHERE run_id = CAST(:run_id AS UUID) AND role = 'assistant'
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"run_id": run_id},
            ).mappings().first()
        return self._normalize_message_row(row) if row else None

    def append_assistant_message(
        self,
        *,
        conversation_id: str,
        content: str,
        artifacts: dict[str, object] | None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        """追写一条 assistant 消息（可关联 run_id），返回规范化后的消息行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_message (conversation_id, role, content, artifacts, run_id, meta)
                    VALUES (CAST(:conversation_id AS UUID), 'assistant', :content, CAST(:artifacts AS JSONB), CAST(:run_id AS UUID), CAST(:meta AS JSONB))
                    RETURNING id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "content": content,
                    "artifacts": json.dumps(artifacts or {}),
                    "run_id": run_id,
                    "meta": json.dumps({"source": "agent"}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_message_row(row)

    def _normalize_conversation_row(self, row: dict[str, object] | None, *, message_count: int | None = None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "actor_id": row["actor_id"],
            "title": row["title"],
            "summary": row["summary"],
            "status": row["status"],
            "meta": dict(row["meta"] or {}),
            "message_count": message_count if message_count is not None else None,
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }

    def _normalize_message_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "conversation_id": str(row["conversation_id"]),
            "role": row["role"],
            "content": row["content"],
            "artifacts": dict(row["artifacts"] or {}) if row.get("artifacts") is not None else None,
            "client_message_id": row["client_message_id"],
            "run_id": str(row["run_id"]) if row.get("run_id") is not None else None,
            "meta": dict(row["meta"] or {}),
            "created_at": as_display_iso(row["created_at"]),
        }

    def _normalize_run_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "run_id": str(row["run_id"]),
            "conversation_id": str(row["conversation_id"]) if row.get("conversation_id") is not None else None,
            "client_message_id": row["client_message_id"],
            "status": row["status"],
            "error": row["error"],
            "meta": dict(row["meta"] or {}),
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }

