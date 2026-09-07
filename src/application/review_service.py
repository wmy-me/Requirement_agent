"""审核决策的应用服务。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from src.domain.requirement import AuditEvent, RequirementMaster, RequirementReview, RequirementSource, RequirementVersion
from src.infrastructure.db.repositories import (
    AuditRepository,
    RequirementMasterRepository,
    RequirementReviewRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.infrastructure.db.session import SessionLocal
from src.infrastructure.worker.outbox import OutboxRepository


class ReviewService:
    """负责审核人决策记录、版本推进和审计日志闭环。"""

    def __init__(
        self,
        review_repo: RequirementReviewRepository | None = None,
        source_repo: RequirementSourceRepository | None = None,
        master_repo: RequirementMasterRepository | None = None,
        version_repo: RequirementVersionRepository | None = None,
        audit_repo: AuditRepository | None = None,
        outbox_repo: OutboxRepository | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self.review_repo = review_repo or RequirementReviewRepository()
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.version_repo = version_repo or RequirementVersionRepository()
        self.audit_repo = audit_repo or AuditRepository()
        self.outbox_repo = outbox_repo or OutboxRepository()
        self.session_factory = session_factory

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
    ) -> dict[str, object]:
        if decision not in {"approved", "rejected", "returned"}:
            raise ValueError("decision must be approved, rejected, or returned")

        session = self.session_factory()
        try:
            source = self.source_repo.get_by_id(source_id, session=session)
            if source is None:
                raise ValueError(f"source_id={source_id} not found")
            if source.processing_status != "pending_review":
                raise ValueError(f"source_id={source_id} is not pending review")

            review = RequirementReview(
                source_id=source_id,
                analysis_snapshot=analysis_snapshot or dict(source.metadata.get("analysis") or {}),
                decision=decision,
                reviewer_id=reviewer_id,
                reviewer_name=reviewer_name,
                review_comment=comment,
                edited_requirement=edited_requirement,
            )
            self.review_repo.save(review, session=session)

            target_key = requirement_key
            master: RequirementMaster | None = None
            if target_key:
                master = self.master_repo.get_by_key(str(target_key), session=session)

            if decision == "approved":
                canonical_title = self._canonical_title(source, edited_requirement)
                canonical_requirement = self._canonical_requirement(source, edited_requirement)
                if master is None:
                    master = RequirementMaster(
                        requirement_key=self.master_repo.allocate_key(session=session),
                        requirement_name=canonical_title[:80],
                        final_requirement=canonical_requirement,
                        current_version=0,
                        status="active",
                        lock_version=0,
                    )
                    master = self.master_repo.save(master, session=session)

                current_version = int(master.current_version or 0)
                next_version = current_version + 1
                version = RequirementVersion(
                    requirement_id=int(master.id or 0),
                    parent_version_id=None,
                    version_no=next_version,
                    version_title=canonical_title[:80],
                    change_type="new" if current_version == 0 else "modify",
                    requirement_snapshot=canonical_requirement,
                    change_summary=comment or "审核通过并生成版本快照",
                    diff_payload={
                        "decision": decision,
                        "reviewer_id": reviewer_id,
                        "reviewer_name": reviewer_name,
                        "edited_requirement": edited_requirement,
                        "source_id": source_id,
                        "source_type": source.source_type,
                        "source_event_id": source.source_event_id,
                        "source_original_text": source.original_text,
                        "source_extracted_text": source.extracted_text,
                        "structured_extraction": source.metadata.get("extracted") or {},
                        "standardized_document": source.metadata.get("standardized_document") or {},
                        "analysis": source.metadata.get("analysis") or analysis_snapshot or {},
                        "risk": source.metadata.get("risk") or {},
                    },
                    created_by=reviewer_id,
                    reviewed_by=reviewer_id,
                )
                saved_version = self.version_repo.save(version, session=session)
                if saved_version.id is not None and source.id is not None:
                    self.version_repo.link_source(saved_version.id, source.id, session=session)
                master.requirement_name = canonical_title[:80]
                master.final_requirement = canonical_requirement
                master.current_version = next_version
                master.status = "active"
                master.lock_version = next_version
                self.master_repo.save(master, session=session)

                trace_metadata = {
                    **source.metadata,
                    "requirement_key": master.requirement_key,
                    "requirement_id": master.id,
                    "version_id": saved_version.id,
                    "version_no": saved_version.version_no,
                    "trace": {
                        "source_id": source_id,
                        "requirement_key": master.requirement_key,
                        "version_id": saved_version.id,
                        "version_no": saved_version.version_no,
                        "relation_type": "source",
                    },
                }
                self.source_repo.update_status(
                    source_id,
                    "committed",
                    metadata=trace_metadata,
                    session=session,
                )
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
                    ),
                    session=session,
                )
                self.outbox_repo.enqueue(
                    aggregate_type="requirement_master",
                    aggregate_id=str(master.requirement_key),
                    event_type="embedding_sync",
                    payload={
                        "requirement_id": master.id,
                        "requirement_key": master.requirement_key,
                        "version_id": saved_version.id,
                        "version_no": saved_version.version_no,
                        "content": master.final_requirement,
                    },
                    session=session,
                )
                session.commit()
                return {
                    "decision": review.decision,
                    "reviewer_id": review.reviewer_id,
                    "status": "recorded",
                    "version_no": saved_version.version_no,
                    "requirement_key": master.requirement_key,
                }

            self.source_repo.update_status(
                source_id,
                decision,
                metadata=source.metadata,
                session=session,
            )
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
                ),
                session=session,
            )
            session.commit()
            return {
                "decision": review.decision,
                "reviewer_id": review.reviewer_id,
                "status": "recorded",
                "version_no": None,
                "requirement_key": target_key,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _canonical_title(self, source: RequirementSource, edited_requirement: str | None) -> str:
        edited = (edited_requirement or "").strip()
        if edited:
            return edited.splitlines()[0] or "待命名需求"
        extracted = source.metadata.get("extracted") if isinstance(source.metadata, dict) else None
        if isinstance(extracted, dict):
            title = str(extracted.get("requirement_title") or "").strip()
            if title:
                return title
        return (source.extracted_text or source.original_text or "待命名需求").strip().splitlines()[0]

    def _canonical_requirement(self, source: RequirementSource, edited_requirement: str | None) -> str:
        edited = (edited_requirement or "").strip()
        if edited:
            return edited
        extracted = source.metadata.get("extracted") if isinstance(source.metadata, dict) else None
        if isinstance(extracted, dict):
            requirements = extracted.get("requirements")
            if isinstance(requirements, list) and requirements:
                normalized = [str(item).strip() for item in requirements if str(item).strip()]
                if normalized:
                    return "\n".join(normalized)
            summary = str(extracted.get("summary") or "").strip()
            if summary:
                return summary
        return (source.extracted_text or source.original_text or "待补充需求说明").strip()
