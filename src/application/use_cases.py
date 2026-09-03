"""Application use cases shared by HTTP and MCP interfaces."""

from __future__ import annotations

from src.domain.requirement import Requirement, RequirementSource


class RequirementUseCases:
    """Thin orchestration layer for requirements processing.

    The actual business logic should be implemented with repositories and
    domain services, but the interface remains consistent for API and MCP.
    """

    def create_requirement(self, source: RequirementSource) -> Requirement:
        requirement = Requirement(
            requirement_key="REQ-000001",
            requirement_name=source.requester_name or "New requirement",
            final_requirement=source.original_text or "",
        )
        return requirement
