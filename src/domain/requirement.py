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
class RequirementAttachment:
    """Attachment metadata for a source requirement."""

    source_id: int
    file_name: str
    content_type: str
    object_uri: str
    file_hash: str
    file_size: int
    extraction_status: str = "pending"


@dataclass(slots=True)
class RequirementMaster:
    """Canonical business requirement after approval."""

    requirement_key: str
    requirement_name: str
    final_requirement: str
    current_version: int = 0
    status: str = "active"
    lock_version: int = 0


@dataclass(slots=True)
class RequirementVersion:
    """Version snapshot and metadata for a requirement change."""

    requirement_id: int
    parent_version_id: int | None
    version_no: int
    version_title: str
    change_type: str
    requirement_snapshot: str
    change_summary: str
    diff_payload: dict[str, object] = field(default_factory=dict)
    created_by: str = "system"
    reviewed_by: str = "system"


@dataclass(slots=True)
class RequirementReview:
    """Manual review result for a requirement source."""

    source_id: int
    analysis_snapshot: dict[str, object]
    decision: str
    reviewer_id: str
    reviewer_name: str | None = None
    review_comment: str | None = None
    edited_requirement: str | None = None


@dataclass(slots=True)
class AuditEvent:
    """Domain audit event for business-level state changes."""

    trace_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str | None = None
    actor_type: str = "system"
    actor_id: str | None = None
    before_data: dict[str, object] | None = None
    after_data: dict[str, object] | None = None
    result_status: str = "success"
    error_code: str | None = None
