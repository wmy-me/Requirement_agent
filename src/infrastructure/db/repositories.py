"""Repository interfaces and database-backed implementations for business data.

文件总览（按类）：
- RequirementSourceRepository / MasterRepository / VersionRepository：
  需求来源、主需求、版本与溯源的基本读写。
- DocumentAssetRepository：上传文档 asset + 分块 + 向量搜索。
- RequirementReviewRepository：人工审核记录。
- RequirementFeatureRepository：功能条目（feature）级版本治理、diff 与检索。
- AuditRepository：审计事件留痕。
- ChatRepository：对话/消息/Agent run（含 client_message_id 幂等与 run 状态机）。
- MemoryRepository：跨会话长期记忆（actor 隔离、superseded/deleted 治理、向量召回）。

通用约定：
- 所有方法接受可选 `session`。**未传入时自建 session 并负责提交/回滚/关闭；**
  传入时把事务所有权交给调用方（不 commit/close），供 LangGraph 决策图等原子流程复用。
- 需要幂等的写入都以“业务唯一键 + ON CONFLICT（与部分唯一索引对齐的 WHERE 谓词）”
  实现，避免重试产生重复数据。
"""

from __future__ import annotations

import json
import re
import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.common.time import as_display_iso
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
        """写入/更新一条需求来源。

        幂等：以 idempotency_key 唯一（ON CONFLICT 更新），重复提交返回同一条来源，
        不重复入队分析。session 所有权：未传入时本方法自建并负责提交/回滚/关闭；
        传入 session 时由调用方负责事务。
        """
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
        """推进来源处理状态（received→…→pending_review→committed/rejected 等）。

        可携带 metadata 一并覆盖（如分析结果、溯源 trace）。
        """
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
        """回填规整/清洗后的文本与分段元数据，并把来源标记为 extracting。"""
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
        """按主键取来源对象；不存在返回 None。"""
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
        """按幂等键查来源，用于判断“是否已处理过”，避免重复分析。"""
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
        """按处理状态列出来源（如 pending_review 待审核队列），最近更新优先。"""
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
                "submitted_at": as_display_iso(row["submitted_at"]),
                "updated_at": as_display_iso(row["updated_at"]),
            }
            for row in rows
        ]

    def get_detail(self, source_id: int) -> dict[str, object] | None:
        """取来源详情（含 metadata/analysis 等），供审核面板与溯源展示。"""
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
            "submitted_at": as_display_iso(row["submitted_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }

    def get_trace(self, source_id: int) -> dict[str, object] | None:
        """组装来源一级溯源：来源详情 + 文档规整/结构化提取/分析/风险 + 关联 REQ 映射。"""
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
                    "version_created_at": as_display_iso(row["version_created_at"]),
                }
                for row in rows
            ],
        }


