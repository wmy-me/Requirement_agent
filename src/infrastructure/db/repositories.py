"""Repository interfaces and database-backed implementations for business data."""

from __future__ import annotations

import json
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.domain.requirement import (
    AuditEvent,
    RequirementMaster,
    RequirementReview,
    RequirementSource,
    RequirementVersion,
)
from src.infrastructure.db.session import SessionLocal


class RequirementSourceRepository:
    """原始需求来源的持久化边界。"""

    def save(self, source: RequirementSource, session: Session | None = None) -> RequirementSource:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    INSERT INTO requirement_source (
                        idempotency_key, source_type, source_event_id, requester_id, requester_name,
                        original_text, extracted_text, original_payload, metadata, submitted_at
                    ) VALUES (
                        :idempotency_key, :source_type, :source_event_id, :requester_id, :requester_name,
                        :original_text, :extracted_text, :original_payload, :metadata, NOW()
                    )
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        source_type = EXCLUDED.source_type,
                        source_event_id = EXCLUDED.source_event_id,
                        requester_id = EXCLUDED.requester_id,
                        requester_name = EXCLUDED.requester_name,
                        original_text = EXCLUDED.original_text,
                        extracted_text = EXCLUDED.extracted_text,
                        original_payload = EXCLUDED.original_payload,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    RETURNING id, idempotency_key, source_type, source_event_id, requester_id, requester_name,
                              original_text, extracted_text, original_payload, metadata, processing_status, submitted_at
                    """
                ),
                {
                    "idempotency_key": source.idempotency_key,
                    "source_type": source.source_type,
                    "source_event_id": source.source_event_id,
                    "requester_id": source.requester_id,
                    "requester_name": source.requester_name,
                    "original_text": source.original_text,
                    "extracted_text": source.extracted_text,
                    "original_payload": json.dumps(source.original_payload or {}),
                    "metadata": json.dumps(source.metadata or {}),
                },
            ).mappings().one()
            if owns_session:
                session.commit()
            source.id = int(row["id"])
            source.source_event_id = row["source_event_id"]
            source.extracted_text = row["extracted_text"]
            source.original_payload = dict(row["original_payload"] or {})
            source.metadata = dict(row["metadata"] or {})
            source.processing_status = str(row["processing_status"])
            source.submitted_at = row["submitted_at"]
            if owns_session:
                session.close()
            return source
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def update_status(
        self,
        source_id: int,
        processing_status: str,
        *,
        metadata: dict[str, object] | None = None,
        session: Session | None = None,
    ) -> None:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            values: dict[str, object] = {
                "id": source_id,
                "processing_status": processing_status,
            }
            metadata_clause = ""
            if metadata is not None:
                values["metadata"] = json.dumps(metadata)
                metadata_clause = ", metadata = CAST(:metadata AS JSONB)"
            session.execute(
                text(
                    f"UPDATE requirement_source SET processing_status = :processing_status{metadata_clause}, updated_at = NOW() WHERE id = :id"
                ),
                values,
            )
            if owns_session:
                session.commit()
                session.close()
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def update_extraction(
        self,
        source_id: int,
        *,
        extracted_text: str,
        metadata: dict[str, object],
        processing_status: str = "extracting",
        session: Session | None = None,
    ) -> None:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            session.execute(
                text(
                    """
                    UPDATE requirement_source
                    SET extracted_text = :extracted_text,
                        metadata = CAST(:metadata AS JSONB),
                        processing_status = :processing_status,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": source_id,
                    "extracted_text": extracted_text,
                    "metadata": json.dumps(metadata),
                    "processing_status": processing_status,
                },
            )
            if owns_session:
                session.commit()
                session.close()
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def get_by_id(self, source_id: int, session: Session | None = None) -> RequirementSource | None:
        owns_session = session is None
        session = session or SessionLocal()
        row = session.execute(
            text(
                "SELECT id, idempotency_key, source_type, source_event_id, requester_id, requester_name, original_text, extracted_text, original_payload, metadata, processing_status, submitted_at FROM requirement_source WHERE id = :id"
            ),
            {"id": source_id},
        ).mappings().first()
        if owns_session:
            session.close()
        if row is None:
            return None
        return RequirementSource(
            id=int(row["id"]),
            idempotency_key=str(row["idempotency_key"]),
            source_type=str(row["source_type"]),
            requester_id=row["requester_id"],
            requester_name=row["requester_name"],
            original_text=row["original_text"],
            extracted_text=row["extracted_text"],
            source_event_id=row["source_event_id"],
            original_payload=dict(row["original_payload"] or {}),
            metadata=dict(row["metadata"] or {}),
            processing_status=str(row["processing_status"]),
            submitted_at=row["submitted_at"],
        )

    def get_by_idempotency_key(self, idempotency_key: str) -> RequirementSource | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT id, idempotency_key, source_type, source_event_id, requester_id, requester_name, original_text, extracted_text, original_payload, metadata, processing_status, submitted_at FROM requirement_source WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key},
            ).mappings().first()
        if row is None:
            return None
        return RequirementSource(
            id=int(row["id"]),
            idempotency_key=str(row["idempotency_key"]),
            source_type=str(row["source_type"]),
            requester_id=row["requester_id"],
            requester_name=row["requester_name"],
            original_text=row["original_text"],
            extracted_text=row["extracted_text"],
            source_event_id=row["source_event_id"],
            original_payload=dict(row["original_payload"] or {}),
            metadata=dict(row["metadata"] or {}),
            processing_status=str(row["processing_status"]),
            submitted_at=row["submitted_at"],
        )

    def list_by_status(self, processing_status: str, limit: int = 20) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, source_type, source_event_id, requester_id, requester_name,
                           original_text, extracted_text, original_payload, metadata,
                           processing_status, submitted_at, updated_at
                    FROM requirement_source
                    WHERE processing_status = :processing_status
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"processing_status": processing_status, "limit": limit},
            ).mappings().all()
        return [
            {
                "source_id": int(row["id"]),
                "source_type": row["source_type"],
                "source_event_id": row["source_event_id"],
                "requester_id": row["requester_id"],
                "requester_name": row["requester_name"],
                "original_text": row["original_text"],
                "extracted_text": row["extracted_text"],
                "original_payload": dict(row["original_payload"] or {}),
                "metadata": dict(row["metadata"] or {}),
                "processing_status": row["processing_status"],
                "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
            for row in rows
        ]

    def get_detail(self, source_id: int) -> dict[str, object] | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, source_type, source_event_id, requester_id, requester_name,
                           original_text, extracted_text, original_payload, metadata,
                           processing_status, submitted_at, updated_at
                    FROM requirement_source
                    WHERE id = :id
                    """
                ),
                {"id": source_id},
            ).mappings().first()
        if row is None:
            return None
        return {
            "source_id": int(row["id"]),
            "source_type": row["source_type"],
            "source_event_id": row["source_event_id"],
            "requester_id": row["requester_id"],
            "requester_name": row["requester_name"],
            "original_text": row["original_text"],
            "extracted_text": row["extracted_text"],
            "original_payload": dict(row["original_payload"] or {}),
            "metadata": dict(row["metadata"] or {}),
            "processing_status": row["processing_status"],
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }

    def get_trace(self, source_id: int) -> dict[str, object] | None:
        source = self.get_detail(source_id)
        if source is None:
            return None

        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT m.id AS requirement_id, m.requirement_key, m.requirement_name,
                           m.current_version, m.status AS requirement_status,
                           v.id AS version_id, v.version_no, v.version_title,
                           v.change_type, v.created_at AS version_created_at,
                           vs.relation_type
                    FROM requirement_version_source vs
                    JOIN requirement_version v ON v.id = vs.version_id
                    JOIN requirement_master m ON m.id = v.requirement_id
                    WHERE vs.source_id = :source_id
                    ORDER BY v.created_at DESC
                    """
                ),
                {"source_id": source_id},
            ).mappings().all()

        source_metadata = dict(source.get("metadata") or {})
        return {
            "source": source,
            "extraction": {
                "extracted_text": source.get("extracted_text"),
                "standardized_document": source_metadata.get("standardized_document") or {},
                "structured_requirement": source_metadata.get("extracted") or {},
                "analysis": source_metadata.get("analysis") or {},
                "risk": source_metadata.get("risk") or {},
            },
            "mappings": [
                {
                    "requirement_id": int(row["requirement_id"]),
                    "requirement_key": row["requirement_key"],
                    "requirement_name": row["requirement_name"],
                    "current_version": int(row["current_version"]),
                    "requirement_status": row["requirement_status"],
                    "version_id": int(row["version_id"]),
                    "version_no": int(row["version_no"]),
                    "version_title": row["version_title"],
                    "change_type": row["change_type"],
                    "relation_type": row["relation_type"],
                    "version_created_at": row["version_created_at"].isoformat() if row["version_created_at"] else None,
                }
                for row in rows
            ],
        }


