from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from requirement_agent.common.time import utc_now


@dataclass(slots=True)
class RequirementSource:
    """表示从某个渠道进入的原始需求。"""

    idempotency_key: str
    source_type: str
    id: int | None = None
    requester_id: str | None = None
    requester_name: str | None = None
    original_text: str | None = None
    extracted_text: str | None = None
    source_event_id: str | None = None
    original_payload: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)
    processing_status: str = "received"
    submitted_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class RequirementMaster:
    """已批准落地的主需求的规范化表示。"""

    requirement_key: str
    requirement_name: str
    final_requirement: str
    current_version: int = 0
    status: str = "active"
    lock_version: int = 0
    id: int | None = None


@dataclass(slots=True)
class RequirementVersion:
    """需求变更时保留的版本快照及版本元数据。"""

    requirement_id: int
    version_no: int
    version_title: str
    change_type: str
    requirement_snapshot: str
    change_summary: str
    parent_version_id: int | None = None
    parent_version_no: int | None = None
    id: int | None = None
    diff_payload: dict[str, object] = field(default_factory=dict)
    feature_changes: list[dict[str, object]] = field(default_factory=list)
    created_by: str = "system"
    reviewed_by: str = "system"


@dataclass(slots=True)
class RequirementReview:
    """对来源需求的人工审核结果。"""

    source_id: int
    analysis_snapshot: dict[str, object]
    decision: str
    reviewer_id: str
    reviewer_name: str | None = None
    review_comment: str | None = None
    edited_requirement: str | None = None


@dataclass(slots=True)
class AuditEvent:
    """用于记录业务状态变更的审计事件。"""

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
