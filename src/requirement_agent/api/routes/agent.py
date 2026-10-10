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

from typing import Any

from fastapi import APIRouter

from requirement_agent.application.agent_execution_service import AgentExecutionService
from requirement_agent.api.schemas import AgentRunRequest
from requirement_agent.workflows.graphs import run_analysis
from requirement_agent.infrastructure.llm.invocation import bind_run_id

router = APIRouter()


@router.post("/api/v1/agent/run")
async def run_agent_pipeline(payload: AgentRunRequest) -> dict[str, object]:
    """与 /chat/stream 共享的分析管线（非流式，供回放/兼容）。"""
    execution = AgentExecutionService()
    created = execution.start(task_type="analysis", source_id=None, meta={"source_type": payload.source_type, "entrypoint": "POST /agent/run"})
    run_id = str(created["run_id"])
    if execution.claim(run_id) is None:  # defensive: a just-created queued run must be claimable
        raise RuntimeError("unable to claim newly created agent run")
    execution.repo.append_events(run_id, [{"event_type": "run_started", "node": "execution", "payload": {"task_type": "analysis"}}])
    try:
        with bind_run_id(run_id):
            result = run_analysis(
                source_text=payload.original_text, source_type=payload.source_type,
                requester_name=payload.requester_name,
            )
    except Exception as exc:  # noqa: BLE001 —— 记下已发生的事件再上抛，不吞
        execution.repo.append_events(run_id, [{"event_type": "run_failed", "node": getattr(exc, "node", None), "payload": {"error": str(exc)}}])
        execution.fail(run_id, exc, current_node=getattr(exc, "node", None))
        raise

    execution.repo.append_events(run_id, list(result.get("run_events") or []) + [{"event_type": "run_completed", "node": "review_decision", "payload": {}}])
    execution.repo.record_tool_invocations(run_id, list(result.get("tool_calls") or []))
    execution.checkpoint(run_id, {"version": 2, "remaining_model_turns": None, "remaining_tool_calls": None, "result": result})
    execution.complete(run_id, current_node="review_decision")

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
