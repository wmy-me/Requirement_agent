"""Agent 分析管线 HTTP 路由（子批次 3.3.2 迁移）。

来源：`src/interfaces/http/agent_chat.py` 的 `POST /api/v1/agent/run`（`run_agent_pipeline`）。
纯分析、**不写数据库**、不触发 outbox：调用
`extract → retrieve → analyze → risk` 管线并返回结构化结果。

该函数同时被同模块的 `chat_with_agent` 作为普通函数复用；旧文件通过 `include_router`
在原位置复用本 router，并以兼容转发保留旧函数名（同一对象）。

注意：本 router **不设 tags**，由父 router（`tags=["agent"]`）在 include 时补齐。
"""

from __future__ import annotations

from fastapi import APIRouter

from src.requirement_agent.application.decision_rules import next_action_for as decision_next_action
from src.requirement_agent.application.decision_rules import review_required as decision_review_required
from src.requirement_agent.api.dependencies import analyze_agent, extract_agent, retrieval_service, risk_agent
from src.requirement_agent.api.schemas import AgentRunRequest

router = APIRouter()


@router.post("/api/v1/agent/run")
async def run_agent_pipeline(payload: AgentRunRequest) -> dict[str, object]:
    """与 /chat/stream 共享的分析管线（非流式，供回放/兼容）。"""
    extracted = extract_agent.extract(
        payload.original_text,
        source_type=payload.source_type,
        requester_name=payload.requester_name,
    )
    candidates = retrieval_service.search(extracted.summary or payload.original_text, limit=5)
    analysis = analyze_agent.analyze(extracted, candidates)
    risk = risk_agent.assess(extracted)
    risk_payload = risk.model_dump(mode="python")
    analysis_payload = analysis.model_dump(mode="python")
    return {
        "status": "ok",
        "steps": ["extract", "retrieve", "analyze", "risk", "review_decision"],
        "extracted": extracted.model_dump(mode="python"),
        "candidates": candidates,
        "analysis": analysis_payload,
        "risk": risk_payload,
        "review_required": decision_review_required(analysis_payload, risk_payload),
        "next_action": decision_next_action(analysis_payload, risk_payload),
    }