class RequirementMasterRepository:
    """规范化主需求的持久化边界。"""

    def save(self, requirement: RequirementMaster, session: Session | None = None) -> RequirementMaster:
        owns_session = session is None
        session = session or SessionLocal()
        try:
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
            if owns_session:
                session.commit()
            requirement_id = int(row["id"])
            requirement.current_version = int(row["current_version"])
            requirement.status = str(row["status"])
            requirement.lock_version = int(row["lock_version"])
            requirement.id = requirement_id
            if owns_session:
                session.close()
            return requirement
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

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

    def allocate_key(self, session: Session | None = None) -> str:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            value = session.execute(text("SELECT nextval('requirement_key_seq')")).scalar_one()
            key = f"REQ-{int(value):06d}"
            if owns_session:
                session.commit()
                session.close()
            return key
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def get_by_key(self, requirement_key: str, session: Session | None = None) -> RequirementMaster | None:
        owns_session = session is None
        session = session or SessionLocal()
        row = session.execute(
                text(
                    "SELECT id, requirement_key, requirement_name, final_requirement, current_version, status, lock_version FROM requirement_master WHERE requirement_key = :requirement_key"
                ),
                {"requirement_key": requirement_key},
            ).mappings().first()
        if owns_session:
            session.close()
        if row is None:
            return None
        return RequirementMaster(
            id=int(row["id"]),
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

    def list_with_source_context(self, limit: int = 100) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT m.id, m.requirement_key, m.requirement_name, m.final_requirement,
                           m.current_version, m.status, m.lock_version,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT s.source_type), NULL) AS source_types,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(
                               s.metadata->>'department',
                               s.metadata #>> '{standardized_document,normalized_fields,department}'
                           )), NULL) AS departments,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(
                               s.metadata->>'business_domain',
                               s.metadata #>> '{standardized_document,normalized_fields,business_domain}',
                               s.metadata #>> '{extracted,business_domain}'
                           )), NULL) AS business_domains,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(
                               s.metadata->>'sensitivity_level',
                               s.metadata #>> '{standardized_document,normalized_fields,sensitivity_level}'
                           )), NULL) AS sensitivity_levels,
                           MIN(s.submitted_at) AS first_source_submitted_at,
                           MAX(s.submitted_at) AS latest_source_submitted_at
                    FROM requirement_master m
                    LEFT JOIN requirement_version v ON v.requirement_id = m.id
                    LEFT JOIN requirement_version_source vs ON vs.version_id = v.id
                    LEFT JOIN requirement_source s ON s.id = vs.source_id
                    GROUP BY m.id, m.requirement_key, m.requirement_name, m.final_requirement,
                             m.current_version, m.status, m.lock_version
                    ORDER BY m.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [
            {
                "id": int(row["id"]),
                "requirement_key": row["requirement_key"],
                "requirement_name": row["requirement_name"],
                "final_requirement": row["final_requirement"],
                "current_version": int(row["current_version"]),
                "status": row["status"],
                "lock_version": int(row["lock_version"]),
                "source_types": list(row["source_types"] or []),
                "departments": list(row["departments"] or []),
                "business_domains": list(row["business_domains"] or []),
                "sensitivity_levels": list(row["sensitivity_levels"] or []),
                "first_source_submitted_at": row["first_source_submitted_at"].isoformat()
                if row["first_source_submitted_at"]
                else None,
                "latest_source_submitted_at": row["latest_source_submitted_at"].isoformat()
                if row["latest_source_submitted_at"]
                else None,
            }
            for row in rows
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
    """需求版本快照的持久化边界。"""

    def save(self, version: RequirementVersion, session: Session | None = None) -> RequirementVersion:
        owns_session = session is None
        session = session or SessionLocal()
        try:
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
            if owns_session:
                session.commit()
            version.id = int(row["id"])
            version.requirement_id = int(row["requirement_id"])
            if owns_session:
                session.close()
            return version
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

    def link_source(self, version_id: int, source_id: int, session: Session | None = None) -> None:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            session.execute(
                text(
                    """
                    INSERT INTO requirement_version_source (version_id, source_id, relation_type)
                    VALUES (:version_id, :source_id, 'source')
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"version_id": version_id, "source_id": source_id},
            )
            if owns_session:
                session.commit()
                session.close()
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

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

    def get_latest_by_requirement(self, requirement_id: int) -> RequirementVersion | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT * FROM requirement_version WHERE requirement_id = :requirement_id ORDER BY version_no DESC LIMIT 1"
                ),
                {"requirement_id": requirement_id},
            ).mappings().first()
        if row is None:
            return None
        return RequirementVersion(
            requirement_id=int(row["requirement_id"]),
            parent_version_id=row["parent_version_id"],
            version_no=int(row["version_no"]),
            version_title=str(row["version_title"]),
            change_type=str(row["change_type"]),
            requirement_snapshot=str(row["requirement_snapshot"]),
            change_summary=str(row["change_summary"]),
            diff_payload=dict(row["diff_payload"] or {}),
            created_by=str(row["created_by"]),
            reviewed_by=str(row["reviewed_by"]),
        )

    def list_by_requirement_key(self, requirement_key: str) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT v.id, v.requirement_id, v.parent_version_id, v.version_no,
                           v.version_title, v.change_type, v.requirement_snapshot,
                           v.change_summary, v.diff_payload, v.created_by, v.reviewed_by,
                           v.created_at
                    FROM requirement_version v
                    JOIN requirement_master m ON m.id = v.requirement_id
                    WHERE m.requirement_key = :requirement_key
                    ORDER BY v.version_no DESC
                    """
                ),
                {"requirement_key": requirement_key},
            ).mappings().all()
        return [
            {
                "id": int(row["id"]),
                "requirement_id": int(row["requirement_id"]),
                "parent_version_id": row["parent_version_id"],
                "version_no": int(row["version_no"]),
                "version_title": row["version_title"],
                "change_type": row["change_type"],
                "requirement_snapshot": row["requirement_snapshot"],
                "change_summary": row["change_summary"],
                "diff_payload": dict(row["diff_payload"] or {}),
                "created_by": row["created_by"],
                "reviewed_by": row["reviewed_by"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ]

    def trace_by_requirement_key(self, requirement_key: str) -> dict[str, object] | None:
        with SessionLocal() as session:
            master = session.execute(
                text(
                    """
                    SELECT id, requirement_key, requirement_name, final_requirement,
                           current_version, status, lock_version, created_at, updated_at
                    FROM requirement_master
                    WHERE requirement_key = :requirement_key
                    """
                ),
                {"requirement_key": requirement_key},
            ).mappings().first()
            if master is None:
                return None

            rows = session.execute(
                text(
                    """
                    SELECT v.id AS version_id, v.version_no, v.version_title, v.change_type,
                           v.requirement_snapshot, v.change_summary, v.diff_payload,
                           v.created_by, v.reviewed_by, v.created_at AS version_created_at,
                           s.id AS source_id, s.source_type, s.source_event_id,
                           s.requester_id, s.requester_name, s.original_text,
                           s.extracted_text, s.original_payload, s.metadata,
                           s.processing_status, s.submitted_at, vs.relation_type
                    FROM requirement_version v
                    LEFT JOIN requirement_version_source vs ON vs.version_id = v.id
                    LEFT JOIN requirement_source s ON s.id = vs.source_id
                    WHERE v.requirement_id = :requirement_id
                    ORDER BY v.version_no DESC, s.submitted_at DESC
                    """
                ),
                {"requirement_id": int(master["id"])},
            ).mappings().all()

        versions_by_id: dict[int, dict[str, object]] = {}
        for row in rows:
            version_id = int(row["version_id"])
            version = versions_by_id.setdefault(
                version_id,
                {
                    "version_id": version_id,
                    "version_no": int(row["version_no"]),
                    "version_title": row["version_title"],
                    "change_type": row["change_type"],
                    "requirement_snapshot": row["requirement_snapshot"],
                    "change_summary": row["change_summary"],
                    "diff_payload": dict(row["diff_payload"] or {}),
                    "created_by": row["created_by"],
                    "reviewed_by": row["reviewed_by"],
                    "created_at": row["version_created_at"].isoformat() if row["version_created_at"] else None,
                    "sources": [],
                },
            )
            if row["source_id"] is None:
                continue
            metadata = dict(row["metadata"] or {})
            version["sources"].append(
                {
                    "source_id": int(row["source_id"]),
                    "source_type": row["source_type"],
                    "source_event_id": row["source_event_id"],
                    "requester_id": row["requester_id"],
                    "requester_name": row["requester_name"],
                    "original_text": row["original_text"],
                    "extracted_text": row["extracted_text"],
                    "original_payload": dict(row["original_payload"] or {}),
                    "structured_requirement": metadata.get("extracted") or {},
                    "relation_type": row["relation_type"],
                    "processing_status": row["processing_status"],
                    "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
                }
            )

        return {
            "requirement": {
                "id": int(master["id"]),
                "requirement_key": master["requirement_key"],
                "requirement_name": master["requirement_name"],
                "final_requirement": master["final_requirement"],
                "current_version": int(master["current_version"]),
                "status": master["status"],
                "lock_version": int(master["lock_version"]),
                "created_at": master["created_at"].isoformat() if master["created_at"] else None,
                "updated_at": master["updated_at"].isoformat() if master["updated_at"] else None,
            },
            "versions": list(versions_by_id.values()),
        }


class DocumentAssetRepository:
    """Persistence for uploaded artefacts and chunked document RAG."""

    def save(
        self,
        *,
        file_name: str,
        content_type: str,
        storage_uri: str,
        checksum: str,
        size_bytes: int,
        source_type: str = "web",
        original_text: str | None = None,
        extracted_text: str | None = None,
        metadata: dict[str, object] | None = None,
        source_id: int | None = None,
    ) -> dict[str, object]:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO document_asset (
                        file_name, content_type, storage_uri, checksum, size_bytes,
                        source_type, source_id, original_text, extracted_text, metadata
                    ) VALUES (
                        :file_name, :content_type, :storage_uri, :checksum, :size_bytes,
                        :source_type, :source_id, :original_text, :extracted_text, CAST(:metadata AS JSONB)
                    )
                    RETURNING id, file_name, content_type, storage_uri, checksum, size_bytes,
                              source_type, source_id, original_text, extracted_text, metadata, created_at
                    """
                ),
                {
                    "file_name": file_name,
                    "content_type": content_type,
                    "storage_uri": storage_uri,
                    "checksum": checksum,
                    "size_bytes": size_bytes,
                    "source_type": source_type,
                    "source_id": source_id,
                    "original_text": original_text or "",
                    "extracted_text": extracted_text or "",
                    "metadata": json.dumps(metadata or {}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_asset_row(row)

    def list_documents(self, limit: int = 20) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, file_name, content_type, storage_uri, checksum, size_bytes,
                           source_type, source_id, original_text, extracted_text, metadata, created_at
                    FROM document_asset
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [self._normalize_asset_row(row) for row in rows]

    def get_document(self, document_id: int) -> dict[str, object] | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, file_name, content_type, storage_uri, checksum, size_bytes,
                           source_type, source_id, original_text, extracted_text, metadata, created_at
                    FROM document_asset
                    WHERE id = :document_id
                    """
                ),
                {"document_id": document_id},
            ).mappings().first()
        return self._normalize_asset_row(row) if row else None

    def add_chunks(self, document_id: int, text: str, *, chunk_size: int = 600, overlap: int = 80) -> list[dict[str, object]]:
        chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            return []
        with SessionLocal() as session:
            saved: list[dict[str, object]] = []
            for index, chunk in enumerate(chunks, start=1):
                embedding = self._embed_text(chunk)
                row = session.execute(
                    text(
                        """
                        INSERT INTO document_chunk (document_id, chunk_index, chunk_text, embedding, metadata)
                        VALUES (:document_id, :chunk_index, :chunk_text, :embedding, CAST(:metadata AS JSONB))
                        RETURNING id, document_id, chunk_index, chunk_text, metadata, created_at
                        """
                    ),
                    {
                        "document_id": document_id,
                        "chunk_index": index,
                        "chunk_text": chunk,
                        "embedding": embedding,
                        "metadata": json.dumps({"source": "fixed-slice"}),
                    },
                ).mappings().one()
                saved.append(self._normalize_chunk_row(row))
            session.commit()
        return saved

    def search_chunks(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        normalized = (query or "").strip()
        if not normalized:
            return []
        embedding = self._embed_text(normalized)
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT dc.id, dc.document_id, dc.chunk_index, dc.chunk_text, dc.metadata, dc.created_at,
                           da.file_name, da.storage_uri,
                           1 - (dc.embedding <=> :embedding::vector) AS score
                    FROM document_chunk dc
                    JOIN document_asset da ON da.id = dc.document_id
                    WHERE dc.embedding IS NOT NULL
                    ORDER BY dc.embedding <=> :embedding::vector
                    LIMIT :limit
                    """
                ),
                {"embedding": embedding, "limit": limit},
            ).mappings().all()
        return [self._normalize_chunk_search_row(row) for row in rows]

    def get_chunks(self, document_id: int, limit: int = 20) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, document_id, chunk_index, chunk_text, metadata, created_at
                    FROM document_chunk
                    WHERE document_id = :document_id
                    ORDER BY chunk_index ASC
                    LIMIT :limit
                    """
                ),
                {"document_id": document_id, "limit": limit},
            ).mappings().all()
        return [self._normalize_chunk_row(row) for row in rows]

    def _normalize_asset_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "file_name": row["file_name"],
            "content_type": row["content_type"],
            "storage_uri": row["storage_uri"],
            "checksum": row["checksum"],
            "size_bytes": int(row["size_bytes"]),
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "original_text": row["original_text"],
            "extracted_text": row["extracted_text"],
            "metadata": dict(row["metadata"] or {}),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }

    def _normalize_chunk_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "document_id": int(row["document_id"]),
            "chunk_index": int(row["chunk_index"]),
            "chunk_text": row["chunk_text"],
            "metadata": dict(row["metadata"] or {}),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }

    def _normalize_chunk_search_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "document_id": int(row["document_id"]),
            "chunk_index": int(row["chunk_index"]),
            "chunk_text": row["chunk_text"],
            "file_name": row["file_name"],
            "storage_uri": row["storage_uri"],
            "score": float(row["score"]),
            "metadata": dict(row["metadata"] or {}),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }

    def _chunk_text(self, text: str, *, chunk_size: int = 600, overlap: int = 80) -> list[str]:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if not normalized:
            return []
        chunks: list[str] = []
        start = 0
        size = max(80, int(chunk_size))
        step = max(20, int(overlap))
        while start < len(normalized):
            end = min(len(normalized), start + size)
            chunk = normalized[start:end].strip()
            if not chunk:
                break
            if end < len(normalized):
                last_space = chunk.rfind(" ")
                if last_space > int(size * 0.65):
                    end = start + last_space
                    chunk = normalized[start:end].strip()
            chunks.append(chunk)
            start = max(start + 1, end - step)
            if start >= len(normalized):
                break
        return [chunk for chunk in chunks if chunk]

    def _embed_text(self, text: str) -> list[float]:
        from src.infrastructure.embedding.embedding_service import EmbeddingService

        return EmbeddingService().embed(text)


class RequirementReviewRepository:
    """人工审核决策的持久化边界。"""

    def save(self, review: RequirementReview, session: Session | None = None) -> RequirementReview:
        owns_session = session is None
        session = session or SessionLocal()
        try:
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
            if owns_session:
                session.commit()
                session.close()
            return review
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise


class AuditRepository:
    """Persistence boundary for audit event records."""

    def record(self, event: AuditEvent, session: Session | None = None) -> AuditEvent:
        owns_session = session is None
        session = session or SessionLocal()
        try:
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
            if owns_session:
                session.commit()
                session.close()
            return event
        except Exception:
            if owns_session:
                session.rollback()
                session.close()
            raise

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

    def list_dicts(self, limit: int = 50) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT trace_id, event_type, aggregate_type, aggregate_id, actor_type,
                           actor_id, before_data, after_data, result_status, error_code, created_at
                    FROM audit_event
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [
            {
                "trace_id": row["trace_id"],
                "event_type": row["event_type"],
                "aggregate_type": row["aggregate_type"],
                "aggregate_id": row["aggregate_id"],
                "actor_type": row["actor_type"],
                "actor_id": row["actor_id"],
                "before_data": dict(row["before_data"] or {}),
                "after_data": dict(row["after_data"] or {}),
                "result_status": row["result_status"],
                "error_code": row["error_code"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ]


class ChatRepository:
    """Persistence for multi-session chat state and run tracking."""

    def create_conversation(
        self,
        *,
        actor_id: str = "api-user",
        title: str | None = None,
        summary: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_conversation (actor_id, title, summary, meta)
                    VALUES (:actor_id, :title, :summary, CAST(:meta AS JSONB))
                    RETURNING id, actor_id, title, summary, status, meta, created_at, updated_at
                    """
                ),
                {
                    "actor_id": actor_id,
                    "title": (title or "新对话").strip() or "新对话",
                    "summary": summary,
                    "meta": json.dumps(meta or {}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_conversation_row(row)

    def get_conversation(self, conversation_id: str, actor_id: str = "api-user") -> dict[str, object] | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, actor_id, title, summary, status, meta, created_at, updated_at
                    FROM agent_conversation
                    WHERE id = CAST(:conversation_id AS UUID) AND actor_id = :actor_id
                    """
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().first()
        return self._normalize_conversation_row(row) if row else None

    def list_conversations(self, actor_id: str = "api-user", limit: int = 20, offset: int = 0) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT c.id, c.actor_id, c.title, c.summary, c.status, c.meta, c.created_at, c.updated_at,
                           COUNT(m.id) AS message_count
                    FROM agent_conversation c
                    LEFT JOIN agent_message m ON m.conversation_id = c.id
                    WHERE c.actor_id = :actor_id
                    GROUP BY c.id, c.actor_id, c.title, c.summary, c.status, c.meta, c.created_at, c.updated_at
                    ORDER BY c.updated_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"actor_id": actor_id, "limit": limit, "offset": offset},
            ).mappings().all()
        return [self._normalize_conversation_row(row, message_count=row["message_count"]) for row in rows]

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        status: str | None = None,
        meta: dict[str, object] | None = None,
        actor_id: str = "api-user",
    ) -> dict[str, object] | None:
        fields: list[str] = []
        values: dict[str, object] = {"conversation_id": conversation_id, "actor_id": actor_id}
        if title is not None:
            fields.append("title = :title")
            values["title"] = title.strip() or "新对话"
        if summary is not None:
            fields.append("summary = :summary")
            values["summary"] = summary
        if status is not None:
            fields.append("status = :status")
            values["status"] = status
        if meta is not None:
            fields.append("meta = CAST(:meta AS JSONB)")
            values["meta"] = json.dumps(meta)
        if not fields:
            return self.get_conversation(conversation_id, actor_id=actor_id)
        sql = "UPDATE agent_conversation SET " + ", ".join(fields) + ", updated_at = NOW() WHERE id = CAST(:conversation_id AS UUID) AND actor_id = :actor_id RETURNING id, actor_id, title, summary, status, meta, created_at, updated_at"
        with SessionLocal() as session:
            row = session.execute(text(sql), values).mappings().first()
            session.commit()
        return self._normalize_conversation_row(row) if row else None

    def delete_conversation(self, conversation_id: str, actor_id: str = "api-user") -> bool:
        with SessionLocal() as session:
            result = session.execute(
                text(
                    "DELETE FROM agent_conversation WHERE id = CAST(:conversation_id AS UUID) AND actor_id = :actor_id"
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            )
            session.commit()
        return result.rowcount > 0

    def get_messages(self, conversation_id: str) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    FROM agent_message
                    WHERE conversation_id = CAST(:conversation_id AS UUID)
                    ORDER BY created_at ASC
                    """
                ),
                {"conversation_id": conversation_id},
            ).mappings().all()
        return [self._normalize_message_row(row) for row in rows]

    def upsert_user_message(
        self,
        *,
        conversation_id: str,
        content: str,
        actor_id: str = "api-user",
        client_message_id: str | None = None,
    ) -> dict[str, object]:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_message (conversation_id, role, content, client_message_id, meta)
                    VALUES (CAST(:conversation_id AS UUID), 'user', :content, :client_message_id, CAST(:meta AS JSONB))
                    ON CONFLICT (conversation_id, client_message_id) WHERE client_message_id IS NOT NULL DO UPDATE SET
                        content = EXCLUDED.content,
                        meta = EXCLUDED.meta
                    RETURNING id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "content": content,
                    "client_message_id": client_message_id,
                    "meta": json.dumps({"actor_id": actor_id}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_message_row(row)

    def create_run(
        self,
        *,
        conversation_id: str,
        client_message_id: str | None,
        status: str = "running",
        error: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_run (conversation_id, client_message_id, status, error, meta)
                    VALUES (CAST(:conversation_id AS UUID), :client_message_id, :status, :error, CAST(:meta AS JSONB))
                    ON CONFLICT (conversation_id, client_message_id) WHERE client_message_id IS NOT NULL DO UPDATE SET
                        status = EXCLUDED.status,
                        error = EXCLUDED.error,
                        meta = EXCLUDED.meta,
                        updated_at = NOW()
                    RETURNING id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "client_message_id": client_message_id,
                    "status": status,
                    "error": error,
                    "meta": json.dumps(meta or {}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_run_row(row)

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        error: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    UPDATE agent_run
                    SET status = :status,
                        error = :error,
                        meta = CAST(:meta AS JSONB),
                        updated_at = NOW()
                    WHERE run_id = CAST(:run_id AS UUID)
                    RETURNING id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    """
                ),
                {"run_id": run_id, "status": status, "error": error, "meta": json.dumps(meta or {})},
            ).mappings().first()
            session.commit()
        return self._normalize_run_row(row) if row else None

    def get_run(self, run_id: str) -> dict[str, object] | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    FROM agent_run
                    WHERE run_id = CAST(:run_id AS UUID)
                    """
                ),
                {"run_id": run_id},
            ).mappings().first()
        return self._normalize_run_row(row) if row else None

    def append_assistant_message(
        self,
        *,
        conversation_id: str,
        content: str,
        artifacts: dict[str, object] | None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO agent_message (conversation_id, role, content, artifacts, run_id, meta)
                    VALUES (CAST(:conversation_id AS UUID), 'assistant', :content, CAST(:artifacts AS JSONB), CAST(:run_id AS UUID), CAST(:meta AS JSONB))
                    RETURNING id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "content": content,
                    "artifacts": json.dumps(artifacts or {}),
                    "run_id": run_id,
                    "meta": json.dumps({"source": "agent"}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_message_row(row)

    def _normalize_conversation_row(self, row: dict[str, object] | None, *, message_count: int | None = None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "actor_id": row["actor_id"],
            "title": row["title"],
            "summary": row["summary"],
            "status": row["status"],
            "meta": dict(row["meta"] or {}),
            "message_count": message_count if message_count is not None else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }

    def _normalize_message_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "conversation_id": str(row["conversation_id"]),
            "role": row["role"],
            "content": row["content"],
            "artifacts": dict(row["artifacts"] or {}) if row.get("artifacts") is not None else None,
            "client_message_id": row["client_message_id"],
            "run_id": str(row["run_id"]) if row.get("run_id") is not None else None,
            "meta": dict(row["meta"] or {}),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }

    def _normalize_run_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "run_id": str(row["run_id"]),
            "conversation_id": str(row["conversation_id"]) if row.get("conversation_id") is not None else None,
            "client_message_id": row["client_message_id"],
            "status": row["status"],
            "error": row["error"],
            "meta": dict(row["meta"] or {}),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }


class MemoryRepository:
    """Long-term memory persistence for actor-scoped, recallable memory notes."""

    def insert_memory(
        self,
        *,
        actor_id: str,
        kind: str,
        content: str,
        source_conversation_id: str | None = None,
        source_message_id: int | None = None,
        ref_requirement_key: str | None = None,
        importance: int = 1,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO memory_note (actor_id, kind, content, source_conversation_id, source_message_id, ref_requirement_key, importance, meta)
                    VALUES (:actor_id, :kind, :content, CAST(:source_conversation_id AS UUID), :source_message_id, :ref_requirement_key, :importance, CAST(:meta AS JSONB))
                    RETURNING id, actor_id, kind, status, active, content, source_conversation_id, source_message_id, ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    """
                ),
                {
                    "actor_id": actor_id,
                    "kind": kind,
                    "content": content,
                    "source_conversation_id": source_conversation_id,
                    "source_message_id": source_message_id,
                    "ref_requirement_key": ref_requirement_key,
                    "importance": importance,
                    "meta": json.dumps(meta or {}),
                },
            ).mappings().one()
            session.commit()
        return self._normalize_memory_row(row)

    def list_memories(self, actor_id: str = "api-user", limit: int = 20) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, actor_id, kind, status, active, content, source_conversation_id, source_message_id,
                           ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    FROM memory_note
                    WHERE actor_id = :actor_id AND status != 'deleted'
                    ORDER BY importance DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "limit": limit},
            ).mappings().all()
        return [self._normalize_memory_row(row) for row in rows]

    def recall(self, actor_id: str, query: str, limit: int = 4) -> list[dict[str, object]]:
        q = (query or "").strip()
        if not q:
            return []
        like = f"%{q}%"
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, actor_id, kind, status, active, content, source_conversation_id, source_message_id,
                           ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    FROM memory_note
                    WHERE actor_id = :actor_id AND status = 'active' AND content ILIKE :query
                    ORDER BY importance DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "query": like, "limit": limit},
            ).mappings().all()
        return [self._normalize_memory_row(row) for row in rows]

    def update_status(self, memory_id: int, status: str) -> dict[str, object] | None:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    UPDATE memory_note
                    SET status = :status, active = (:status = 'active'), updated_at = NOW()
                    WHERE id = :memory_id
                    RETURNING id, actor_id, kind, status, active, content, source_conversation_id, source_message_id, ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    """
                ),
                {"memory_id": memory_id, "status": status},
            ).mappings().first()
            session.commit()
        return self._normalize_memory_row(row) if row else None

    def _normalize_memory_row(self, row: dict[str, object] | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "actor_id": row["actor_id"],
            "kind": row["kind"],
            "status": row["status"],
            "active": bool(row["active"]),
            "content": row["content"],
            "source_conversation_id": str(row["source_conversation_id"]) if row.get("source_conversation_id") is not None else None,
            "source_message_id": row["source_message_id"],
            "ref_requirement_key": row["ref_requirement_key"],
            "superseded_by": row["superseded_by"],
            "importance": int(row["importance"]),
            "meta": dict(row["meta"] or {}),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
