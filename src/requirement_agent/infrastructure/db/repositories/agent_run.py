"""Agent 运行与运行事件的仓储（B2.1）。

与 `chat.py` 的关系：`chat.py` 管的是**对话 run 的读写**（它是那条线原有的仓储），
本模块管的是**运行追踪本身** —— 建 run、追加事件、收尾、工具调用明细。
两者读写的是同一张 `agent_run` 表，分工按「关注点」而不是按「表」切。

## 为什么 `append_events` 要先锁 run 行

序号是「每个 run 内从 1 开始、不重不漏」，而它由应用层分配（见 `migrations/021`
文件头③）。两个进程同时给同一个 run 追加事件时，各自 `SELECT MAX(sequence)`
都会读到同一个值，然后一个插入成功、另一个撞 `uq_run_event_seq` 唯一约束。

所以先 `SELECT 1 FROM agent_run WHERE run_id = ? FOR UPDATE` 拿行锁，
把「读最大值」和「插入」放进同一个事务。**锁的是 run 行而不是事件行**：
事件行可能一条都还没有，锁不住不存在的东西。

代价是同一个 run 的追加被串行化 —— 这正是想要的，而且一次运行只有一个写入方。
"""

from __future__ import annotations

import json

from sqlalchemy import text

from requirement_agent.common.snowflake import new_id
from requirement_agent.common.time import as_display_iso
from requirement_agent.domain.agent_run import redact_payload
from requirement_agent.infrastructure.db.session import SessionLocal

__all__ = ["AgentRunRepository"]

_RUN_COLUMNS = (
    "id, run_id, conversation_id, source_id, run_type, client_message_id, status, error, "
    "meta, stage, checkpoint, current_node, started_at, ended_at, created_at, updated_at"
)


class AgentRunRepository:
    """运行与其事件、工具调用的读写。"""

    # ── run ───────────────────────────────────────────────────────────────

    def create_run(
        self,
        *,
        run_type: str,
        source_id: int | None = None,
        conversation_id: str | None = None,
        client_message_id: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """建一个 run，状态 `queued`。

        **立刻把 `started_at` 置上**：`queued → running` 的间隔在本系统里约等于零
        （没有真正的排队），而「这次分析什么时候开始的」是排障时第一个要看的数。
        真正的耗时区间用 `started_at → ended_at` 表达，不依赖状态迁移时间。
        """
        with SessionLocal() as session:
            row = session.execute(
                text(
                    f"""
                    INSERT INTO agent_run
                        (id, run_id, conversation_id, source_id, run_type, client_message_id,
                         status, meta, started_at)
                    VALUES
                        (:id, gen_random_uuid(), CAST(:conversation_id AS UUID), :source_id,
                         :run_type, :client_message_id, 'queued', CAST(:meta AS JSONB), NOW())
                    RETURNING {_RUN_COLUMNS}
                    """
                ),
                {
                    "id": new_id(),
                    "conversation_id": conversation_id,
                    "source_id": source_id,
                    "run_type": run_type,
                    "client_message_id": client_message_id,
                    "meta": json.dumps(meta or {}),
                },
            ).mappings().one()
            session.commit()
        return dict(row)

    def get_run(self, run_id: str) -> dict[str, object] | None:
        with SessionLocal() as session:
            row = session.execute(
                text(f"SELECT {_RUN_COLUMNS} FROM agent_run WHERE run_id = CAST(:r AS UUID)"),
                {"r": run_id},
            ).mappings().first()
        return dict(row) if row else None

    def list_by_source(self, source_id: int, limit: int = 50) -> list[dict[str, object]]:
        """某条来源的全部运行记录（新→旧）。同一条来源可以被分析多次。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    f"""
                    SELECT {_RUN_COLUMNS} FROM agent_run
                    WHERE source_id = :source_id
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"source_id": source_id, "limit": max(1, min(limit, 200))},
            ).mappings().all()
        return [dict(row) for row in rows]

    def mark_running(self, run_id: str, *, current_node: str | None = None) -> None:
        """`queued → running`。节点名一并写入，让「跑到哪了」在第一个节点就能查到。"""
        self._update_run(run_id, status="running", current_node=current_node)

    def set_current_node(self, run_id: str, node: str) -> None:
        """更新「正在跑哪个节点」—— **失败时它指向出错的那个**，这是失败定位的实现。"""
        self._update_run(run_id, current_node=node)

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        current_node: str | None = None,
    ) -> None:
        """收尾：置终态并盖上 `ended_at`。

        ⚠️ **只有终态才盖 `ended_at`** —— 一个还在等的 run 有个「结束时间」是自相矛盾的。
        """
        self._update_run(
            run_id, status=status, error=error, current_node=current_node, ended_at=True
        )

    def _update_run(self, run_id: str, **fields: object) -> None:
        """只更新传了的字段（与 `ChatRepository.update_run` 同一语义）。

        `ended_at` 用布尔开关而不是传时间值：让**数据库时钟**决定结束时间，
        与 `started_at`（NOW()）同一把钟。应用层算的时间与库里的 `created_at`
        可能差几毫秒，排障时对不齐。
        """
        assignments: list[str] = []
        params: dict[str, object] = {"r": run_id}
        for key, value in fields.items():
            if value is None:
                continue
            if key == "ended_at" and value is True:
                assignments.append("ended_at = NOW()")
                continue
            assignments.append(f"{key} = :{key}")
            params[key] = value
        if not assignments:
            return
        with SessionLocal() as session:
            session.execute(
                text(
                    f"UPDATE agent_run SET {', '.join(assignments)} "
                    "WHERE run_id = CAST(:r AS UUID)"
                ),
                params,
            )
            session.commit()

    # ── 事件 ──────────────────────────────────────────────────────────────

    def append_events(
        self, run_id: str, events: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """批量追加事件，序号在事务内分配。返回落库后的行。

        入参是**普通 dict**（`{"event_type", "node", "payload"}`），与 state 的
        `run_events` 通道、`tool_calls` 通道同一种形状 —— 不为它再造一个类型。

        ⚠️ **脱敏在这里做**，不在生产端。事件也可能从别的路径进来（对话管线手工构造的），
        在每个生产端各脱一次迟早漏一个；写入边界是唯一的必经之路。

        **空列表直接返回**：`run_analysis` 在异常路径上可能攒不到任何事件，
        那种情况下不该开一个什么都不做的事务。
        """
        if not events:
            return []
        with SessionLocal() as session:
            # 锁 run 行 —— 见模块 docstring：不锁就会有两个写入方各自读到同一个 MAX。
            session.execute(
                text("SELECT 1 FROM agent_run WHERE run_id = CAST(:r AS UUID) FOR UPDATE"),
                {"r": run_id},
            )
            start = session.execute(
                text(
                    "SELECT COALESCE(MAX(sequence), 0) FROM agent_run_event "
                    "WHERE run_id = CAST(:r AS UUID)"
                ),
                {"r": run_id},
            ).scalar() or 0

            rows: list[dict[str, object]] = []
            for offset, event in enumerate(events, start=1):
                saved = session.execute(
                    text(
                        """
                        INSERT INTO agent_run_event
                            (id, run_id, sequence, event_type, node, payload)
                        VALUES
                            (:id, CAST(:run_id AS UUID), :sequence, :event_type, :node,
                             CAST(:payload AS JSONB))
                        RETURNING id, run_id, sequence, event_type, node, payload, created_at
                        """
                    ),
                    {
                        "id": new_id(),
                        "run_id": run_id,
                        "sequence": int(start) + offset,
                        "event_type": str(event.get("event_type") or ""),
                        "node": event.get("node"),
                        "payload": json.dumps(
                            redact_payload(event.get("payload")), ensure_ascii=False
                        ),
                    },
                ).mappings().one()
                rows.append(dict(saved))
            session.commit()
        return rows

    def list_events(
        self, run_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[dict[str, object]]:
        """按 `sequence > after_seq` 升序取事件 —— **断线回放的唯一入口**。

        `after_seq` 是**排他的**（只返回严格大于它的），这样客户端记住「最后收到
        的 seq」原样回传即可，不需要自己 +1。
        """
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, run_id, sequence, event_type, node, payload, created_at
                    FROM agent_run_event
                    WHERE run_id = CAST(:r AS UUID) AND sequence > :after_seq
                    ORDER BY sequence ASC
                    LIMIT :limit
                    """
                ),
                {"r": run_id, "after_seq": int(after_seq), "limit": max(1, min(limit, 2000))},
            ).mappings().all()
        return [_normalize_event(dict(row)) for row in rows]

    # ── 工具调用 ──────────────────────────────────────────────────────────

    def record_tool_invocations(
        self, run_id: str | None, records: list[dict[str, object]]
    ) -> int:
        """把 `tools/invoker.tool_call_record` 的形状落成行，返回写入条数。

        **列的对应关系是刻意的、有测试钉着**（`test_tool_invocation_mirrors_tool_call_record`）：
        留痕的形状是 B3 定下的，这里不另发明一套，否则就是两份要同步的事实源。
        """
        if not records:
            return 0
        written = 0
        with SessionLocal() as session:
            for record in records:
                session.execute(
                    text(
                        """
                        INSERT INTO tool_invocation
                            (id, run_id, tool_name, arguments_summary, result_summary,
                             status, elapsed_ms, error_message)
                        VALUES
                            (:id, CAST(:run_id AS UUID), :tool_name,
                             CAST(:arguments_summary AS JSONB), CAST(:result_summary AS JSONB),
                             :status, :elapsed_ms, :error_message)
                        """
                    ),
                    {
                        "id": new_id(),
                        "run_id": run_id,
                        "tool_name": str(record.get("tool") or ""),
                        "arguments_summary": json.dumps(record.get("params") or {}, ensure_ascii=False),
                        "result_summary": json.dumps(
                            {
                                "count": record.get("count"),
                                "sample": record.get("sample"),
                            },
                            ensure_ascii=False,
                        ),
                        "status": str(record.get("status") or "unknown"),
                        "elapsed_ms": int(record.get("duration_ms") or 0),
                        "error_message": record.get("message"),
                    },
                )
                written += 1
            session.commit()
        return written

    def list_tool_invocations(self, run_id: str, limit: int = 200) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, run_id, tool_name, arguments_summary, result_summary,
                           status, elapsed_ms, error_message, created_at
                    FROM tool_invocation
                    WHERE run_id = CAST(:r AS UUID)
                    ORDER BY created_at ASC
                    LIMIT :limit
                    """
                ),
                {"r": run_id, "limit": max(1, min(limit, 500))},
            ).mappings().all()
        return [dict(row) for row in rows]


def _normalize_event(row: dict[str, object]) -> dict[str, object]:
    """事件行 → 对外形状。

    时间走 `as_display_iso`（与全站一致），`id` 字符串化（雪花 id 超过 JS 安全整数，
    见 `api-contract.md` §1.1）。`run_id` 本来就是 UUID 字符串。
    """
    return {
        "id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "sequence": int(row["sequence"]),  # type: ignore[arg-type]
        "event_type": str(row["event_type"]),
        "node": row.get("node"),
        "payload": row.get("payload") or {},
        "created_at": as_display_iso(row.get("created_at")),  # type: ignore[arg-type]
    }
