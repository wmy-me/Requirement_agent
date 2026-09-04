"""Repository interfaces and database-backed implementations for business data."""

from __future__ import annotations

import json

from sqlalchemy import text

from src.domain.requirement import (
    AuditEvent,
    RequirementMaster,
    RequirementReview,
    RequirementSource,
    RequirementVersion,
)
from src.infrastructure.db.session import SessionLocal


class RequirementSourceRepository:
    """Persistence boundary for raw requirement sources."""

    def save(self, source: RequirementSource) -> RequirementSource:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO requirement_source (
                        idempotency_key, source_type, requester_id, requester_name,
                        original_text, metadata, submitted_at
                    ) VALUES (
                        :idempotency_key, :source_type, :requester_id, :requester_name,
                        :original_text, :metadata, NOW()
                    )
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        source_type = EXCLUDED.source_type,
                        requester_id = EXCLUDED.requester_id,
                        requester_name = EXCLUDED.requester_name,
                        original_text = EXCLUDED.original_text,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    RETURNING id, idempotency_key, source_type, requester_id, requester_name,
                              original_text, metadata, submitted_at
                    """
                ),
                {
                    "idempotency_key": source.idempotency_key,
                    "source_type": source.source_type,
                    "requester_id": source.requester_id,
                    "requester_name": source.requester_name,
                    "original_text": source.original_text,
                    "metadata": json.dumps(source.metadata or {}),
                },
            ).mappings().one()
            session.commit()
            source.metadata = dict(row["metadata"] or {})
            return source

    def get_by_idempotency_key(self, idempotency_key: str) -> RequirementSource | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT idempotency_key, source_type, requester_id, requester_name, original_text, metadata, submitted_at FROM requirement_source WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key},
            ).mappings().first()
        if row is None:
            return None
        return RequirementSource(
            idempotency_key=str(row["idempotency_key"]),
            source_type=str(row["source_type"]),
            requester_id=row["requester_id"],
            requester_name=row["requester_name"],
            original_text=row["original_text"],
            metadata=dict(row["metadata"] or {}),
        )


class RequirementMasterRepository:
    """Persistence boundary for canonical requirements."""

    def save(self, requirement: RequirementMaster) -> RequirementMaster:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO requirement_master (requirement_key, requirement_name, final_requirement, current_version, status, lock_version)
                    VALUES (:requirement_key, :requirement_name, :final_requirement, :current_version, :status, :lock_version)
                    ON CONFLICT (requirement_key) DO UPDATE SET
                        requirement_name = EXCLUDED.requirement_name,
                        final_requirement = EXCLUDED.final_requirement,
                        current_version = EXCLUDED.current_version,
                        status = EXCLUDED.status,
                        lock_version = EXCLUDED.lock_version,
                        updated_at = NOW()
                    RETURNING id, requirement_key, requirement_name, final_requirement, current_version, status, lock_version
                    """
                ),
                {
                    "requirement_key": requirement.requirement_key,
                    "requirement_name": requirement.requirement_name,
                    "final_requirement": requirement.final_requirement,
                    "current_version": requirement.current_version,
                    "status": requirement.status,
                    "lock_version": requirement.lock_version,
                },
            ).mappings().one()
            session.commit()
            requirement_id = int(row["id"])
            requirement.current_version = int(row["current_version"])
            requirement.status = str(row["status"])
            requirement.lock_version = int(row["lock_version"])
            requirement.id = requirement_id
            return requirement

    def get_by_id(self, requirement_id: int) -> RequirementMaster | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT id, requirement_key, requirement_name, final_requirement, current_version, status, lock_version FROM requirement_master WHERE id = :id"
                ),
                {"id": requirement_id},
            ).mappings().first()
        if row is None:
            return None
        return RequirementMaster(
            requirement_key=str(row["requirement_key"]),
            requirement_name=str(row["requirement_name"]),
            final_requirement=str(row["final_requirement"]),
            current_version=int(row["current_version"]),
            status=str(row["status"]),
            lock_version=int(row["lock_version"]),
        )

    def list(self) -> list[RequirementMaster]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT requirement_key, requirement_name, final_requirement, current_version, status, lock_version FROM requirement_master ORDER BY created_at DESC LIMIT 50"
                )
            ).mappings().all()
        return [
            RequirementMaster(
                requirement_key=str(item["requirement_key"]),
                requirement_name=str(item["requirement_name"]),
                final_requirement=str(item["final_requirement"]),
                current_version=int(item["current_version"]),
                status=str(item["status"]),
                lock_version=int(item["lock_version"]),
            )
            for item in rows
        ]

    def search(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        clause = "%" + query + "%"
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT requirement_key, requirement_name, final_requirement, current_version, status
                    FROM requirement_master
                    WHERE requirement_name ILIKE :query OR final_requirement ILIKE :query
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"query": clause, "limit": limit},
            ).mappings().all()
        return [
            {
                "requirement_key": row["requirement_key"],
                "requirement_name": row["requirement_name"],
                "final_requirement": row["final_requirement"],
                "status": row["status"],
                "current_version": row["current_version"],
            }
            for row in rows
        ]


class RequirementVersionRepository:
    """Persistence boundary for requirement version snapshots."""

    def save(self, version: RequirementVersion) -> RequirementVersion:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO requirement_version (
                        requirement_id, parent_version_id, version_no, version_title, change_type,
                        requirement_snapshot, change_summary, diff_payload, created_by, reviewed_by
                    ) VALUES (
                        :requirement_id, :parent_version_id, :version_no, :version_title, :change_type,
                        :requirement_snapshot, :change_summary, :diff_payload, :created_by, :reviewed_by
                    )
                    RETURNING id, requirement_id, version_no
                    """
                ),
                {
                    "requirement_id": version.requirement_id,
                    "parent_version_id": version.parent_version_id,
                    "version_no": version.version_no,
                    "version_title": version.version_title,
                    "change_type": version.change_type,
                    "requirement_snapshot": version.requirement_snapshot,
                    "change_summary": version.change_summary,
                    "diff_payload": json.dumps(version.diff_payload or {}),
                    "created_by": version.created_by,
                    "reviewed_by": version.reviewed_by,
                },
            ).mappings().one()
            session.commit()
            version.requirement_id = int(row["requirement_id"])
            return version

    def list_by_requirement(self, requirement_id: int) -> list[RequirementVersion]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT * FROM requirement_version WHERE requirement_id = :requirement_id ORDER BY version_no DESC"
                ),
                {"requirement_id": requirement_id},
            ).mappings().all()
        return [
            RequirementVersion(
                requirement_id=int(item["requirement_id"]),
                parent_version_id=item["parent_version_id"],
                version_no=int(item["version_no"]),
                version_title=str(item["version_title"]),
                change_type=str(item["change_type"]),
                requirement_snapshot=str(item["requirement_snapshot"]),
                change_summary=str(item["change_summary"]),
                diff_payload=dict(item["diff_payload"] or {}),
                created_by=str(item["created_by"]),
                reviewed_by=str(item["reviewed_by"]),
            )
            for item in rows
        ]


class RequirementReviewRepository:
    """Persistence boundary for human review decisions."""

    def save(self, review: RequirementReview) -> RequirementReview:
        with SessionLocal() as session:
            session.execute(
                text(
                    """
                    INSERT INTO requirement_review (
                        source_id, analysis_snapshot, decision, reviewer_id, reviewer_name,
                        review_comment, edited_requirement
                    ) VALUES (
                        :source_id, :analysis_snapshot, :decision, :reviewer_id, :reviewer_name,
                        :review_comment, :edited_requirement
                    )
                    """
                ),
                {
                    "source_id": review.source_id,
                    "analysis_snapshot": json.dumps(review.analysis_snapshot or {}),
                    "decision": review.decision,
                    "reviewer_id": review.reviewer_id,
                    "reviewer_name": review.reviewer_name,
                    "review_comment": review.review_comment,
                    "edited_requirement": review.edited_requirement,
                },
            )
            session.commit()
        return review


class AuditRepository:
    """Persistence boundary for audit event records."""

    def record(self, event: AuditEvent) -> AuditEvent:
        with SessionLocal() as session:
            session.execute(
                text(
                    """
                    INSERT INTO audit_event (
                        trace_id, event_type, aggregate_type, aggregate_id, actor_type, actor_id,
                        before_data, after_data, result_status, error_code
                    ) VALUES (
                        :trace_id, :event_type, :aggregate_type, :aggregate_id, :actor_type, :actor_id,
                        :before_data, :after_data, :result_status, :error_code
                    )
                    """
                ),
                {
                    "trace_id": event.trace_id,
                    "event_type": event.event_type,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": event.aggregate_id,
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "before_data": json.dumps(event.before_data or {}),
                    "after_data": json.dumps(event.after_data or {}),
                    "result_status": event.result_status,
                    "error_code": event.error_code,
                },
            )
            session.commit()
        return event

    def list(self) -> list[AuditEvent]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT trace_id, event_type, aggregate_type, aggregate_id, actor_type, actor_id, before_data, after_data, result_status, error_code FROM audit_event ORDER BY created_at DESC LIMIT 100"
                )
            ).mappings().all()
        return [
            AuditEvent(
                trace_id=str(item["trace_id"]),
                event_type=str(item["event_type"]),
                aggregate_type=str(item["aggregate_type"]),
                aggregate_id=item["aggregate_id"],
                actor_type=str(item["actor_type"]),
                actor_id=item["actor_id"],
                before_data=dict(item["before_data"] or {}),
                after_data=dict(item["after_data"] or {}),
                result_status=str(item["result_status"]),
                error_code=item["error_code"],
            )
            for item in rows
        ]
