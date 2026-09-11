"""长期记忆 Repository：`MemoryRepository` actor 隔离的跨会话记忆。

含 status/active 治理（superseded/deleted）、关键字与向量召回。
"""

from __future__ import annotations

import json

from sqlalchemy import text

from src.common.snowflake import new_id
from src.common.time import as_display_iso
from src.infrastructure.db.session import SessionLocal


class MemoryRepository:
    """Long-term memory persistence for actor-scoped, recallable memory notes."""

    def insert_memory(
        self,
        *,
        actor_id: str,
        kind: str,
        content: str,
        source_conversation_id: str | None = None,
        source_message_id: int | None = None,
        ref_requirement_key: str | None = None,
        importance: int = 1,
        meta: dict[str, object] | None = None,
        embedding: list[float] | None = None,
    ) -> dict[str, object]:
        """插入一条 actor 维度的长期记忆（可带 embedding 供向量召回），返回规范化记忆行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO memory_note (id, actor_id, kind, content, source_conversation_id, source_message_id, ref_requirement_key, importance, meta, embedding)
                    VALUES (:id, :actor_id, :kind, :content, CAST(:source_conversation_id AS UUID), :source_message_id, :ref_requirement_key, :importance, CAST(:meta AS JSONB), CAST(:embedding AS vector))
                    RETURNING id, actor_id, kind, status, active, content, source_conversation_id, source_message_id, ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    """
                ),
                {
                    "id": new_id(),
                    "actor_id": actor_id,
                    "kind": kind,
                    "content": content,
                    "source_conversation_id": source_conversation_id,
                    "source_message_id": source_message_id,
                    "ref_requirement_key": ref_requirement_key,
                    "importance": importance,
                    "meta": json.dumps(meta or {}),
                    "embedding": ("[" + ",".join(str(float(x)) for x in embedding) + "]") if embedding is not None else None,
                },
            ).mappings().one()
            session.commit()
        return self._normalize_memory_row(row)

    def list_memories(self, actor_id: str = "api-user", limit: int = 20) -> list[dict[str, object]]:
        """列出该 actor 未删除的记忆（importance 优先），返回规范化记忆列表。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, actor_id, kind, status, active, content, source_conversation_id, source_message_id,
                           ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    FROM memory_note
                    WHERE actor_id = :actor_id AND status != 'deleted'
                    ORDER BY importance DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "limit": limit},
            ).mappings().all()
        return [self._normalize_memory_row(row) for row in rows]

    def recall(self, actor_id: str, query: str, limit: int = 4) -> list[dict[str, object]]:
        """按文本关键词（ILIKE）召回该 actor 的 active 记忆，作为无向量场景的兜底。"""
        q = (query or "").strip()
        if not q:
            return []
        like = f"%{q}%"
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, actor_id, kind, status, active, content, source_conversation_id, source_message_id,
                           ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    FROM memory_note
                    WHERE actor_id = :actor_id AND status = 'active' AND content ILIKE :query
                    ORDER BY importance DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "query": like, "limit": limit},
            ).mappings().all()
        return [self._normalize_memory_row(row) for row in rows]

    def update_status(self, memory_id: int, status: str) -> dict[str, object] | None:
        """更新某条记忆的状态并同步 active 标志，返回更新后的记忆行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    UPDATE memory_note
                    SET status = :status, active = (:status = 'active'), updated_at = NOW()
                    WHERE id = :memory_id
                    RETURNING id, actor_id, kind, status, active, content, source_conversation_id, source_message_id, ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    """
                ),
                {"memory_id": memory_id, "status": status},
            ).mappings().first()
            session.commit()
        return self._normalize_memory_row(row) if row else None

    def supersede(self, old_id: int, new_id: int) -> dict[str, object] | None:
        """把一条 active 记忆标记为已被新记忆替代（保留审计）。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    UPDATE memory_note
                    SET status = 'superseded',
                        active = FALSE,
                        superseded_by = :new_id,
                        updated_at = NOW()
                    WHERE id = :old_id AND status = 'active'
                    RETURNING id, actor_id, kind, status, active, content, source_conversation_id, source_message_id, ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    """
                ),
                {"old_id": old_id, "new_id": new_id},
            ).mappings().first()
            session.commit()
        return self._normalize_memory_row(row) if row else None

    def recall_vector(self, actor_id: str, query_vector: list[float], limit: int = 4) -> list[dict[str, object]]:
        """按向量余弦相似度召回该 actor 的有效记忆（embedding 为 NULL 的行自动跳过）。"""
        vec = "[" + ",".join(str(float(x)) for x in query_vector) + "]"
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, actor_id, kind, status, active, content, source_conversation_id, source_message_id,
                           ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    FROM memory_note
                    WHERE actor_id = :actor_id AND status = 'active'
                    ORDER BY embedding <=> CAST(:query_vector AS vector)
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "query_vector": vec, "limit": limit},
            ).mappings().all()
        return [self._normalize_memory_row(row) for row in rows]

    def _normalize_memory_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "actor_id": row["actor_id"],
            "kind": row["kind"],
            "status": row["status"],
            "active": bool(row["active"]),
            "content": row["content"],
            "source_conversation_id": str(row["source_conversation_id"]) if row.get("source_conversation_id") is not None else None,
            "source_message_id": row["source_message_id"],
            "ref_requirement_key": row["ref_requirement_key"],
            "superseded_by": row["superseded_by"],
            "importance": int(row["importance"]),
            "meta": dict(row["meta"] or {}),
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }