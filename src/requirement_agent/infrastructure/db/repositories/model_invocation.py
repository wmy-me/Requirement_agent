"""模型调用记录的仓储（B3.1）。

消费 `infrastructure/llm/invocation.record_invocation` 交过来的 dict，
写成 `model_invocation` 行。装配在 `api/dependencies.py`（组合根）。

⚠️ **写入必须是「尽力而为」的。** 它是观测数据，一条记录写不进去不该让
「回答用户」失败。`record()` 因此不抛 —— 但也不静默：失败会留一条 warning。
（真正兜异常的是调用它的 `record_invocation`；这里再兜一层是因为仓储可能被
别处直接调用。）
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from requirement_agent.common.snowflake import new_id
from requirement_agent.common.time import as_display_iso
from requirement_agent.infrastructure.db.session import SessionLocal

logger = logging.getLogger(__name__)

__all__ = ["ModelInvocationRepository"]

_COLUMNS = (
    "id, run_id, task_type, provider, model, prompt_version, schema_version, "
    "input_tokens, output_tokens, latency_ms, request_id, status, error_code, "
    "fallback_from, fallback_level, fallback_used, embedding_dimension, "
    "embedding_version, created_at"
)


class ModelInvocationRepository:
    """模型调用记录的读写。"""

    def record(self, payload: dict[str, object]) -> None:
        """写一条调用记录。**不抛异常。**"""
        try:
            with SessionLocal() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO model_invocation
                            (id, run_id, task_type, provider, model, prompt_version,
                             schema_version, input_tokens, output_tokens, latency_ms,
                             request_id, status, error_code, fallback_from,
                             fallback_level, fallback_used, embedding_dimension,
                             embedding_version)
                        VALUES
                            (:id, CAST(:run_id AS UUID), :task_type, :provider, :model,
                             :prompt_version, :schema_version, :input_tokens, :output_tokens,
                             :latency_ms, :request_id, :status, :error_code, :fallback_from,
                             :fallback_level, :fallback_used, :embedding_dimension,
                             :embedding_version)
                        """
                    ),
                    {
                        "id": new_id(),
                        "run_id": payload.get("run_id"),
                        # 这三列 NOT NULL —— 缺了就写不下去，但也不该因此抛
                        "task_type": str(payload.get("task_type") or "unknown"),
                        "provider": str(payload.get("provider") or "unknown"),
                        "model": str(payload.get("model") or "unknown"),
                        "prompt_version": payload.get("prompt_version"),
                        "schema_version": payload.get("schema_version"),
                        "input_tokens": payload.get("input_tokens"),
                        "output_tokens": payload.get("output_tokens"),
                        "latency_ms": payload.get("latency_ms"),
                        "request_id": payload.get("request_id"),
                        "status": str(payload.get("status") or "unknown"),
                        "error_code": payload.get("error_code"),
                        "fallback_from": payload.get("fallback_from"),
                        "fallback_level": int(payload.get("fallback_level") or 0),
                        "fallback_used": bool(payload.get("fallback_used")),
                        "embedding_dimension": payload.get("embedding_dimension"),
                        "embedding_version": payload.get("embedding_version"),
                    },
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 —— 观测数据，不拖垮调用方
            logger.warning(
                "event=model_invocation_insert_failed task_type=%s model=%s error=%s: %s",
                payload.get("task_type"),
                payload.get("model"),
                type(exc).__name__,
                exc,
            )

    def list_by_run(
        self, run_id: str, limit: int = 200, task_type: str | None = None
    ) -> list[dict[str, object]]:
        """某次运行的模型调用，**按发生顺序**（`ASC`）。

        与 `list_recent` 的 `DESC` 是**刻意相反**的，因为用途不同：
        · 全局清单（`list_recent`）要的是「最近都调了什么」→ 倒序；
        · Run 详情页要的是「这次运行依次调了什么」→ 正序，与节点时间线对齐。

        `task_type` 与 `run_id` **可组合**，不是二选一 —— 组合时若静默丢掉一个，
        调用方会拿到一份看起来合理、实则范围不对的结果。
        """
        where = ["run_id = CAST(:r AS UUID)"]
        params: dict[str, object] = {"r": run_id, "limit": max(1, min(limit, 500))}
        if task_type:
            where.append("task_type = :task_type")
            params["task_type"] = task_type

        with SessionLocal() as session:
            rows = session.execute(
                text(
                    f"""
                    SELECT {_COLUMNS} FROM model_invocation
                    WHERE {' AND '.join(where)}
                    ORDER BY created_at ASC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
        return [_normalize(dict(row)) for row in rows]

    def list_recent(self, task_type: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        """最近若干次调用，可按 task_type 过滤。

        「我明明把 analyze 换成了 X，怎么还在用 Y」——这个查询是第一手证据。
        """
        with SessionLocal() as session:
            if task_type:
                rows = session.execute(
                    text(
                        f"""
                        SELECT {_COLUMNS} FROM model_invocation
                        WHERE task_type = :task_type
                        ORDER BY created_at DESC LIMIT :limit
                        """
                    ),
                    {"task_type": task_type, "limit": max(1, min(limit, 500))},
                ).mappings().all()
            else:
                rows = session.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM model_invocation "
                        "ORDER BY created_at DESC LIMIT :limit"
                    ),
                    {"limit": max(1, min(limit, 500))},
                ).mappings().all()
        return [_normalize(dict(row)) for row in rows]


def _normalize(row: dict[str, object]) -> dict[str, object]:
    """对外形状：id 字符串化（雪花 id 超过 JS 安全整数），时间走展示时区。"""
    row["id"] = str(row["id"])
    row["run_id"] = str(row["run_id"]) if row.get("run_id") else None
    row["created_at"] = as_display_iso(row.get("created_at"))  # type: ignore[arg-type]
    return row
