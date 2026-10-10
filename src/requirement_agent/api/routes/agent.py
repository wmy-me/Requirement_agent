"""Agent 分析管线 HTTP 路由（子批次 3.3.2 迁移）。

来源：`src/interfaces/http/agent_chat.py` 的 `POST /api/v1/agent/run`（`run_agent_pipeline`）。
调用 `extract → retrieve → analyze → risk` 管线并返回结构化结果，不触发 outbox。

该函数同时被同模块的 `chat_with_agent` 作为普通函数复用；旧文件通过 `include_router`
在原位置复用本 router，并以兼容转发保留旧函数名（同一对象）。

注意：本 router **不设 tags**，由父 router（`tags=["agent"]`）在 include 时补齐。

> ⚠️ **B2.1 起这个端点会写库**（原先的注释写着「纯分析、不写数据库」）。
> 写的是运行追踪：一个 `agent_run` 行 + 一条事件流。改这个性质是因为它的定位变了 ——
> 在有了统一运行追踪之后，它会是「不被追踪的分析」这条旁路，而**能追踪**正是
> B2.1 要建立的性质。不做的话，「上周这次分析跑的什么」在它这里永远查不到。
>
> ⚠️ 它**不经过 LangGraph 分析图**（自己串了四步），所以事件在这里手工产出，
> 不走 `event_nodes` 的适配器。两条分析路径都要留下事件 ——
> 只给其中一条装监控，等于没有统一追踪。
"""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any

from fastapi import APIRouter, HTTPException, status

from requirement_agent.application.agent_execution_service import AgentExecutionService
from requirement_agent.api.schemas import AgentRunRequest
from requirement_agent.workflows.graphs import run_analysis
from requirement_agent.infrastructure.llm.invocation import bind_run_id
from requirement_agent.config.settings import settings
from requirement_agent.application.execution_control import AnalysisBudgetExhausted, ExecutionStopped

router = APIRouter()

# This endpoint still returns its historical synchronous result, but its CPU/
# blocking-model work must not occupy the async server loop.  Keep the pool
# bounded; full detached execution/SSE is intentionally a later migration.
_ANALYSIS_EXECUTOR = ThreadPoolExecutor(
    max_workers=settings.agent_analysis_executor_workers, thread_name_prefix="agent-analysis"
)


def _run_analysis_in_context(
    *, run_id: str, source_text: str, source_type: str, requester_name: str | None,
    guard: Any, deadline_monotonic: float,
) -> dict[str, Any]:
    with bind_run_id(run_id):
        return run_analysis(
            source_text=source_text, source_type=source_type, requester_name=requester_name,
            execution_guard=guard, deadline_monotonic=deadline_monotonic,
        )


@router.post("/api/v1/agent/run")
async def run_agent_pipeline(payload: AgentRunRequest) -> dict[str, object]:
    """与 /chat/stream 共享的分析管线（非流式，供回放/兼容）。"""
    execution = AgentExecutionService()
    now_wall = time.time()
    budget_seconds = settings.agent_analysis_budget_seconds
    created = execution.start(task_type="analysis", source_id=None, meta={
        "source_type": payload.source_type, "entrypoint": "POST /agent/run",
        "budget_seconds": budget_seconds, "deadline_at_epoch": now_wall + budget_seconds,
    })
    run_id = str(created["run_id"])
    claimed = execution.claim(run_id)
    if claimed is None:  # defensive: a just-created queued run must be claimable
        raise RuntimeError("unable to claim newly created agent run")
    token = str(claimed["execution_token"])
    deadline_at_epoch = float((claimed.get("meta") or {}).get("deadline_at_epoch") or (now_wall + budget_seconds))
    remaining_budget = max(0.0, deadline_at_epoch - time.time())
    if not execution.checkpoint(run_id, token, {
        "version": 3, "deadline_at_epoch": deadline_at_epoch,
        "remaining_budget_seconds": remaining_budget, "execution_position": "starting",
    }, events=[{"event_type": "run_started", "node": "execution", "payload": {"task_type": "analysis", "deadline_at_epoch": deadline_at_epoch}}]):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="agent run lost execution ownership")
    if remaining_budget <= 0:
        execution.incomplete(run_id, token, "analysis budget exhausted before start", current_node="execution")
        return {"status": "incomplete", "run_id": run_id, "reason": "deadline_exceeded"}

    heartbeat = execution.heartbeat_loop(run_id, token)
    deadline_monotonic = time.monotonic() + remaining_budget

    def guard() -> bool:
        return not heartbeat.lost and execution.owns(run_id, token)
    try:
        # copy_context retains request/run context for the bounded worker.
        context = contextvars.copy_context()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _ANALYSIS_EXECUTOR,
            lambda: context.run(
                _run_analysis_in_context,
                run_id=run_id, source_text=payload.original_text, source_type=payload.source_type,
                requester_name=payload.requester_name, guard=guard, deadline_monotonic=deadline_monotonic,
            ),
        )
    except AnalysisBudgetExhausted as exc:
        execution.incomplete(run_id, token, str(exc), current_node="analysis")
        return {"status": "incomplete", "run_id": run_id, "reason": "deadline_exceeded"}
    except ExecutionStopped as exc:
        # Pause/cancel/reclaim wins.  Never write a stale terminal event.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 —— 记下已发生的事件再上抛，不吞
        execution.fail(run_id, token, exc, current_node=getattr(exc, "node", None))
        raise
    finally:
        heartbeat.stop()

    if not execution.events(run_id, token, list(result.get("run_events") or [])):
        raise RuntimeError("agent run lost execution ownership")
    if not execution.tool_calls(run_id, token, list(result.get("tool_calls") or [])):
        raise RuntimeError("agent run lost execution ownership")
    remaining_budget = max(0.0, deadline_at_epoch - time.time())
    if remaining_budget <= 0:
        if not execution.incomplete(run_id, token, "agent analysis budget exhausted", current_node="review_decision"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="agent run lost execution ownership")
        return {"status": "incomplete", "run_id": run_id, "reason": "deadline_exceeded"}
    if not execution.checkpoint(run_id, token, {"version": 3, "deadline_at_epoch": deadline_at_epoch, "remaining_budget_seconds": remaining_budget, "execution_position": "review_decision", "result": result}):
        raise RuntimeError("agent run lost execution ownership")
    if not execution.complete(run_id, token, current_node="review_decision"):
        raise RuntimeError("agent run lost execution ownership")

    return {
        "status": "ok",
        "run_id": run_id,
        "steps": list(result.get("completed_steps") or []),
        "extracted": result.get("extracted") or {},
        "candidates": result.get("candidates") or [],
        "analysis": result.get("analysis") or {},
        "risk": result.get("risk") or {},
        "review_required": result.get("decision") == "manual_review",
        "next_action": result.get("next_action"),
        "routing": {key: result.get(key) for key in ("intent", "route_history", "route_reason", "missing_information", "error_state")},
    }
