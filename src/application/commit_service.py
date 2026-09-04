"""用于需求事务写入的提交服务。"""

from __future__ import annotations

from src.domain.requirement import AuditEvent, RequirementMaster, RequirementVersion
from src.infrastructure.db.repositories import AuditRepository, RequirementMasterRepository, RequirementVersionRepository


class CommitService:
    """协调需求创建、版本记录和审计日志的事务流程。"""

    def __init__(
        self,
        master_repo: RequirementMasterRepository | None = None,
        version_repo: RequirementVersionRepository | None = None,
        audit_repo: AuditRepository | None = None,
    ) -> None:
        self.master_repo = master_repo or RequirementMasterRepository()
        self.version_repo = version_repo or RequirementVersionRepository()
        self.audit_repo = audit_repo or AuditRepository()

    def create_or_update_requirement(
        self,
        *,
        requirement: RequirementMaster,
        version: RequirementVersion,
        trace_id: str,
        actor_id: str = "system",
    ) -> dict[str, object]:
        saved_requirement = self.master_repo.save(requirement)
        saved_version = self.version_repo.save(version)
        self.audit_repo.record(
            AuditEvent(
                trace_id=trace_id,
                event_type="requirement_committed",
                aggregate_type="requirement_master",
                aggregate_id=str(saved_requirement.requirement_key),
                actor_type="system",
                actor_id=actor_id,
                before_data={"version": version.version_no - 1},
                after_data={"version": version.version_no, "requirement_key": saved_requirement.requirement_key},
                result_status="success",
            )
        )
        return {
            "requirement_key": saved_requirement.requirement_key,
            "version_no": saved_version.version_no,
            "status": "committed",
        }
