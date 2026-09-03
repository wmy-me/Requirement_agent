"""Routing logic for the requirement workflow graph."""

from __future__ import annotations

from src.graph.state import RequirementGraphState


def route_after_extract(state: RequirementGraphState) -> str:
    return "retrieve" if state.summary or state.source_text else "done"


def route_after_retrieval(state: RequirementGraphState) -> str:
    return "analyze" if state.candidates or state.source_text else "done"


def route_after_analysis(state: RequirementGraphState) -> str:
    if state.analysis.get("duplicate") or state.analysis.get("conflict"):
        return "review"
    return "risk"


def route_after_risk(state: RequirementGraphState) -> str:
    if state.risk.get("quality_risk") in {"medium", "high"}:
        return "review"
    if state.risk.get("change_risk") in {"medium", "high"}:
        return "review"
    if state.risk.get("technical_impact_risk") in {"medium", "high"}:
        return "review"
    return "commit"


def route_after_review(state: RequirementGraphState) -> str:
    if state.review_decision in {"approved", "needs_revision"}:
        return "commit"
    if state.review_decision == "rejected":
        return "rejected"
    return "commit"
