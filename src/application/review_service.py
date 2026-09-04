"""审核决策的应用服务。"""

from __future__ import annotations

from src.domain.requirement import AuditEvent, RequirementMaster, RequirementReview, RequirementVersion
from src.infrastructure.db.repositories import (
    AuditRepository,
    RequirementMasterRepository,
    RequirementReviewRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)


class ReviewService:
    """负责审核人决策记录、版本推进和审计日志闭环。"""

    def __init__(
        self,
        review_repo: RequirementReviewRepository | None = None,
        source_repo: RequirementSourceRepository | None = None,
        master_repo: RequirementMasterRepository | None = None,
        version_repo: RequirementVersionRepository | None = None,
        audit_repo: AuditRepository | None = None,
    ) -> None:
        self.review_repo = review_repo or RequirementReviewRepository()
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.version_repo = version_repo or RequirementVersionRepository()
        self.audit_repo = audit_repo or AuditRepository()

    def submit_decision(
        self,
        *,
        source_id: int,
        decision: str,
        reviewer_id: str,
        reviewer_name: str | None = None,
        comment: str | None = None,
        edited_requirement: str | None = None,
        analysis_snapshot: dict[str, object] | None = None,
        requirement_key: str | None = None,
    ) -> dict[str, str | bool | None]:
        source = self.source_repo.get_by_id(source_id)
        if source is None:
            raise ValueError(f"source_id={source_id} not found")

        review = RequirementReview(
            source_id=source_id,
            analysis_snapshot=analysis_snapshot or {"status": "pending_review"},
            decision=decision,
            reviewer_id=reviewer_id,
            reviewer_name=reviewer_name,
            review_comment=comment,
            edited_requirement=edited_requirement,
        )
        self.review_repo.save(review)

        target_key = requirement_key or source.metadata.get("requirement_key")
        master: RequirementMaster | None = None
        if target_key:
            master = self.master_repo.get_by_key(str(target_key))

        if decision == "approved":
            if master is None:
                master = RequirementMaster(
                    requirement_key=str(target_key or "REQ-000001"),
                    requirement_name=(edited_requirement or source.original_text or "待命名需求")[:80],
                    final_requirement=edited_requirement or source.original_text or "待补充需求说明",
                    current_version=0,
                    status="active",
                    lock_version=0,
                )
                master = self.master_repo.save(master)

            current_version = int(master.current_version or 0)
            next_version = current_version + 1
            version = RequirementVersion(
                requirement_id=int(master.id or 0),
                parent_version_id=None,
                version_no=next_version,
                version_title=(edited_requirement or master.final_requirement)[:80],
                change_type="modify",
                requirement_snapshot=edited_requirement or master.final_requirement,
                change_summary=comment or "审核通过并生成版本快照",
                diff_payload={
                    "decision": decision,
                    "reviewer_id": reviewer_id,
                    "reviewer_name": reviewer_name,
                    "edited_requirement": edited_requirement,
                },
                created_by=reviewer_id,
                reviewed_by=reviewer_id,
            )
            saved_version = self.version_repo.save(version)
            master.final_requirement = edited_requirement or master.final_requirement
            master.current_version = next_version
            master.status = "active"
            master.lock_version = next_version
            self.master_repo.save(master)

            self.audit_repo.record(
                AuditEvent(
                    trace_id=f"review-{source_id}-{reviewer_id}",
                    event_type="requirement_review_approved",
                    aggregate_type="requirement_master",
                    aggregate_id=str(master.requirement_key),
                    actor_type="reviewer",
                    actor_id=reviewer_id,
                    before_data={"source_id": source_id, "version": current_version},
                    after_data={"version": next_version, "requirement_key": master.requirement_key},
                    result_status="success",
                )
            )
            return {
                "decision": review.decision,
                "reviewer_id": review.reviewer_id,
                "status": "recorded",
                "version_no": saved_version.version_no,
                "requirement_key": master.requirement_key,
            }

        self.audit_repo.record(
            AuditEvent(
                trace_id=f"review-{source_id}-{reviewer_id}",
                event_type=f"requirement_review_{decision}",
                aggregate_type="requirement_source",
                aggregate_id=str(source_id),
                actor_type="reviewer",
                actor_id=reviewer_id,
                before_data={"source_id": source_id},
                after_data={"decision": decision, "comment": comment},
                result_status="success",
            )
        )
        return {
            "decision": review.decision,
            "reviewer_id": review.reviewer_id,
            "status": "recorded",
            "version_no": None,
            "requirement_key": target_key,
        }
