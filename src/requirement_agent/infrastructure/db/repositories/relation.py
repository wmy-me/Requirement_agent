"""需求关系 Repository：REQ ↔ REQ 的关系边（谁与谁重复/关联/冲突）。

数据来源是分析阶段的候选：审核通过时按 `duplicate`/`related`/`conflict` 落成本表的边。
`depends` 在 CHECK 里预留但没有产出方——它是人判断出来的关系，本轮不做。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.common.snowflake import new_id
from requirement_agent.common.time import as_display_iso
from requirement_agent.infrastructure.db.session import SessionLocal

_RELATION_FIELDS = (
    "id",
    "subject_requirement_id",
    "subject_requirement_key",
    "target_requirement_id",
    "target_requirement_key",
    "relation_type",
    "reason",
    "similarity",
    "source_id",
    "status",
    "created_by",
    "decided_by",
    "created_at",
    "updated_at",
)
_RELATION_COLUMNS = ", ".join(_RELATION_FIELDS)
# 双向读要 JOIN requirement_master，两边都有 id/created_at，必须带表别名限定
_RELATION_COLUMNS_ALIASED = ", ".join(f"r.{field}" for field in _RELATION_FIELDS)


class RequirementRelationRepository:
    """需求之间关系的读写边界。"""

    def upsert_many(
        self,
        *,
        subject_requirement_id: int,
        subject_requirement_key: str,
        relations: Iterable[Mapping[str, object]],
        source_id: int | None = None,
        session: Session | None = None,
    ) -> int:
        """批量写入关系边，返回**实际新增**的条数。

        同一 `(subject, target, relation_type)` 已存在则跳过：同一条关系会被反复分析出来，
        不该重复落。session 所有权：未传入时本方法自建并提交；传入时由调用方负责事务
        （审核链路里与落库同事务，保证原子）。
        """
        owns_session = session is None
        session = session or SessionLocal()
        inserted = 0
        try:
            for item in relations:
                result = session.execute(
                    text(
                        """
                        INSERT INTO requirement_relation (
                            id, subject_requirement_id, subject_requirement_key,
                            target_requirement_id, target_requirement_key,
                            relation_type, reason, similarity, source_id, status, created_by
                        ) VALUES (
                            :id, :subject_id, :subject_key,
                            :target_id, :target_key,
                            :relation_type, :reason, :similarity, :source_id, 'proposed', 'analysis'
                        )
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "id": new_id(),
                        "subject_id": subject_requirement_id,
                        "subject_key": subject_requirement_key,
                        "target_id": int(item["target_requirement_id"]),
                        "target_key": str(item["target_requirement_key"]),
                        "relation_type": str(item["relation_type"]),
                        "reason": item.get("reason"),
                        "similarity": item.get("similarity"),
                        "source_id": source_id,
                    },
                )
                inserted += result.rowcount
            if owns_session:
                session.commit()
            return inserted
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()

    def confirm_many(
        self,
        *,
        subject_requirement_id: int,
        subject_requirement_key: str,
        relations: Iterable[Mapping[str, object]],
        source_id: int | None = None,
        decided_by: str | None = None,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        """把关系边写成 `confirmed`（人工合并时用），返回被确认的边。

        与 `upsert_many` 的两处差异都是刻意的：

        - **冲突时升级而不是跳过**：合并这个动作本身就是人对这条重复关系的背书，
          已存在的 `proposed` 边要升格为 `confirmed`；
        - **不回头改 `upsert_many`**：它的返回值语义是「实际新增条数」，调用方与假实现
          都依赖它；而且它的 `DO NOTHING` 保证了「人已 dismissed 的边不会被后续分析
          翻回 proposed」这条性质 —— 不能被破坏。

        事务：传 session 时不 commit（审核链路里与落库同事务）；未传时自建并提交。
        """
        owns_session = session is None
        session = session or SessionLocal()
        confirmed: list[dict[str, object]] = []
        try:
            for item in relations:
                row = session.execute(
                    text(
                        f"""
                        INSERT INTO requirement_relation (
                            id, subject_requirement_id, subject_requirement_key,
                            target_requirement_id, target_requirement_key,
                            relation_type, reason, similarity, source_id, status, created_by, decided_by
                        ) VALUES (
                            :id, :subject_id, :subject_key,
                            :target_id, :target_key,
                            :relation_type, :reason, :similarity, :source_id,
                            'confirmed', 'review', :decided_by
                        )
                        ON CONFLICT (subject_requirement_id, target_requirement_id, relation_type)
                        DO UPDATE SET status = 'confirmed',
                                      decided_by = EXCLUDED.decided_by,
                                      reason = COALESCE(EXCLUDED.reason, requirement_relation.reason),
                                      similarity = COALESCE(
                                          EXCLUDED.similarity, requirement_relation.similarity
                                      ),
                                      updated_at = NOW()
                        RETURNING {_RELATION_COLUMNS}
                        """
                    ),
                    {
                        "id": new_id(),
                        "subject_id": subject_requirement_id,
                        "subject_key": subject_requirement_key,
                        "target_id": int(item["target_requirement_id"]),
                        "target_key": str(item["target_requirement_key"]),
                        "relation_type": str(item["relation_type"]),
                        "reason": item.get("reason"),
                        "similarity": item.get("similarity"),
                        "source_id": source_id,
                        "decided_by": decided_by,
                    },
                ).mappings().first()
                if row is not None:
                    confirmed.append(self._normalize_row(row))
            if owns_session:
                session.commit()
            return confirmed
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()

    def list_for_requirement(self, requirement_key: str) -> list[dict[str, object]]:
        """**双向**返回某条需求的关系：它指向别人的 + 别人指向它的。

        每条都带出**对方**的编号与名称，以及方向（outgoing/incoming），
        免得调用方还要再查一次 master 才能显示人看得懂的名字。
        """
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    f"""
                    SELECT {_RELATION_COLUMNS_ALIASED},
                           CASE WHEN r.subject_requirement_key = :key THEN 'outgoing' ELSE 'incoming' END
                               AS direction,
                           other.requirement_key AS other_requirement_key,
                           other.requirement_name AS other_requirement_name
                    FROM requirement_relation r
                    JOIN requirement_master other
                      ON other.id = CASE WHEN r.subject_requirement_key = :key
                                         THEN r.target_requirement_id
                                         ELSE r.subject_requirement_id END
                    WHERE :key IN (r.subject_requirement_key, r.target_requirement_key)
                    ORDER BY r.created_at DESC, r.id DESC
                    """
                ),
                {"key": requirement_key},
            ).mappings().all()
        return [self._normalize_row(row) for row in rows]

    def update_status(
        self,
        relation_id: int,
        status: str,
        *,
        decided_by: str | None = None,
    ) -> dict[str, object] | None:
        """裁决一条关系（confirmed / dismissed）；不存在返回 None。"""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    f"""
                    UPDATE requirement_relation
                    SET status = :status, decided_by = :decided_by, updated_at = NOW()
                    WHERE id = :id
                    RETURNING {_RELATION_COLUMNS}
                    """
                ),
                {"id": relation_id, "status": status, "decided_by": decided_by},
            ).mappings().first()
            session.commit()
        return self._normalize_row(row) if row else None

    @staticmethod
    def _normalize_row(row: Mapping[str, Any]) -> dict[str, object]:
        normalized: dict[str, object] = {
            "id": int(row["id"]),
            "subject_requirement_key": row["subject_requirement_key"],
            "target_requirement_key": row["target_requirement_key"],
            "relation_type": row["relation_type"],
            "reason": row["reason"],
            "similarity": float(row["similarity"]) if row["similarity"] is not None else None,
            "source_id": row["source_id"],
            "status": row["status"],
            "created_by": row["created_by"],
            "decided_by": row["decided_by"],
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }
        # list_for_requirement 会额外带出方向与对方信息；update_status 的结果里没有
        for key in ("direction", "other_requirement_key", "other_requirement_name"):
            if key in row:
                normalized[key] = row[key]
        return normalized
