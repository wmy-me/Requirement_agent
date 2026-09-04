"""用于需求处理的 LangGraph 风格编排器。"""

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
    """一个轻量的 LangGraph 风格执行器，用于串联需求处理节点。"""

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
                state.status = "extracting"
                next_step = route_after_extract(state)
            elif next_step == "retrieve":
                state = self.nodes["retrieve"](state)
                state.status = "retrieving"
                next_step = route_after_retrieval(state)
            elif next_step == "analyze":
                state = self.nodes["analyze"](state)
                state.status = "analyzing"
                next_step = route_after_analysis(state)
            elif next_step == "risk":
                state = self.nodes["risk"](state)
                state.status = "assessing_risk"
                next_step = route_after_risk(state)
            elif next_step == "review":
                state = self.nodes["review"](state)
                state.status = state.status or "in_review"
                next_step = route_after_review(state)
            elif next_step == "commit":
                state = self.nodes["commit"](state)
                state.status = "committed"
                next_step = "done"
            else:
                break

        if state.status in {"", "pending"}:
            state.status = "done" if next_step == "done" else state.status

        return state
