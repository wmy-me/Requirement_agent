from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class RequirementSource:
    """Represents an incoming requirement from a channel."""

    idempotency_key: str
    source_type: str
    requester_id: str | None = None
    requester_name: str | None = None
    original_text: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class Requirement:
    """Canonical business requirement after approval."""

    requirement_key: str
    requirement_name: str
    final_requirement: str
    current_version: int = 1
    status: str = "active"
