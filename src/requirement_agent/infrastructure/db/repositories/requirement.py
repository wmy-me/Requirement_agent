"""需求主体 Repository：来源(Source)、主需求(Master)、版本(Version)与溯源。

- `RequirementSourceRepository`：原始来源增删改查 + 幂等去重。
- `RequirementMasterRepository`：规范化主需求（REQ 主体、乐观锁）。
- `RequirementVersionRepository`：版本快照、来源关联与溯源。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from requirement_agent.common.snowflake import new_id
from requirement_agent.common.time import as_display_iso
from requirement_agent.domain.requirement import RequirementMaster, RequirementSource, RequirementVersion
from requirement_agent.infrastructure.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 渠道事件幂等唯一索引（migrations/001_init_business_schema.sql:26-28）
_CHANNEL_EVENT_CONSTRAINT = "uq_requirement_source_channel_event"


def _is_channel_event_conflict(exc: IntegrityError) -> bool:
    """判断是否撞上了渠道事件唯一索引，而非其它完整性错误（避免误吞真正的写入失败）。"""
    return _CHANNEL_EVENT_CONSTRAINT in str(exc)


# 来源侧的元数据取值表达式：聚合（SELECT）与筛选（HAVING）共用同一份定义。
# 这几个 JSON 路径写错一个字符就会静默查不到数据，所以只允许有一处事实源。
_SOURCE_REQUESTER = "COALESCE(s.requester_name, s.requester_id)"
_SOURCE_DEPARTMENT = (
    "COALESCE(s.metadata->>'department', "
    "s.metadata #>> '{standardized_document,normalized_fields,department}')"
)
_SOURCE_BUSINESS_DOMAIN = (
    "COALESCE(s.metadata->>'business_domain', "
    "s.metadata #>> '{standardized_document,normalized_fields,business_domain}', "
    "s.metadata #>> '{extracted,business_domain}')"
)
_SOURCE_SENSITIVITY = (
    "COALESCE(s.metadata->>'sensitivity_level', "
    "s.metadata #>> '{standardized_document,normalized_fields,sensitivity_level}')"
)


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
                        id, idempotency_key, source_type, source_event_id, requester_id, requester_name,
                        original_text, extracted_text, original_payload, metadata, submitted_at
                    ) VALUES (
                        :id, :idempotency_key, :source_type, :source_event_id, :requester_id, :requester_name,
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
                    "id": new_id(),
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
        except IntegrityError as exc:
            # 渠道事件唯一索引不在 ON CONFLICT 目标里：同一 (渠道, 事件ID) 若正文有差异
            # （idempotency_key 因而不同），并发重投就会撞上它。此时该事件其实已入库，
            # 回查返回既有来源即可，不必让调用方吃到 500。
            if not owns_session:
                raise
            session.rollback()
            session.close()
            existing = (
                self.find_by_channel_event(source.source_type, source.source_event_id)
                if _is_channel_event_conflict(exc)
                else None
            )
            if existing is None:
                raise
            logger.info(
                "event=channel_event_deduplicated channel=%s event_id=%s source_id=%s",
                source.source_type,
                source.source_event_id,
                existing.id,
            )
            return existing
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

    def find_by_channel_event(
        self, source_type: str, source_event_id: str | None
    ) -> RequirementSource | None:
        """按 (渠道, 渠道事件 ID) 取已入库来源；事件 ID 为空时直接返回 None。

        渠道幂等的第一道防线（第二道是 uq_requirement_source_channel_event 唯一索引）。
        语义：同一 (渠道, 事件ID) 视为**同一条需求** —— 渠道重复投递是投递重试，
        不是编辑操作，所以后到的正文不覆盖已入库内容。
        """
        if not source_event_id:
            return None
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT id, idempotency_key, source_type, source_event_id, requester_id, requester_name, "
                    "original_text, extracted_text, original_payload, metadata, processing_status, submitted_at "
                    "FROM requirement_source WHERE source_type = :source_type AND source_event_id = :event_id"
                ),
                {"source_type": source_type, "event_id": source_event_id},
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
                # **必须序列化成字符串**：雪花 id 超过 JS 的 Number.MAX_SAFE_INTEGER（2^53），
                # 前端 JSON.parse 会把它悄悄改写（实测 224103804432285696 → ...700、
                # 225111676653928448 → ...450），回传时就成了「source_id not found」→ 审核 409。
                # 字符串在 JS 里原样透传；后端 pydantic 会把数字字符串再转回 int。
                "source_id": str(row["id"]),
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
                    INSERT INTO requirement_master (id, requirement_key, requirement_name, final_requirement, current_version, status, lock_version)
                    VALUES (:id, :requirement_key, :requirement_name, :final_requirement, :current_version, :status, :lock_version)
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
                    "id": new_id(),
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

    @staticmethod
    def _build_master_filter(
        filters: Mapping[str, object],
    ) -> tuple[str, str, dict[str, object]]:
        """把筛选条件编译成 (WHERE 段, HAVING 段, 绑定参数)。

        为什么要分成两处：本查询靠 `GROUP BY m.id` 聚合出行，`channel` / `department` 这类
        条件比的是**聚合值**，只能写 HAVING；若写进 WHERE 或 JOIN 的 ON，会把不匹配的来源
        从聚合里剔除，行本身还在却少了几列——那不是「筛掉这条需求」而是「改写这条需求」。

        `status` / `has_version_ge` / `q` 比的是主表列，放 WHERE 更省事也更快。

        匹配语义与 `RetrievalService._matches_filters` 保持一致：**单值精确匹配、大小写敏感**。
        值为空串或 None 的键一律忽略；参数全部走绑定，不做字符串拼接。
        """
        where: list[str] = []
        having: list[str] = []
        params: dict[str, object] = {}

        def normalize(key: str) -> str | None:
            value = filters.get(key)
            if value is None:
                return None
            stripped = str(value).strip()
            return stripped or None

        if (value := normalize("status")) is not None:
            where.append("m.status = :status")
            params["status"] = value
        if (value := filters.get("has_version_ge")) is not None:
            where.append("m.current_version >= :has_version_ge")
            params["has_version_ge"] = int(value)
        if (value := normalize("q")) is not None:
            where.append("(m.requirement_name ILIKE :q OR m.final_requirement ILIKE :q)")
            params["q"] = f"%{value}%"

        if (value := normalize("channel")) is not None:
            having.append("bool_or(s.source_type = :channel)")
            params["channel"] = value
        if (value := normalize("requester")) is not None:
            having.append(f"bool_or({_SOURCE_REQUESTER} = :requester)")
            params["requester"] = value
        if (value := normalize("department")) is not None:
            having.append(f"bool_or({_SOURCE_DEPARTMENT} = :department)")
            params["department"] = value
        if (value := normalize("business_domain")) is not None:
            having.append(f"bool_or({_SOURCE_BUSINESS_DOMAIN} = :business_domain)")
            params["business_domain"] = value
        if (value := normalize("sensitivity_level")) is not None:
            having.append(f"bool_or({_SOURCE_SENSITIVITY} = :sensitivity_level)")
            params["sensitivity_level"] = value
        if (value := normalize("submitted_from")) is not None:
            having.append("bool_or(s.submitted_at >= CAST(:submitted_from AS TIMESTAMPTZ))")
            params["submitted_from"] = value
        if (value := normalize("submitted_to")) is not None:
            having.append("bool_or(s.submitted_at <= CAST(:submitted_to AS TIMESTAMPTZ))")
            params["submitted_to"] = value

        where_clause = ("\n                    WHERE " + " AND ".join(where)) if where else ""
        having_clause = ("\n                    HAVING " + " AND ".join(having)) if having else ""
        return where_clause, having_clause, params

    def list_with_source_context(
        self, limit: int = 100, filters: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        """聚合主需求 + 来源上下文（领域/来源渠道/输入人/密级/最新提交时间/功能数），供列表与检索使用。

        `filters` 见 `_build_master_filter`；传空时不产生任何额外子句，SQL 与加筛选前逐字一致。
        """
        where_clause, having_clause, params = self._build_master_filter(filters or {})
        params["limit"] = limit
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    f"""
                    SELECT m.id, m.requirement_key, m.requirement_name, m.final_requirement,
                           m.current_version, m.status, m.lock_version,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT s.source_type), NULL) AS source_types,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT {_SOURCE_REQUESTER}), NULL) AS requester_names,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT {_SOURCE_DEPARTMENT}), NULL) AS departments,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT {_SOURCE_BUSINESS_DOMAIN}), NULL) AS business_domains,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT {_SOURCE_SENSITIVITY}), NULL) AS sensitivity_levels,
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT f.content), NULL) AS feature_contents,
                           COUNT(DISTINCT CASE WHEN f.status = 'active' THEN f.id END) AS feature_count,
                           MIN(s.submitted_at) AS first_source_submitted_at,
                           MAX(s.submitted_at) AS latest_source_submitted_at
                    FROM requirement_master m
                    LEFT JOIN requirement_version v ON v.requirement_id = m.id
                    LEFT JOIN requirement_version_source vs ON vs.version_id = v.id
                    LEFT JOIN requirement_source s ON s.id = vs.source_id
                    LEFT JOIN requirement_feature f ON f.requirement_id = m.id{where_clause}
                    GROUP BY m.id, m.requirement_key, m.requirement_name, m.final_requirement,
                            m.current_version, m.status, m.lock_version{having_clause}
                    ORDER BY m.updated_at DESC
                    LIMIT :limit
                    """
                ),
                params,
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
                        id, requirement_id, parent_version_id, version_no, version_title, change_type,
                        requirement_snapshot, change_summary, diff_payload, created_by, reviewed_by, feature_changes, parent_version_no,
                        capability_snapshot, constraint_snapshot, status
                    ) VALUES (
                        :id, :requirement_id, :parent_version_id, :version_no, :version_title, :change_type,
                        :requirement_snapshot, :change_summary, :diff_payload, :created_by, :reviewed_by, :feature_changes, :parent_version_no,
                        CAST(:capability_snapshot AS JSONB), CAST(:constraint_snapshot AS JSONB), :status
                    )
                    RETURNING id, requirement_id, version_no
                    """
                ),
                {
                    "id": new_id(),
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
                    "capability_snapshot": json.dumps(
                        getattr(version, "capability_snapshot", None) or []
                    ),
                    "constraint_snapshot": json.dumps(
                        getattr(version, "constraint_snapshot", None) or []
                    ),
                    "status": getattr(version, "status", "current") or "current",
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

    def get_current(self, *, master_id: int, session: Session | None = None) -> dict[str, object] | None:
        """取某主线的**当前版本**（`status='current'`），含能力/条件快照。

        与 `get_latest_by_requirement`（按 version_no 最大）的区别：那条不问状态，
        这条认状态。有了批次 3 的 `status` 之后，**「当前」应由状态定义**
        —— 否则回滚（G 批）把某个历史版本重新置为 current 时，这里会取错版本。
        """
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    SELECT id, requirement_id, parent_version_id, parent_version_no, version_no,
                           version_title, change_type, requirement_snapshot, change_summary,
                           diff_payload, feature_changes, capability_snapshot, constraint_snapshot,
                           status, created_by, reviewed_by, created_at
                    FROM requirement_version
                    WHERE requirement_id = :rid AND status = 'current'
                    LIMIT 1
                    """
                ),
                {"rid": master_id},
            ).mappings().first()
        finally:
            if owns_session:
                session.close()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "requirement_id": int(row["requirement_id"]),
            "version_no": int(row["version_no"]),
            "version_title": row["version_title"],
            "change_type": row["change_type"],
            "requirement_snapshot": row["requirement_snapshot"],
            "change_summary": row["change_summary"],
            "status": row["status"],
            "capability_snapshot": list(row["capability_snapshot"] or []),
            "constraint_snapshot": list(row["constraint_snapshot"] or []),
            "created_at": as_display_iso(row["created_at"]),
        }

    def supersede_current(
        self,
        *,
        requirement_id: int,
        keep_version_no: int,
        session: Session | None = None,
    ) -> int:
        """把该主线里**除 `keep_version_no` 之外**的 current 版本降为 `superseded`。

        ⚠️ **必须在插入新版本之前调用。** 数据库上有部分唯一索引
        `uq_version_current (requirement_id) WHERE status = 'current'`，
        而 `save()` 插入时状态默认就是 `current` —— 若先插入再降级，
        **第二条版本写入时会直接撞唯一约束**（实测踩过一次）。
        正确顺序：先调本方法把旧的降级，再 `save()` 插入新的。

        `superseded_by_version_no` 用 `COALESCE` 只填一次：历史版本一旦被某版本取代
        就固定下来，不该被后续版本改写。
        """
        owns_session = session is None
        session = session or SessionLocal()
        try:
            result = session.execute(
                text(
                    """
                    UPDATE requirement_version
                    SET status = 'superseded',
                        superseded_by_version_no = COALESCE(superseded_by_version_no, :version_no)
                    WHERE requirement_id = :requirement_id
                      AND version_no <> :version_no
                      AND status = 'current'
                    """
                ),
                {"requirement_id": requirement_id, "version_no": keep_version_no},
            )
            affected = result.rowcount
            if owns_session:
                session.commit()
            return affected
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()

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

