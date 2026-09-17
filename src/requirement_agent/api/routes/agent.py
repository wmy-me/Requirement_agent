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

from requirement_agent.application.decision_rules import next_action_for as decision_next_action
from requirement_agent.application.decision_rules import review_required as decision_review_required
from requirement_agent.api.dependencies import (
    analyze_agent,
    extract_agent,
    retrieval_service,
    risk_agent,
    run_tracking,
)
from requirement_agent.api.schemas import AgentRunRequest

router = APIRouter()


@router.post("/api/v1/agent/run")
async def run_agent_pipeline(payload: AgentRunRequest) -> dict[str, object]:
    """与 /chat/stream 共享的分析管线（非流式，供回放/兼容）。"""
    run_id = run_tracking.start_analysis(
        source_id=None,  # ad-hoc 分析，不绑来源 —— 它不是「某条来源的分析」
        meta={"source_type": payload.source_type, "entrypoint": "POST /agent/run"},
    )
    events: list[dict[str, Any]] = []

    def _step(node: str) -> None:
        events.append({"event_type": "node_started", "node": node, "payload": {}})

    def _done(node: str, summary: dict[str, Any]) -> None:
        events.append({"event_type": "node_completed", "node": node, "payload": summary})

    try:
        _step("extract")
        extracted = extract_agent.extract(
            payload.original_text,
            source_type=payload.source_type,
            requester_name=payload.requester_name,
        )
        extracted_payload = extracted.model_dump(mode="python")
        _done("extract", {"business_domain": extracted_payload.get("business_domain")})

        _step("retrieve")
        candidates = retrieval_service.search(extracted.summary or payload.original_text, limit=5)
        _done("retrieve", {"candidate_count": len(candidates)})

        _step("analyze")
        analysis = analyze_agent.analyze(extracted, candidates)
        analysis_payload = analysis.model_dump(mode="python")
        _done(
            "analyze",
            {
                "duplicate": analysis_payload.get("duplicate"),
                "related": analysis_payload.get("related"),
                "conflict": analysis_payload.get("conflict"),
            },
        )

        _step("risk")
        risk = risk_agent.assess(extracted)
        risk_payload = risk.model_dump(mode="python")
        _done("risk", {"quality_risk": risk_payload.get("quality_risk")})
    except Exception as exc:  # noqa: BLE001 —— 记下已发生的事件再上抛，不吞
        run_tracking.record_events(run_id, events)
        run_tracking.fail(run_id, error=exc, node=events[-1]["node"] if events else None)
        raise

    run_tracking.record_events(run_id, events)
    run_tracking.finish(run_id, current_node="review_decision")

    return {
        "status": "ok",
        "run_id": run_id,
        "steps": ["extract", "retrieve", "analyze", "risk", "review_decision"],
        "extracted": extracted_payload,
        "candidates": candidates,
        "analysis": analysis_payload,
        "risk": risk_payload,
        "review_required": decision_review_required(analysis_payload, risk_payload),
        "next_action": decision_next_action(analysis_payload, risk_payload),
    }
