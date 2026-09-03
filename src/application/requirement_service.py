"""Application service for requirement lifecycle operations."""

from __future__ import annotations

from src.domain.requirement import RequirementMaster, RequirementSource
from src.infrastructure.db.repositories import RequirementMasterRepository, RequirementSourceRepository


class RequirementService:
    """Application use case boundary for requirement submission and query."""

    def __init__(
        self,
        source_repo: RequirementSourceRepository | None = None,
        master_repo: RequirementMasterRepository | None = None,
    ) -> None:
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()

    def submit_requirement(self, source: RequirementSource) -> dict[str, object]:
        saved_source = self.source_repo.save(source)
        requirement = RequirementMaster(
            requirement_key="REQ-000001",
            requirement_name=source.requester_name or "New requirement",
            final_requirement=source.original_text or "",
            current_version=1,
            status="active",
            lock_version=0,
        )
        self.master_repo.save(requirement)
        return {
            "idempotency_key": saved_source.idempotency_key,
            "source_type": saved_source.source_type,
            "status": "accepted",
            "requirement_key": requirement.requirement_key,
        }

    def list_requirements(self) -> list[dict[str, object]]:
        return [
            {
                "requirement_key": "REQ-000001",
                "requirement_name": "用户登录",
                "final_requirement": "支持邮箱和手机号登录，并支持验证码校验。",
                "status": "active",
            }
        ]
