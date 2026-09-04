"""需求工作流图的路由逻辑。"""

from __future__ import annotations

from src.graph.state import RequirementGraphState


def route_after_extract(state: RequirementGraphState) -> str:
    return "retrieve" if state.summary or state.source_text else "done"


def route_after_retrieval(state: RequirementGraphState) -> str:
    return "analyze" if state.candidates or state.source_text else "done"


def route_after_analysis(state: RequirementGraphState) -> str:
    analysis = state.analysis or {}
    if analysis.get("duplicate") or analysis.get("conflict") or analysis.get("related"):
        return "review"
    return "risk"


def route_after_risk(state: RequirementGraphState) -> str:
    risk = state.risk or {}
    if risk.get("quality_risk") == "high":
        return "review"
    if risk.get("change_risk") == "high":
        return "review"
    if risk.get("technical_impact_risk") == "high":
        return "review"
    return "commit"


def route_after_review(state: RequirementGraphState) -> str:
    if state.review_decision == "approved":
        return "commit"
    if state.review_decision == "needs_revision":
        return "done"
    if state.review_decision == "rejected":
        return "rejected"
    return "commit"
