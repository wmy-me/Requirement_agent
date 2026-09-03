"""Repository interfaces and stubs for business data."""

from __future__ import annotations


class RequirementRepository:
    """Persistence boundary for requirement records."""

    def list(self) -> list[dict[str, object]]:
        return []


class AuditRepository:
    """Persistence boundary for audit event records."""

    def list(self) -> list[dict[str, object]]:
        return []
