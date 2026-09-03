"""LangGraph-style orchestrator for requirement processing."""

from __future__ import annotations

from src.graph.nodes import (
    analyze_requirement,
    assess_risk,
    commit_requirement,
    extract_requirement,
    retrieval_candidates,
    review_requirement,
)
from src.graph.routers import (
    route_after_analysis,
    route_after_extract,
    route_after_risk,
    route_after_retrieval,
    route_after_review,
)
from src.graph.state import RequirementGraphState


class RequirementGraph:
    """Simple orchestration runner that imitates a LangGraph workflow."""

    def __init__(self) -> None:
        self.nodes = {
            "extract": extract_requirement,
            "retrieve": retrieval_candidates,
            "analyze": analyze_requirement,
            "risk": assess_risk,
            "review": review_requirement,
            "commit": commit_requirement,
        }

    def run(self, payload: dict[str, object]) -> RequirementGraphState:
        state = RequirementGraphState(
            source_text=str(payload.get("source_text") or ""),
            source_type=str(payload.get("source_type") or "web"),
            requester_name=payload.get("requester_name"),
        )

        next_step = "extract"
        while next_step not in {"done", "rejected"}:
            if next_step == "extract":
                state = self.nodes["extract"](state)
                next_step = route_after_extract(state)
            elif next_step == "retrieve":
                state = self.nodes["retrieve"](state)
                next_step = route_after_retrieval(state)
            elif next_step == "analyze":
                state = self.nodes["analyze"](state)
                next_step = route_after_analysis(state)
            elif next_step == "risk":
                state = self.nodes["risk"](state)
                next_step = route_after_risk(state)
            elif next_step == "review":
                state = self.nodes["review"](state)
                next_step = route_after_review(state)
            elif next_step == "commit":
                state = self.nodes["commit"](state)
                next_step = "done"
            else:
                break

        return state
