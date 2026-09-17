"""会话 Repository：`ChatRepository` 多轮对话/消息/Agent run 持久化。

含 `client_message_id` 幂等（断线重发回放已完成结果）与 run 状态机。
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from requirement_agent.common.snowflake import new_id
from requirement_agent.common.time import as_display_iso
from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.db.session import SessionLocal

# 并发隔离用的部分唯一索引（migrations/012）：(conversation_id) WHERE status IN ('running','paused')
# ⚠️ 谓词里的 'paused' 是**永不可能出现**的值 —— 代码里已无写入方（pause 端点已删）、
# DB 的 CHECK 也从不允许它。索引实际只覆盖 running；留着它是因为改索引谓词要动
# 已有迁移建的对象，而收益只是好看。
_ACTIVE_RUN_CONSTRAINT = "uq_run_active_per_conversation"


class ConversationBusyError(RuntimeError):
    """该对话已有正在进行的思考 —— 同一对话一次只允许一个活跃运行。

    约束落在数据库而不是进程内锁：前端 `state.streaming` 是页面级的，多标签页、
    多客户端、直连 curl 都能绕过，且进程内锁在多 worker 下形同虚设。
    **跨对话不受影响** —— 唯一索引的键是 conversation_id，开新对话即可并行。
    """

    def __init__(self, conversation_id: str) -> None:
        super().__init__(f"conversation {conversation_id} already has an active run")
        self.conversation_id = conversation_id


def is_active_run_conflict(exc: IntegrityError) -> bool:
    """判断是否撞上了「同对话只允许一个活跃 run」的唯一索引（而非其它完整性错误）。"""
    return _ACTIVE_RUN_CONSTRAINT in str(exc)


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
                    INSERT INTO agent_message (id, conversation_id, role, content, client_message_id, meta)
                    VALUES (:id, CAST(:conversation_id AS UUID), 'user', :content, :client_message_id, CAST(:meta AS JSONB))
                    ON CONFLICT (conversation_id, client_message_id) WHERE client_message_id IS NOT NULL DO UPDATE SET
                        content = EXCLUDED.content,
                        meta = EXCLUDED.meta
                    RETURNING id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    """
                ),
                {
                    "id": new_id(),
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
        """创建/更新一次 Agent run（按 client_message_id 幂等），返回规范化后的 run 行。

        并发约束（`migrations/012`）：同一对话最多一个活跃运行（running）。
        撞上该约束时抛 `ConversationBusyError` —— **除非**挡路的那条是崩溃留下的僵尸
        （静默超过 `chat_run_stale_timeout_seconds`）：那种情况先把它判为 failed 再重试一次。

        没有这段自愈，一次进程中断留下的 running 行会把该对话**永久**堵死：用户只看到 409，
        而没有任何地方会去清理它。库里就躺过一条静默 3 天的 running run。
        """
        values = {
            "id": new_id(),
            "conversation_id": conversation_id,
            "client_message_id": client_message_id,
            "status": status,
            "error": error,
            "meta": json.dumps(meta or {}),
        }
        try:
            return self._insert_run(values)
        except IntegrityError as exc:
            if not is_active_run_conflict(exc):
                raise
            if not self.expire_stale_runs(conversation_id):
                raise ConversationBusyError(conversation_id) from exc
            # 僵尸已判死，重试一次；再冲突说明确实有人在跑
            try:
                return self._insert_run(values)
            except IntegrityError as retry_exc:
                if is_active_run_conflict(retry_exc):
                    raise ConversationBusyError(conversation_id) from retry_exc
                raise

    def _insert_run(self, values: dict[str, object]) -> dict[str, object]:
        """执行 run 的 upsert（幂等键仍是 client_message_id）。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_run (id, conversation_id, client_message_id, status, error, meta)
                    VALUES (:id, CAST(:conversation_id AS UUID), :client_message_id, :status, :error, CAST(:meta AS JSONB))
                    ON CONFLICT (conversation_id, client_message_id) WHERE client_message_id IS NOT NULL DO UPDATE SET
                        status = EXCLUDED.status,
                        error = EXCLUDED.error,
                        meta = EXCLUDED.meta,
                        updated_at = NOW()
                    RETURNING id, run_id, conversation_id, client_message_id, status, error, meta, stage, checkpoint, created_at, updated_at
                    """
                ),
                values,
            ).mappings().one()
            session.commit()
        return self._normalize_run_row(row)

    def get_active_run(self, conversation_id: str) -> dict[str, object] | None:
        """取该对话当前的活跃运行（running），供「本对话在忙」的 409 提示用。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, stage, checkpoint, created_at, updated_at
                    FROM agent_run
                    WHERE conversation_id = CAST(:conversation_id AS UUID)
                      AND status = 'running'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id},
            ).mappings().first()
        return self._normalize_run_row(row) if row else None

    def expire_stale_runs(self, conversation_id: str, *, older_than_seconds: int | None = None) -> int:
        """把该对话里静默过久的活跃 run 判为 failed，返回判死的条数。

        阈值默认 `settings.chat_run_stale_timeout_seconds`（远大于任何正常分析耗时）。
        被进程中断的运行不会自己收尾，留着就会一直占着并发位。
        """
        threshold = (
            settings.chat_run_stale_timeout_seconds if older_than_seconds is None else older_than_seconds
        )
        with SessionLocal() as session:
            result = session.execute(
                text(
                    """
                    UPDATE agent_run
                    SET status = 'failed',
                        error = 'stale: 运行超时未收尾（进程中断或客户端长时间无响应）',
                        updated_at = NOW()
                    WHERE conversation_id = CAST(:conversation_id AS UUID)
                      AND status = 'running'
                      AND updated_at < NOW() - make_interval(secs => :secs)
                    """
                ),
                {"conversation_id": conversation_id, "secs": threshold},
            )
            session.commit()
            return result.rowcount

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        error: str | None = None,
        meta: dict[str, object] | None = None,
        stage: str | None = None,
        checkpoint: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """更新一次 run 的状态/错误/元数据/阶段/断点，返回更新后的 run 行。

        只有**传了的字段**才更新（其余保持原值）——尤其 checkpoint 是逐阶段累积的，
        不能每次整体覆盖。`stage` 用「已完成态」命名（extracted/retrieved/analyzed/
        assessed/done），续跑时据此跳过已完成阶段。
        """
        sets = ["status = :status", "error = :error", "meta = CAST(:meta AS JSONB)", "updated_at = NOW()"]
        values: dict[str, object] = {
            "run_id": run_id,
            "status": status,
            "error": error,
            "meta": json.dumps(meta or {}),
        }
        if stage is not None:
            sets.append("stage = :stage")
            values["stage"] = stage
        if checkpoint is not None:
            sets.append("checkpoint = CAST(:checkpoint AS JSONB)")
            values["checkpoint"] = json.dumps(checkpoint)
        with SessionLocal() as session:
            row = session.execute(
                text(
                    f"""
                    UPDATE agent_run
                    SET {", ".join(sets)}
                    WHERE run_id = CAST(:run_id AS UUID)
                    RETURNING id, run_id, conversation_id, client_message_id, status, error, meta,
                              stage, checkpoint, created_at, updated_at
                    """
                ),
                values,
            ).mappings().first()
            session.commit()
        return self._normalize_run_row(row) if row else None

    def get_run(self, run_id: str) -> dict[str, object] | None:
        """按 run_id 取 run；不存在返回 None。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, stage, checkpoint, created_at, updated_at
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
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, stage, checkpoint, created_at, updated_at
                    FROM agent_run
                    WHERE conversation_id = CAST(:conversation_id AS UUID) AND client_message_id = :client_message_id
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id, "client_message_id": client_message_id},
            ).mappings().first()
        return self._normalize_run_row(row) if row else None

    def find_resumable_run(self, conversation_id: str) -> dict[str, object] | None:
        """该会话最近一个「未完成但已有断点」的 run，供续跑入口用。

        只认 cancelled/failed 且 checkpoint 非空 —— completed（跑完了没必要续）、
        running（正在跑，不该再续一次）、checkpoint 为空（没算出任何东西，续了也是从头）都不算。
        """
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, stage, checkpoint, created_at, updated_at
                    FROM agent_run
                    WHERE conversation_id = CAST(:conversation_id AS UUID)
                      AND status IN ('cancelled', 'failed')
                      AND checkpoint::text <> '{}'
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id},
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
                    INSERT INTO agent_message (id, conversation_id, role, content, artifacts, run_id, meta)
                    VALUES (:id, CAST(:conversation_id AS UUID), 'assistant', :content, CAST(:artifacts AS JSONB), CAST(:run_id AS UUID), CAST(:meta AS JSONB))
                    RETURNING id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    """
                ),
                {
                    "id": new_id(),
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
            "stage": str(row.get("stage") or "queued"),
            "checkpoint": dict(row.get("checkpoint") or {}),
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }

