"""Graph state for the requirement workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class RequirementGraphState:
    """Mutable state carried through the LangGraph-style workflow."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    source_text: str = ""
    source_type: str = "web"
    requester_name: str | None = None
    requirement_title: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    review_decision: str | None = None
    review_comment: str | None = None
    final_requirement: str = ""
    requirement_key: str = ""
    status: str = "pending"
    current_step: str = "extract"
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
