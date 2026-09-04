"""需求工作流图中的节点函数。"""

from __future__ import annotations

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent, ExtractedRequirement
from src.agents.retrieval_agent import RetrievalAgent
from src.agents.risk_agent import RiskAgent
from src.graph.state import RequirementGraphState


def extract_requirement(state: RequirementGraphState) -> RequirementGraphState:
    agent = ExtractAgent()
    extracted = agent.extract(
        state.source_text,
        source_type=state.source_type,
        requester_name=state.requester_name,
    )
    state.requirement_title = extracted.requirement_title
    state.summary = extracted.summary
    state.tags = extracted.tags
    state.current_step = "retrieve"
    return state


def retrieval_candidates(state: RequirementGraphState) -> RequirementGraphState:
    agent = RetrievalAgent()
    state.candidates = agent.retrieve(state.summary or state.source_text, limit=5)
    state.current_step = "analyze"
    return state


def analyze_requirement(state: RequirementGraphState) -> RequirementGraphState:
    agent = AnalyzeAgent()
    extracted = ExtractedRequirement(
        requirement_title=state.requirement_title or state.summary or "新需求",
        summary=state.summary or state.source_text,
        requester_name=state.requester_name,
        source_type=state.source_type,
        business_domain="general",
        tags=state.tags,
        requirements=[state.summary or state.source_text],
        raw_text=state.source_text,
    )
    state.analysis = agent.analyze(extracted, state.candidates).model_dump(mode="python")
    state.current_step = "risk"
    return state


def assess_risk(state: RequirementGraphState) -> RequirementGraphState:
    agent = RiskAgent()
    extracted = ExtractedRequirement(
        requirement_title=state.requirement_title or state.summary or "新需求",
        summary=state.summary or state.source_text,
        requester_name=state.requester_name,
        source_type=state.source_type,
        business_domain="general",
        tags=state.tags,
        requirements=[state.summary or state.source_text],
        raw_text=state.source_text,
    )
    state.risk = agent.assess(extracted).model_dump(mode="python")
    state.current_step = "review" if state.analysis.get("duplicate") or state.analysis.get("conflict") else "commit"
    return state


def review_requirement(state: RequirementGraphState) -> RequirementGraphState:
    analysis = state.analysis or {}
    risk = state.risk or {}

    if state.review_decision is None:
        if analysis.get("duplicate") or analysis.get("conflict"):
            state.review_decision = "needs_revision"
        elif any(risk.get(key) == "high" for key in ["quality_risk", "change_risk", "technical_impact_risk"]):
            state.review_decision = "needs_revision"
        else:
            state.review_decision = "approved"

    if state.review_decision == "needs_revision":
        state.status = "needs_revision"
        state.review_comment = (
            analysis.get("reasoning")
            or "需求存在重复、冲突或较高风险，建议在提交前补充澄清与修正。"
        )
        state.current_step = "done"
    elif state.review_decision == "approved":
        state.status = "in_review"
        state.review_comment = "审核通过，等待提交入库。"
        state.current_step = "commit"
    else:
        state.status = "rejected"
        state.review_comment = "需求已被拒绝，未进入提交流程。"
        state.current_step = "rejected"

    return state


def commit_requirement(state: RequirementGraphState) -> RequirementGraphState:
    if not state.requirement_key:
        state.requirement_key = f"REQ-{abs(hash(state.trace_id)) % 100000:06d}"
    state.final_requirement = state.summary or state.requirement_title or state.source_text
    state.status = "committed"
    state.current_step = "done"
    return state
