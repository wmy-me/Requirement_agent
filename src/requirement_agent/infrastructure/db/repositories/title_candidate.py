"""需求的候选标题仓储（同一需求的不同视角入口）。

**标题不是另一份内容，是一个视角。** 所以每条标题可以绑定一个锚点
（能力 / 条件 / 业务对象），锚点同时决定「从这条标题点进去时高亮哪几行功能」。

状态沿用全局约定：提议一律 `proposed`，**只有人工确认过的才对外可见**
（列表只出 confirmed；proposed 留给审核页）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.common.snowflake import new_id, to_sid
from requirement_agent.common.time import as_display_iso
from requirement_agent.infrastructure.db.session import SessionLocal

__all__ = ["RequirementTitleCandidateRepository"]

_FIELDS = (
    "id",
    "requirement_id",
    "title",
    "angle",
    "capability_id",
    "constraint_key",
    "source",
    "review_status",
    "decided_by",
    "created_by",
    "created_at",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


class RequirementTitleCandidateRepository:
    """候选标题的读写边界。"""

    def upsert_many(
        self,
        *,
        requirement_id: int,
        titles: Iterable[Mapping[str, object]],
        session: Session | None = None,
    ) -> int:
        """批量写入候选标题，返回**实际新增**条数。

        重复标题走 `DO NOTHING`：同一条标题会被反复派生出来，不该重复落，
        而且**不能覆盖人工已有的裁决**（confirmed/dismissed 不该被下一次派生翻回 proposed）。
        """
        items = [item for item in titles if _text(item.get("title"))]
        if not items:
            return 0
        owns_session = session is None
        session = session or SessionLocal()
        inserted = 0
        try:
            for item in items:
                result = session.execute(
                    text(
                        """
                        INSERT INTO requirement_title_candidate (
                            id, requirement_id, title, angle, capability_id, constraint_key,
                            source, review_status, created_by
                        ) VALUES (
                            :id, :requirement_id, :title, :angle, :capability_id, :constraint_key,
                            :source, 'proposed', :created_by
                        )
                        ON CONFLICT (requirement_id, title) DO NOTHING
                        """
                    ),
                    {
                        "id": new_id(),
                        "requirement_id": requirement_id,
                        "title": _text(item.get("title"))[:200],
                        "angle": _text(item.get("angle")) or "free",
                        "capability_id": item.get("capability_id"),
                        "constraint_key": _text(item.get("constraint_key")) or None,
                        "source": _text(item.get("source")) or "analysis",
                        "created_by": _text(item.get("created_by")) or "analysis",
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

    def list_for_requirement(
        self,
        requirement_id: int,
        *,
        review_status: str | None = None,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        owns_session = session is None
        session = session or SessionLocal()
        clauses = ["requirement_id = :rid"]
        params: dict[str, object] = {"rid": requirement_id}
        if review_status:
            clauses.append("review_status = :rs")
            params["rs"] = review_status
        try:
            rows = session.execute(
                text(
                    f"SELECT {', '.join(_FIELDS)} FROM requirement_title_candidate "
                    f"WHERE {' AND '.join(clauses)} ORDER BY created_at, id"
                ),
                params,
            ).mappings().all()
        finally:
            if owns_session:
                session.close()
        return [self._normalize(row) for row in rows]

    def list_confirmed_for_requirements(
        self,
        requirement_ids: Iterable[int],
        *,
        session: Session | None = None,
    ) -> dict[int, list[dict[str, object]]]:
        """一次取多条需求的**已确认**标题，供列表页展开多入口用（避免 N+1）。"""
        ids = [int(item) for item in requirement_ids]
        if not ids:
            return {}
        owns_session = session is None
        session = session or SessionLocal()
        try:
            rows = session.execute(
                text(
                    f"SELECT {', '.join(_FIELDS)} FROM requirement_title_candidate "
                    "WHERE requirement_id = ANY(:ids) AND review_status = 'confirmed' "
                    "ORDER BY created_at, id"
                ),
                {"ids": ids},
            ).mappings().all()
        finally:
            if owns_session:
                session.close()
        grouped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            item = self._normalize(row)
            grouped.setdefault(int(item["requirement_id"]), []).append(item)
        return grouped

    def update_status(
        self,
        title_id: int,
        status: str,
        *,
        decided_by: str | None = None,
        session: Session | None = None,
    ) -> dict[str, object] | None:
        """人工裁决一条候选标题。**这是标题对外可见的唯一入口。**"""
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    f"UPDATE requirement_title_candidate SET review_status = :status, "
                    f"decided_by = :decided_by WHERE id = :id RETURNING {', '.join(_FIELDS)}"
                ),
                {"id": title_id, "status": status, "decided_by": decided_by},
            ).mappings().first()
            if owns_session:
                session.commit()
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()
        return self._normalize(row) if row else None

    @staticmethod
    def _normalize(row) -> dict[str, object]:
        return {
            # 雪花 ID 字符串化：`id` 会被前端拼进 PATCH `/requirement-titles/{id}`。
            "id": to_sid(row["id"]),
            "requirement_id": to_sid(row["requirement_id"]),
            "title": row["title"],
            "angle": row["angle"],
            "capability_id": to_sid(row["capability_id"]),
            "constraint_key": row["constraint_key"],
            "source": row["source"],
            "review_status": row["review_status"],
            "decided_by": row.get("decided_by"),
            "created_by": row["created_by"],
            "created_at": as_display_iso(row["created_at"]),
        }
