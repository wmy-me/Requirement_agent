"""Node functions for the requirement workflow graph."""

from __future__ import annotations

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent, ExtractedRequirement
from src.agents.risk_agent import RiskAgent
from src.application.retrieval_service import RetrievalService
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
    service = RetrievalService()
    state.candidates = service.search(state.summary or state.source_text, limit=5)
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
    if state.review_decision is None:
        state.review_decision = "approved" if not state.analysis.get("duplicate") else "needs_revision"
    if state.review_decision == "needs_revision":
        state.status = "needs_revision"
    elif state.review_decision == "approved":
        state.status = "in_review"
    else:
        state.status = "rejected"
    state.current_step = "commit" if state.review_decision in {"approved", "needs_revision"} else "rejected"
    return state


def commit_requirement(state: RequirementGraphState) -> RequirementGraphState:
    if not state.requirement_key:
        state.requirement_key = f"REQ-{abs(hash(state.trace_id)) % 100000:06d}"
    state.final_requirement = state.summary or state.requirement_title or state.source_text
    state.status = "committed"
    state.current_step = "done"
    return state
