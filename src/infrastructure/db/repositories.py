"""Repository interfaces and stubs for business data."""

from __future__ import annotations

from src.domain.requirement import (
    AuditEvent,
    RequirementMaster,
    RequirementReview,
    RequirementSource,
    RequirementVersion,
)


class RequirementSourceRepository:
    """Persistence boundary for raw requirement sources."""

    def save(self, source: RequirementSource) -> RequirementSource:
        return source

    def get_by_idempotency_key(self, idempotency_key: str) -> RequirementSource | None:
        return None


class RequirementMasterRepository:
    """Persistence boundary for canonical requirements."""

    def save(self, requirement: RequirementMaster) -> RequirementMaster:
        return requirement

    def get_by_id(self, requirement_id: int) -> RequirementMaster | None:
        return None

    def list(self) -> list[RequirementMaster]:
        return []


class RequirementVersionRepository:
    """Persistence boundary for requirement version snapshots."""

    def save(self, version: RequirementVersion) -> RequirementVersion:
        return version

    def list_by_requirement(self, requirement_id: int) -> list[RequirementVersion]:
        return []


class RequirementReviewRepository:
    """Persistence boundary for human review decisions."""

    def save(self, review: RequirementReview) -> RequirementReview:
        return review


class AuditRepository:
    """Persistence boundary for audit event records."""

    def record(self, event: AuditEvent) -> AuditEvent:
        return event

    def list(self) -> list[AuditEvent]:
        return []