class RequirementMasterRepository:
    """规范化主需求的持久化边界。"""

    def save(self, requirement: RequirementMaster, session: Session | None = None) -> RequirementMaster:
        """写入/更新主需求（REQ 主体、当前版本、乐观锁版本）。

        乐观锁：调用方在并发合并时应基于 lock_version 校验，冲突需人工重审。
        """
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
        """按主键取主需求对象；不存在返回 None。"""
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
        """从序列分配下一个 REQ 编号（REQ-{6 位}），保证全局唯一。"""
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
        """按 REQ 编号取主需求对象；不存在返回 None。"""
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
        """列出最近更新的主需求（最多 50 条），返回 RequirementMaster 列表。"""
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
        """聚合主需求 + 来源上下文（领域/来源渠道/输入人/密级/最新提交时间/功能数），供列表与检索使用。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT m.id, m.requirement_key, m.requirement_name, m.final_requirement,
                           m.current_version, m.status, m.lock_version,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT s.source_type), NULL) AS source_types,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(s.requester_name, s.requester_id)), NULL) AS requester_names,
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
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT f.content), NULL) AS feature_contents,
                           COUNT(DISTINCT CASE WHEN f.status = 'active' THEN f.id END) AS feature_count,
                           MIN(s.submitted_at) AS first_source_submitted_at,
                           MAX(s.submitted_at) AS latest_source_submitted_at
                    FROM requirement_master m
                    LEFT JOIN requirement_version v ON v.requirement_id = m.id
                    LEFT JOIN requirement_version_source vs ON vs.version_id = v.id
                    LEFT JOIN requirement_source s ON s.id = vs.source_id
                    LEFT JOIN requirement_feature f ON f.requirement_id = m.id
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
                "requester_names": list(row["requester_names"] or []),
                "departments": list(row["departments"] or []),
                "business_domains": list(row["business_domains"] or []),
                "sensitivity_levels": list(row["sensitivity_levels"] or []),
                "feature_contents": list(row["feature_contents"] or []),
                "feature_count": int(row["feature_count"] or 0),
                "first_source_submitted_at": as_display_iso(row["first_source_submitted_at"]),
                "latest_source_submitted_at": as_display_iso(row["latest_source_submitted_at"]),
            }
            for row in rows
        ]

    def search(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        """按名称/最终需求内容模糊搜索主需求，返回精简字段列表。"""
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
        """写入一条需求版本快照，回填自增 id 与 requirement_id。"""
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    INSERT INTO requirement_version (
                        requirement_id, parent_version_id, version_no, version_title, change_type,
                        requirement_snapshot, change_summary, diff_payload, created_by, reviewed_by, feature_changes, parent_version_no
                    ) VALUES (
                        :requirement_id, :parent_version_id, :version_no, :version_title, :change_type,
                        :requirement_snapshot, :change_summary, :diff_payload, :created_by, :reviewed_by, :feature_changes, :parent_version_no
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
                    "feature_changes": json.dumps(getattr(version, "feature_changes", []) or []),
                    "parent_version_no": getattr(version, "parent_version_no", None),
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
        """记录“版本 ← 来源”关联（relation_type='source'），幂等（重复关联忽略）。"""
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
        """按主需求 id 列出全部版本快照（版本号倒序），返回 RequirementVersion 列表。"""
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
        """取某主需求的最新版本快照；不存在返回 None。"""
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
            parent_version_no=row.get("parent_version_no"),
            version_no=int(row["version_no"]),
            version_title=str(row["version_title"]),
            change_type=str(row["change_type"]),
            requirement_snapshot=str(row["requirement_snapshot"]),
            change_summary=str(row["change_summary"]),
            diff_payload=dict(row["diff_payload"] or {}),
            feature_changes=list(row.get("feature_changes") or []),
            created_by=str(row["created_by"]),
            reviewed_by=str(row["reviewed_by"]),
        )

    def list_by_requirement_key(self, requirement_key: str) -> list[dict[str, object]]:
        """按 REQ 编号列出版本历史（含 diff_payload、feature_changes），供版本时间线展示。"""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT v.id, v.requirement_id, v.parent_version_id, v.parent_version_no, v.version_no,
                           v.version_title, v.change_type, v.requirement_snapshot,
                           v.change_summary, v.diff_payload, v.feature_changes, v.created_by, v.reviewed_by,
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
                "parent_version_no": row["parent_version_no"],
                "version_no": int(row["version_no"]),
                "version_title": row["version_title"],
                "change_type": row["change_type"],
                "requirement_snapshot": row["requirement_snapshot"],
                "change_summary": row["change_summary"],
                "diff_payload": dict(row["diff_payload"] or {}),
                "feature_changes": list(row["feature_changes"] or []),
                "created_by": row["created_by"],
                "reviewed_by": row["reviewed_by"],
                "created_at": as_display_iso(row["created_at"]),
            }
            for row in rows
        ]

    def trace_by_requirement_key(self, requirement_key: str) -> dict[str, object] | None:
        """按 REQ 编号做版本级溯源：需求主体 + 每个版本的快照/来源链条。"""
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
                    SELECT v.id AS version_id, v.version_no, v.parent_version_no, v.version_title, v.change_type,
                           v.requirement_snapshot, v.change_summary, v.diff_payload, v.feature_changes,
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
                    "parent_version_no": row["parent_version_no"],
                    "version_title": row["version_title"],
                    "change_type": row["change_type"],
                    "requirement_snapshot": row["requirement_snapshot"],
                    "change_summary": row["change_summary"],
                    "diff_payload": dict(row["diff_payload"] or {}),
                    "feature_changes": list(row["feature_changes"] or []),
                    "created_by": row["created_by"],
                    "reviewed_by": row["reviewed_by"],
                    "created_at": as_display_iso(row["version_created_at"]),
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
                    "submitted_at": as_display_iso(row["submitted_at"]),
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
                "created_at": as_display_iso(master["created_at"]),
                "updated_at": as_display_iso(master["updated_at"]),
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
        """落库一条上传文档 asset（含校验字段），返回规范化后的 asset 行。"""
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
        """按创建时间倒序列出上传文档 asset，返回规范化行列表。"""
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
        """按 id 取上传文档 asset；不存在返回 None。"""
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

    def add_chunks(self, document_id: int, content: str, *, chunk_size: int = 600, overlap: int = 80) -> list[dict[str, object]]:
        """将文本固定切片后逐块写入 document_chunk（含向量），返回保存的分块列表。"""
        chunks = self._chunk_text(content, chunk_size=chunk_size, overlap=overlap)
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
        """按向量相似度检索文档分块，返回带文档信息与相似度分数的候选列表。"""
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
        """按文档 id 列出全部分块（chunk_index 升序），返回规范化行列表。"""
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
            "created_at": as_display_iso(row["created_at"]),
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
            "created_at": as_display_iso(row["created_at"]),
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
            "created_at": as_display_iso(row["created_at"]),
        }

    def _chunk_text(self, content: str, *, chunk_size: int = 600, overlap: int = 80) -> list[str]:
        """把文本切成带重叠的固定大小分块（供向量检索）。

        采用标准滑动窗口：每块最长 `chunk_size`，窗口每次前进 `chunk_size - overlap`
        （即相邻块重叠 `overlap` 字符），避免「逐字符偏移」产生海量冗余块。
        若某块在 65% 之后遇到空格，则回退到该空格处断句，保证块边界尽量完整。
        """
        normalized = re.sub(r"\s+", " ", (content or "").strip())
        if not normalized:
            return []
        chunks: list[str] = []
        size = max(80, int(chunk_size))
        step = max(20, int(size - max(0, int(overlap))))
        start = 0
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
            if end >= len(normalized):
                break
            start += step
        return [chunk for chunk in chunks if chunk]

    def _embed_text(self, text: str) -> list[float]:
        from src.infrastructure.embedding.embedding_service import EmbeddingService

        return EmbeddingService().embed(text)


class RequirementReviewRepository:
    """人工审核决策的持久化边界。"""

    def save(self, review: RequirementReview, session: Session | None = None) -> RequirementReview:
        """写入一条人工审核记录（分析快照/决策/审阅人/编辑后需求）。"""
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


class RequirementFeatureRepository:
    """功能条目（feature）级版本治理。

    一条 REQ 的最终描述 = 其 active features 的有序拼接；每个 feature 记录来源
    （origin_source_id / origin_requirement_key / origin_version_no）、生效区间
    （removed_version_no）与变更史（provenance），从而支持“最终描述 + 行级溯源 + 版本 diff”。
    """

    def list_active(self, requirement_id: int, *, session: Session | None = None) -> list[dict[str, object]]:
        """列出某 REQ 当前生效的 feature（按 ordinal 排序），是“最终描述”的事实来源。"""
        owns_session = session is None
        session = session or SessionLocal()
        rows = session.execute(
            text(
                """
                SELECT id, requirement_id, feature_key, content, status, ordinal,
                       origin_source_id, origin_requirement_key, origin_version_no, removed_version_no, provenance
                FROM requirement_feature
                WHERE requirement_id = :requirement_id AND status = 'active'
                ORDER BY ordinal ASC, id ASC
                """
            ),
            {"requirement_id": requirement_id},
        ).mappings().all()
        if owns_session:
            session.close()
        return [self._row_to_feature(row) for row in rows]

    def create_features(
        self,
        requirement_id: int,
        features: list[str],
        *,
        source_id: int | None,
        requirement_key: str,
        version_no: int,
        session: Session,
    ) -> list[dict[str, object]]:
        """新建 REQ 时把来源的功能行整体落为 features（每个新 feature 记 provenance=add）。"""
        created: list[dict[str, object]] = []
        ordinal = 1
        for content in [item.strip() for item in features if item and item.strip()]:
            feature_key = self._next_feature_key(requirement_id, ordinal=ordinal, version_no=version_no, session=session)
            provenance = [
                {
                    "version_no": version_no,
                    "source_id": source_id,
                    "requirement_key": requirement_key,
                    "kind": "add",
                }
            ]
            row = session.execute(
                text(
                    """
                    INSERT INTO requirement_feature (
                        requirement_id, feature_key, content, status, ordinal,
                        origin_source_id, origin_requirement_key, origin_version_no, provenance, content_hash
                    ) VALUES (
                        :requirement_id, :feature_key, :content, 'active', :ordinal,
                        :origin_source_id, :origin_requirement_key, :origin_version_no, CAST(:provenance AS JSONB), :content_hash
                    )
                    RETURNING id, requirement_id, feature_key, content, status, ordinal,
                              origin_source_id, origin_requirement_key, origin_version_no, removed_version_no, provenance
                    """
                ),
                {
                    "requirement_id": requirement_id,
                    "feature_key": feature_key,
                    "content": content,
                    "ordinal": ordinal,
                    "origin_source_id": source_id,
                    "origin_requirement_key": requirement_key,
                    "origin_version_no": version_no,
                    "provenance": json.dumps(provenance),
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                },
            ).mappings().one()
            created.append(self._row_to_feature(row))
            ordinal += 1
        return created

    def sync_features(
        self,
        requirement_id: int,
        features: list[str],
        *,
        source_id: int | None,
        requirement_key: str,
        version_no: int,
        session: Session,
    ) -> list[dict[str, object]]:
        """无人工 overrides 时，按“当前来源功能行 vs 现有 active features”做保守同步。

        同 ordinal 内容不变→keep；变化→modify；新行→add；现有行在新来源中被移除→delete。
        返回本版本的 feature 变更清单，供 diff 与审计使用。
        """
        normalized = [item.strip() for item in features if item and item.strip()]
        existing = self.list_active(requirement_id, session=session)
        by_ordinal = {int(item["ordinal"]): item for item in existing}
        changes: list[dict[str, object]] = []

        for ordinal, content in enumerate(normalized, start=1):
            current = by_ordinal.get(ordinal)
            if current is None:
                feature_key = self._next_feature_key(
                    requirement_id,
                    ordinal=ordinal,
                    version_no=version_no,
                    session=session,
                )
                provenance = [
                    {
                        "version_no": version_no,
                        "source_id": source_id,
                        "requirement_key": requirement_key,
                        "kind": "add",
                    }
                ]
                session.execute(
                    text(
                        """
                        INSERT INTO requirement_feature (
                            requirement_id, feature_key, content, status, ordinal,
                            origin_source_id, origin_requirement_key, origin_version_no, provenance, content_hash
                        ) VALUES (
                            :requirement_id, :feature_key, :content, 'active', :ordinal,
                            :origin_source_id, :origin_requirement_key, :origin_version_no, CAST(:provenance AS JSONB), :content_hash
                        )
                        """
                    ),
                    {
                        "requirement_id": requirement_id,
                        "feature_key": feature_key,
                        "content": content,
                        "ordinal": ordinal,
                        "origin_source_id": source_id,
                        "origin_requirement_key": requirement_key,
                        "origin_version_no": version_no,
                        "provenance": json.dumps(provenance),
                        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    },
                )
                changes.append({"op": "add", "feature_key": feature_key, "content": content})
                continue

            provenance = list(current.get("provenance") or [])
            if str(current.get("content") or "").strip() != content:
                provenance.append(
                    {
                        "version_no": version_no,
                        "source_id": source_id,
                        "requirement_key": requirement_key,
                        "kind": "modify",
                    }
                )
                session.execute(
                    text(
                        """
                        UPDATE requirement_feature
                        SET content = :content,
                            status = 'active',
                            removed_version_no = NULL,
                            provenance = CAST(:provenance AS JSONB),
                            content_hash = :content_hash,
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": current["id"],
                        "content": content,
                        "provenance": json.dumps(provenance),
                        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    },
                )
                changes.append(
                    {
                        "op": "modify",
                        "feature_key": current["feature_key"],
                        "before": current["content"],
                        "after": content,
                    }
                )
            else:
                session.execute(
                    text(
                        """
                        UPDATE requirement_feature
                        SET ordinal = :ordinal,
                            status = 'active',
                            removed_version_no = NULL,
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"id": current["id"], "ordinal": ordinal},
                )

        for leftover in existing:
            if int(leftover["ordinal"]) <= len(normalized):
                continue
            provenance = list(leftover.get("provenance") or [])
            provenance.append(
                {
                    "version_no": version_no,
                    "source_id": source_id,
                    "requirement_key": requirement_key,
                    "kind": "delete",
                }
            )
            session.execute(
                text(
                    """
                    UPDATE requirement_feature
                    SET status = 'deleted',
                        removed_version_no = :removed_version_no,
                        provenance = CAST(:provenance AS JSONB),
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": leftover["id"],
                    "removed_version_no": version_no,
                    "provenance": json.dumps(provenance),
                },
            )
            changes.append(
                {
                    "op": "delete",
                    "feature_key": leftover["feature_key"],
                    "content": leftover["content"],
                }
            )

        return changes

    def join_active_features(self, requirement_id: int, *, session: Session | None = None) -> str:
        """把某 REQ 当前生效的 feature 内容按 ordinal 顺序拼接成“最终描述”文本。"""
        features = self.list_active(requirement_id, session=session)
        lines = [item["content"] for item in features if str(item.get("content") or "").strip()]
        return "\n".join(lines).strip()

    def apply_overrides(
        self,
        requirement_id: int,
        overrides: list[dict[str, object]],
        *,
        source_id: int | None,
        requirement_key: str,
        version_no: int,
        session: Session,
    ) -> list[dict[str, object]]:
        """合并模式：按人工给出的 add/modify/delete/keep 逐条裁决 feature 变更。

        keep 只保留现状；add 新增行；modify 改内容并记 provenance；delete 软删并写 removed_version_no。
        用于审核时人工明确处理“与既有 feature 冲突/修改既有行”的场景。
        """
        current_items = self.list_active(requirement_id, session=session)
        current_by_key = {str(item["feature_key"]): item for item in current_items}
        changes: list[dict[str, object]] = []
        max_ordinal = max((int(item["ordinal"]) for item in current_items), default=0)

        for raw in overrides:
            feature_key = str(raw.get("feature_key") or "").strip()
            op = str(raw.get("op") or "keep").strip().lower()
            content = str(raw.get("content") or "").strip()
            current = current_by_key.get(feature_key) if feature_key else None

            if op == "keep":
                continue
            if op == "add":
                max_ordinal += 1
                next_key = feature_key or self._next_feature_key(
                    requirement_id,
                    ordinal=max_ordinal,
                    version_no=version_no,
                    session=session,
                )
                provenance = [
                    {
                        "version_no": version_no,
                        "source_id": source_id,
                        "requirement_key": requirement_key,
                        "kind": "add",
                    }
                ]
                session.execute(
                    text(
                        """
                        INSERT INTO requirement_feature (
                            requirement_id, feature_key, content, status, ordinal,
                            origin_source_id, origin_requirement_key, origin_version_no, provenance, content_hash
                        ) VALUES (
                            :requirement_id, :feature_key, :content, 'active', :ordinal,
                            :origin_source_id, :origin_requirement_key, :origin_version_no, CAST(:provenance AS JSONB), :content_hash
                        )
                        """
                    ),
                    {
                        "requirement_id": requirement_id,
                        "feature_key": next_key,
                        "content": content,
                        "ordinal": max_ordinal,
                        "origin_source_id": source_id,
                        "origin_requirement_key": requirement_key,
                        "origin_version_no": version_no,
                        "provenance": json.dumps(provenance),
                        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    },
                )
                changes.append({"op": "add", "feature_key": next_key, "content": content})
                continue

            if current is None:
                raise ValueError(f"feature_key={feature_key} not found for override")

            provenance = list(current.get("provenance") or [])
            provenance.append(
                {
                    "version_no": version_no,
                    "source_id": source_id,
                    "requirement_key": requirement_key,
                    "kind": op,
                }
            )
            if op == "modify":
                if not content:
                    raise ValueError(f"feature_key={feature_key} modify requires content")
                session.execute(
                    text(
                        """
                        UPDATE requirement_feature
                        SET content = :content,
                            provenance = CAST(:provenance AS JSONB),
                            content_hash = :content_hash,
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": current["id"],
                        "content": content,
                        "provenance": json.dumps(provenance),
                        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    },
                )
                changes.append(
                    {
                        "op": "modify",
                        "feature_key": feature_key,
                        "before": current["content"],
                        "after": content,
                    }
                )
                continue

            if op == "delete":
                session.execute(
                    text(
                        """
                        UPDATE requirement_feature
                        SET status = 'deleted',
                            removed_version_no = :removed_version_no,
                            provenance = CAST(:provenance AS JSONB),
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": current["id"],
                        "removed_version_no": version_no,
                        "provenance": json.dumps(provenance),
                    },
                )
                changes.append({"op": "delete", "feature_key": feature_key, "content": current["content"]})
                continue

            raise ValueError(f"unsupported feature override op={op}")

        return changes

    def list_by_requirement_key(
        self,
        requirement_key: str,
        *,
        at_version: int | None = None,
        include_deleted: bool = False,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        """按 REQ 编号列 feature，支持指定版本生效区间或包含已删除项。"""
        owns_session = session is None
        session = session or SessionLocal()
        if at_version is None and not include_deleted:
            status_clause = "AND f.status = 'active'"
            version_clause = ""
            values: dict[str, object] = {"requirement_key": requirement_key}
        else:
            status_clause = ""
            version_clause = """
                AND f.origin_version_no <= :at_version
                AND (f.removed_version_no IS NULL OR f.removed_version_no > :at_version)
            """ if at_version is not None else ""
            values = {"requirement_key": requirement_key, "at_version": at_version}
            if include_deleted:
                version_clause = ""
        rows = session.execute(
            text(
                """
                SELECT f.id, f.requirement_id, f.feature_key, f.content, f.status, f.ordinal,
                       f.origin_source_id, f.origin_requirement_key, f.origin_version_no, f.removed_version_no, f.provenance
                FROM requirement_feature f
                JOIN requirement_master m ON m.id = f.requirement_id
                WHERE m.requirement_key = :requirement_key
                """ + status_clause + version_clause + """
                ORDER BY f.ordinal ASC, f.id ASC
                """
            ),
            values,
        ).mappings().all()
        if owns_session:
            session.close()
        return [self._row_to_feature(row) for row in rows]

    def diff_by_requirement_key(
        self,
        requirement_key: str,
        *,
        from_version: int | None,
        to_version: int | None,
        session: Session | None = None,
    ) -> dict[str, object]:
        """按版本区间计算 feature 级 diff（added/removed/modified/unchanged），供版本对比展示。

        to_version 为空时取当前版本；from_version 为空时取上一版本。
        """
        owns_session = session is None
        session = session or SessionLocal()
        try:
            if to_version is None:
                current = session.execute(
                    text(
                        """
                        SELECT current_version
                        FROM requirement_master
                        WHERE requirement_key = :requirement_key
                        """
                    ),
                    {"requirement_key": requirement_key},
                ).scalar_one_or_none()
                if current is None:
                    raise ValueError("requirement not found")
                to_version = int(current)
            if from_version is None:
                from_version = max(1, int(to_version) - 1)

            left = self.list_by_requirement_key(requirement_key, at_version=from_version, session=session)
            right = self.list_by_requirement_key(requirement_key, at_version=to_version, session=session)
            left_by_key = {str(item["feature_key"]): item for item in left}
            right_by_key = {str(item["feature_key"]): item for item in right}

            added = [item for key, item in right_by_key.items() if key not in left_by_key]
            removed = [item for key, item in left_by_key.items() if key not in right_by_key]
            modified = []
            unchanged = 0
            for key, after in right_by_key.items():
                before = left_by_key.get(key)
                if before is None:
                    continue
                if str(before["content"]) != str(after["content"]):
                    modified.append(
                        {
                            "feature_key": key,
                            "before": before["content"],
                            "after": after["content"],
                        }
                    )
                else:
                    unchanged += 1
            return {
                "requirement_key": requirement_key,
                "from_version": from_version,
                "to_version": to_version,
                "added": added,
                "removed": removed,
                "modified": modified,
                "unchanged": unchanged,
            }
        finally:
            if owns_session:
                session.close()

    def search_features(
        self,
        query: str,
        *,
        status: str | None = None,
        requester: str | None = None,
        has_version_ge: int | None = None,
        limit: int = 20,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        """feature 行级检索：按功能内容匹配，支持状态/输入人/版本号下限筛选。

        返回精确到“哪条功能在哪个 REQ 的哪个版本”，供功能溯源与表格明细使用。
        """
        owns_session = session is None
        session = session or SessionLocal()
        values: dict[str, object] = {
            "query": f"%{query.strip()}%",
            "limit": limit,
        }
        clauses = ["f.content ILIKE :query"]
        if status:
            clauses.append("f.status = :status")
            values["status"] = status
        if requester:
            clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM requirement_version v2
                    JOIN requirement_version_source vs2 ON vs2.version_id = v2.id
                    JOIN requirement_source s2 ON s2.id = vs2.source_id
                    WHERE v2.requirement_id = m.id
                      AND (s2.requester_name = :requester OR s2.requester_id = :requester)
                )
                """
            )
            values["requester"] = requester
        if has_version_ge is not None:
            clauses.append("m.current_version >= :has_version_ge")
            values["has_version_ge"] = has_version_ge
        rows = session.execute(
            text(
                """
                SELECT f.id, f.feature_key, f.content, f.status, f.ordinal, f.origin_source_id,
                       f.origin_requirement_key, f.origin_version_no, f.removed_version_no, f.provenance,
                       m.requirement_key, m.requirement_name, m.current_version, m.status AS requirement_status
                FROM requirement_feature f
                JOIN requirement_master m ON m.id = f.requirement_id
                WHERE """ + " AND ".join(clauses) + """
                ORDER BY m.updated_at DESC, f.ordinal ASC
                LIMIT :limit
                """
            ),
            values,
        ).mappings().all()
        if owns_session:
            session.close()
        return [
            {
                **self._row_to_feature(row),
                "requirement_key": row["requirement_key"],
                "requirement_name": row["requirement_name"],
                "current_version": int(row["current_version"]),
                "requirement_status": row["requirement_status"],
            }
            for row in rows
        ]

    def _next_feature_key(self, requirement_id: int, *, ordinal: int, version_no: int, session: Session) -> str:
        base = f"F-{ordinal:03d}"
        exists = session.execute(
            text(
                """
                SELECT 1
                FROM requirement_feature
                WHERE requirement_id = :requirement_id AND feature_key = :feature_key
                """
            ),
            {"requirement_id": requirement_id, "feature_key": base},
        ).first()
        if not exists:
            return base
        return f"F-{version_no:03d}-{ordinal:03d}"

    @staticmethod
    def _row_to_feature(row: dict[str, object]) -> dict[str, object]:
        return {
            "id": int(row["id"]),
            "requirement_id": int(row["requirement_id"]),
            "feature_key": row["feature_key"],
            "content": row["content"],
            "status": row["status"],
            "ordinal": int(row["ordinal"]),
            "origin_source_id": row["origin_source_id"],
            "origin_requirement_key": row["origin_requirement_key"],
            "origin_version_no": int(row["origin_version_no"]),
            "removed_version_no": row["removed_version_no"],
            "provenance": list(row["provenance"] or []),
        }


class AuditRepository:
    """Persistence boundary for audit event records（审计事件写入/查询）。"""

    def record(self, event: AuditEvent, session: Session | None = None) -> AuditEvent:
        """写入一条审计事件（谁·何时·对什么做了什么·前后数据·结果），用于全程留痕与合规回溯。"""
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
        """按时间倒序返回最近 100 条审计事件（AuditEvent 对象）。"""
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
        """按时间倒序返回审计事件 dict 列表，供审计面板/回放展示。"""
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
                "created_at": as_display_iso(row["created_at"]),
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
        """新建一条对话（默认标题“新对话”），返回规范化后的对话行。"""
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
        """按 id 与 actor 取对话；不存在返回 None。"""
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
        """按 actor 列出对话（含消息数），最近更新优先，支持分页。"""
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
        """按需更新对话的 title/summary/status/meta 字段，返回更新后的对话行。"""
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
        """按 id 与 actor 删除一条对话，返回是否实际删除。"""
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
        """按对话 id 取全部消息（时间升序），返回规范化消息列表。"""
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
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """写入一条用户消息（按 client_message_id 幂等），返回规范化后的消息行。"""
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
                    "meta": json.dumps({**(meta or {}), "actor_id": actor_id}),
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
        """创建/更新一次 Agent run（按 client_message_id 幂等），返回规范化后的 run 行。"""
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
        """更新一次 run 的状态/错误/元数据，返回更新后的 run 行。"""
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
        """按 run_id 取 run；不存在返回 None。"""
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

    def get_run_by_client(self, conversation_id: str, client_message_id: str) -> dict[str, object] | None:
        """按 (conversation, client_message_id) 取最近一次 run（幂等重放用）。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, conversation_id, client_message_id, status, error, meta, created_at, updated_at
                    FROM agent_run
                    WHERE conversation_id = CAST(:conversation_id AS UUID) AND client_message_id = :client_message_id
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id, "client_message_id": client_message_id},
            ).mappings().first()
        return self._normalize_run_row(row) if row else None

    def get_assistant_message_for_run(self, run_id: str) -> dict[str, object] | None:
        """返回某次 run 的 assistant 消息（用于断连后重放落库结果，避免重算/重复）。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, conversation_id, role, content, artifacts, client_message_id, run_id, meta, created_at
                    FROM agent_message
                    WHERE run_id = CAST(:run_id AS UUID) AND role = 'assistant'
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"run_id": run_id},
            ).mappings().first()
        return self._normalize_message_row(row) if row else None

    def append_assistant_message(
        self,
        *,
        conversation_id: str,
        content: str,
        artifacts: dict[str, object] | None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        """追写一条 assistant 消息（可关联 run_id），返回规范化后的消息行。"""
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
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
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
            "created_at": as_display_iso(row["created_at"]),
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
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
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
        embedding: list[float] | None = None,
    ) -> dict[str, object]:
        """插入一条 actor 维度的长期记忆（可带 embedding 供向量召回），返回规范化记忆行。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO memory_note (actor_id, kind, content, source_conversation_id, source_message_id, ref_requirement_key, importance, meta, embedding)
                    VALUES (:actor_id, :kind, :content, CAST(:source_conversation_id AS UUID), :source_message_id, :ref_requirement_key, :importance, CAST(:meta AS JSONB), CAST(:embedding AS vector))
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
                    "embedding": ("[" + ",".join(str(float(x)) for x in embedding) + "]") if embedding is not None else None,
                },
            ).mappings().one()
            session.commit()
        return self._normalize_memory_row(row)

    def list_memories(self, actor_id: str = "api-user", limit: int = 20) -> list[dict[str, object]]:
        """列出该 actor 未删除的记忆（importance 优先），返回规范化记忆列表。"""
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
        """按文本关键词（ILIKE）召回该 actor 的 active 记忆，作为无向量场景的兜底。"""
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
        """更新某条记忆的状态并同步 active 标志，返回更新后的记忆行。"""
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

    def supersede(self, old_id: int, new_id: int) -> dict[str, object] | None:
        """把一条 active 记忆标记为已被新记忆替代（保留审计）。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    UPDATE memory_note
                    SET status = 'superseded',
                        active = FALSE,
                        superseded_by = :new_id,
                        updated_at = NOW()
                    WHERE id = :old_id AND status = 'active'
                    RETURNING id, actor_id, kind, status, active, content, source_conversation_id, source_message_id, ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    """
                ),
                {"old_id": old_id, "new_id": new_id},
            ).mappings().first()
            session.commit()
        return self._normalize_memory_row(row) if row else None

    def recall_vector(self, actor_id: str, query_vector: list[float], limit: int = 4) -> list[dict[str, object]]:
        """按向量余弦相似度召回该 actor 的有效记忆（embedding 为 NULL 的行自动跳过）。"""
        vec = "[" + ",".join(str(float(x)) for x in query_vector) + "]"
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, actor_id, kind, status, active, content, source_conversation_id, source_message_id,
                           ref_requirement_key, superseded_by, importance, meta, created_at, updated_at
                    FROM memory_note
                    WHERE actor_id = :actor_id AND status = 'active'
                    ORDER BY embedding <=> CAST(:query_vector AS vector)
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "query_vector": vec, "limit": limit},
            ).mappings().all()
        return [self._normalize_memory_row(row) for row in rows]

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
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }
