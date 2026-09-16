"""能力与限定条件的**受控词表**仓储（方案批次 1）。

设计要点（与方案 §11 的职责边界一致）：

- **能力 = `(action, object)` 二元组**，归并靠**精确匹配**而不是相似度。用相似度会让
  「按部门筛选导出 Excel」和「导出 Excel」（余弦 0.85+）被误合成同一条能力。
- **新建一律是提案**：`create_*` 的 status 参数默认 `pending_confirmation`，
  词表里只有 `active` 的条目才参与匹配。**AI 没有路径直接创建 `active` 条目** ——
  这是把「受控词表新增必须人工确认」这条规则落在代码里，而不是靠调用方自觉。
- **条件有别名**：同一个条件换个说法不该裂成两条。「按部门维度筛选」作为别名指向
  正式键「按部门筛选」。

`updated_at` 由数据库触发器维护，这里不写。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.common.snowflake import new_id, to_sid
from requirement_agent.common.time import as_display_iso
from requirement_agent.infrastructure.db.session import SessionLocal

__all__ = [
    "ACTIVE",
    "DEPRECATED",
    "PENDING_CONFIRMATION",
    "CapabilityRepository",
    "ConstraintVocabRepository",
]

# 词表状态：只有 ACTIVE 参与匹配
ACTIVE = "active"
DEPRECATED = "deprecated"
PENDING_CONFIRMATION = "pending_confirmation"


def _text(value: object) -> str:
    """归一：trim，None 变空串。"""
    return str(value or "").strip()


class CapabilityRepository:
    """能力词表的读写边界。"""

    def list(
        self,
        *,
        status: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        """列能力；默认全部状态，按 (action, object) 排序。

        `q` 在 display_name / action / object 上做 ILIKE 模糊过滤（只读查询用）。
        """
        owns_session = session is None
        session = session or SessionLocal()
        clauses: list[str] = []
        params: dict[str, object] = {}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if q:
            clauses.append("(display_name ILIKE :q OR action ILIKE :q OR object ILIKE :q)")
            params["q"] = f"%{q.strip()}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, action, object, display_name, status, created_by, origin_source_id, created_at, updated_at "
            f"FROM capability {where} ORDER BY action, object"
        )
        if limit:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)
        try:
            rows = session.execute(text(sql), params).mappings().all()
        finally:
            if owns_session:
                session.close()
        return [self._normalize(row) for row in rows]

    def get(self, capability_id: int, *, session: Session | None = None) -> dict[str, object] | None:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    "SELECT id, action, object, display_name, status, created_by, origin_source_id, created_at, updated_at "
                    "FROM capability WHERE id = :id"
                ),
                {"id": capability_id},
            ).mappings().first()
        finally:
            if owns_session:
                session.close()
        return self._normalize(row) if row else None

    def find_exact(
        self,
        action: str,
        object_: str,
        *,
        only_active: bool = True,
        session: Session | None = None,
    ) -> dict[str, object] | None:
        """按 `(action, object)` **精确匹配**。这是能力归并的唯一入口。

        `only_active=True`（默认）时只认已确认的能力 —— 别人提议的、还没人确认的
        条目不该被自动复用。
        """
        owns_session = session is None
        session = session or SessionLocal()
        sql = (
            "SELECT id, action, object, display_name, status, created_by, origin_source_id, created_at, updated_at "
            "FROM capability WHERE action = :action AND object = :object"
        )
        if only_active:
            sql += " AND status = 'active'"
        try:
            row = session.execute(
                text(sql), {"action": _text(action), "object": _text(object_)}
            ).mappings().first()
        finally:
            if owns_session:
                session.close()
        return self._normalize(row) if row else None

    def create(
        self,
        *,
        action: str,
        object_: str,
        display_name: str | None = None,
        status: str = PENDING_CONFIRMATION,
        created_by: str = "analysis",
        origin_source_id: int | None = None,
        session: Session | None = None,
    ) -> dict[str, object]:
        """新增一条能力。**默认落 `pending_confirmation`，不参与匹配。**

        调用方只有在人工确认的路径上才应显式传 `status=ACTIVE`。
        同一 `(action, object)` 已存在时返回既有行（幂等），不报错。

        `origin_source_id` 记录**是哪条来源提出的**：外键带 `ON DELETE CASCADE`，
        来源一删提案跟着走 —— 测试清理因此自动完整，不必每个调用方各自记得清。
        审核页也靠它显示「这条能力是哪个需求提的」。
        """
        action, object_ = _text(action), _text(object_)
        if not action or not object_:
            raise ValueError("capability requires both action and object")
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    INSERT INTO capability (
                        id, action, object, display_name, status, created_by, origin_source_id
                    )
                    VALUES (
                        :id, :action, :object, :display_name, :status, :created_by, :origin_source_id
                    )
                    ON CONFLICT (action, object) DO UPDATE
                        SET display_name = capability.display_name
                    RETURNING id, action, object, display_name, status, created_by,
                              origin_source_id, created_at, updated_at
                    """
                ),
                {
                    "id": new_id(),
                    "action": action,
                    "object": object_,
                    "display_name": _text(display_name) or f"{action} {object_}",
                    "status": status,
                    "created_by": created_by,
                    "origin_source_id": origin_source_id,
                },
            ).mappings().one()
            if owns_session:
                session.commit()
            return self._normalize(row)
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()

    def update_status(
        self,
        capability_id: int,
        status: str,
        *,
        session: Session | None = None,
    ) -> dict[str, object] | None:
        """改状态（人工确认 `active` / 停用 `deprecated`）。不存在返回 None。"""
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    UPDATE capability SET status = :status
                    WHERE id = :id
                    RETURNING id, action, object, display_name, status, created_by, origin_source_id, created_at, updated_at
                    """
                ),
                {"id": capability_id, "status": status},
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
            # 雪花 ID 字符串化：`id` 会进 `/capabilities/{id}/streams` 与 PATCH 的 URL。
            "id": to_sid(row["id"]),
            "action": row["action"],
            "object": row["object"],
            "display_name": row["display_name"],
            "status": row["status"],
            "created_by": row["created_by"],
            "origin_source_id": to_sid(row.get("origin_source_id")),
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }


class ConstraintVocabRepository:
    """限定条件词表的读写边界（含别名）。"""

    def list(
        self,
        *,
        status: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        owns_session = session is None
        session = session or SessionLocal()
        clauses: list[str] = []
        params: dict[str, object] = {}
        if status:
            clauses.append("c.status = :status")
            params["status"] = status
        if q:
            clauses.append("(c.constraint_key ILIKE :q OR c.display_name ILIKE :q)")
            params["q"] = f"%{q.strip()}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT c.id, c.constraint_key, c.display_name, c.status, c.created_by,
                   c.created_at, c.updated_at,
                   COALESCE(
                       (SELECT array_agg(a.alias ORDER BY a.alias)
                        FROM constraint_alias a WHERE a.constraint_id = c.id),
                       ARRAY[]::TEXT[]
                   ) AS aliases
            FROM constraint_vocab c
            {where}
            ORDER BY c.constraint_key
        """
        if limit:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)
        try:
            rows = session.execute(text(sql), params).mappings().all()
        finally:
            if owns_session:
                session.close()
        return [self._normalize(row) for row in rows]

    def get(self, constraint_id: int, *, session: Session | None = None) -> dict[str, object] | None:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    SELECT c.id, c.constraint_key, c.display_name, c.status, c.created_by,
                           c.created_at, c.updated_at,
                           COALESCE(
                               (SELECT array_agg(a.alias ORDER BY a.alias)
                                FROM constraint_alias a WHERE a.constraint_id = c.id),
                               ARRAY[]::TEXT[]
                           ) AS aliases
                    FROM constraint_vocab c WHERE c.id = :id
                    """
                ),
                {"id": constraint_id},
            ).mappings().first()
        finally:
            if owns_session:
                session.close()
        return self._normalize(row) if row else None

    def find_exact(
        self,
        key: str,
        *,
        only_active: bool = True,
        session: Session | None = None,
    ) -> dict[str, object] | None:
        """按**正式键或别名**精确匹配。别名命中与正式键命中等价。"""
        target = _text(key)
        if not target:
            return None
        owns_session = session is None
        session = session or SessionLocal()
        # ⚠️ 正式键与别名两个分支**必须整体加括号**再拼状态条件。
        # 写成 `WHERE a = x OR EXISTS(...) AND status = 'active'` 时，SQL 的优先级会把
        # 状态过滤只作用到别名那一支，于是正式键命中的行永远不受状态约束 ——
        # `only_active` 形同虚设，待确认/已停用的条件照样能匹配上。
        sql = """
            SELECT c.id, c.constraint_key, c.display_name, c.status, c.created_by,
                   c.created_at, c.updated_at,
                   COALESCE(
                       (SELECT array_agg(a.alias ORDER BY a.alias)
                        FROM constraint_alias a WHERE a.constraint_id = c.id),
                       ARRAY[]::TEXT[]
                   ) AS aliases
            FROM constraint_vocab c
            WHERE (
                c.constraint_key = :key
                OR EXISTS (SELECT 1 FROM constraint_alias a
                           WHERE a.constraint_id = c.id AND a.alias = :key)
            )
        """
        if only_active:
            sql += " AND c.status = 'active'"
        try:
            row = session.execute(text(sql), {"key": target}).mappings().first()
        finally:
            if owns_session:
                session.close()
        return self._normalize(row) if row else None

    def create(
        self,
        *,
        constraint_key: str,
        display_name: str | None = None,
        status: str = PENDING_CONFIRMATION,
        created_by: str = "analysis",
        session: Session | None = None,
    ) -> dict[str, object]:
        """新增一条条件。**默认落 `pending_confirmation`，不参与匹配。**"""
        key = _text(constraint_key)
        if not key:
            raise ValueError("constraint requires a non-empty key")
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    INSERT INTO constraint_vocab (id, constraint_key, display_name, status, created_by)
                    VALUES (:id, :key, :display_name, :status, :created_by)
                    ON CONFLICT (constraint_key) DO UPDATE
                        SET display_name = constraint_vocab.display_name
                    RETURNING id, constraint_key, display_name, status, created_by, created_at, updated_at
                    """
                ),
                {
                    "id": new_id(),
                    "key": key,
                    "display_name": _text(display_name) or key,
                    "status": status,
                    "created_by": created_by,
                },
            ).mappings().one()
            if owns_session:
                session.commit()
            normalized = self._normalize(row)
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()
        return normalized

    def update_status(
        self,
        constraint_id: int,
        status: str,
        *,
        session: Session | None = None,
    ) -> dict[str, object] | None:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    UPDATE constraint_vocab SET status = :status
                    WHERE id = :id
                    RETURNING id, constraint_key, display_name, status, created_by, created_at, updated_at
                    """
                ),
                {"id": constraint_id, "status": status},
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
        if not row:
            return None
        normalized = self._normalize(row)
        normalized["aliases"] = []
        return normalized

    def add_alias(
        self,
        *,
        alias: str,
        constraint_id: int,
        created_by: str = "review",
        session: Session | None = None,
    ) -> dict[str, object]:
        """把一条原始表达登记为某条件的别名（人工审核的四个选项之一）。

        别名**唯一**：同一表达只能指向一个条件。重复登记时返回既有行（幂等），
        不会把别名改挂到别的条件上 —— 避免一次误操作静默改变历史语义。
        """
        text_alias = _text(alias)
        if not text_alias:
            raise ValueError("alias must not be empty")
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    INSERT INTO constraint_alias (id, alias, constraint_id, created_by)
                    VALUES (:id, :alias, :constraint_id, :created_by)
                    ON CONFLICT (alias) DO UPDATE SET alias = constraint_alias.alias
                    RETURNING id, alias, constraint_id, created_by, created_at
                    """
                ),
                {
                    "id": new_id(),
                    "alias": text_alias,
                    "constraint_id": constraint_id,
                    "created_by": created_by,
                },
            ).mappings().one()
            if owns_session:
                session.commit()
            return {
                # 雪花 ID 字符串化，与同仓储的 `create` / `find_exact` 保持一致 ——
                # 同一仓储的两个方法返回同一个字段的不同类型，调用方一定会踩。
                "id": to_sid(row["id"]),
                "alias": row["alias"],
                "constraint_id": to_sid(row["constraint_id"]),
                "created_by": row["created_by"],
                "created_at": as_display_iso(row["created_at"]),
            }
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()

    @staticmethod
    def _normalize(row) -> dict[str, object]:
        aliases = row.get("aliases") if hasattr(row, "get") else None
        return {
            "id": to_sid(row["id"]),
            "constraint_key": row["constraint_key"],
            "display_name": row["display_name"],
            "status": row["status"],
            "created_by": row["created_by"],
            "aliases": list(aliases or []),
            "created_at": as_display_iso(row["created_at"]),
            "updated_at": as_display_iso(row["updated_at"]),
        }


class FeatureCapabilityRepository:
    """功能条目 ↔ 能力的关联（方案批次 3）。

    **关联挂在 feature 上，不是挂在 REQ 上** —— 这是方案的核心决定：feature 已有
    一整套生命周期（`status` / `origin_version_no` / `removed_version_no`），
    需求更新后不再支持某能力 → 该 feature 被软删 → 关联自动失效，不需要任何新代码。
    这正好回答「某个需求不再支持 PDF 导出了，把来源删掉」那个场景。

    写入一律 `proposed`（`link_many` 在 SQL 里写死）：AI 提议的关联需要人工裁决，
    **只有 `confirmed` 才代表该能力在这个需求上正式成立**。
    """

    def link_many(
        self,
        *,
        links: list[dict[str, object]],
        session: Session | None = None,
    ) -> int:
        """批量写入关联，返回**实际新增**条数。

        `links` 元素为 `{"feature_id", "capability_id", "raw_text"?, "confidence"?}`。
        重复关联走 `DO NOTHING`：同一条关联会被反复算出，不该重复落，
        而且**不能覆盖人工已有的裁决**（confirmed/dismissed 不该被下一次分析翻回 proposed）。
        """
        if not links:
            return 0
        owns_session = session is None
        session = session or SessionLocal()
        inserted = 0
        try:
            for item in links:
                result = session.execute(
                    text(
                        """
                        INSERT INTO feature_capability (
                            feature_id, capability_id, raw_text, confidence, review_status
                        ) VALUES (
                            :feature_id, :capability_id, :raw_text, :confidence, 'proposed'
                        )
                        ON CONFLICT (feature_id, capability_id) DO NOTHING
                        """
                    ),
                    {
                        "feature_id": int(item["feature_id"]),
                        "capability_id": int(item["capability_id"]),
                        "raw_text": _text(item.get("raw_text"))[:2000],
                        "confidence": item.get("confidence"),
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

    def search_streams(
        self,
        *,
        capability_id: int,
        constraint_key: str | None = None,
        review_status: str | None = None,
        limit: int = 100,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        """按能力（可加条件）反查需求主线 —— 方案 §6 的前两类搜索。

        两个维度取自**不同的地方**，这是刻意的：

        - **能力的存在性**查 `feature_capability`（活的事实，含人工裁决状态），
          并只统计**仍生效**的 feature；
        - **条件**查**当前版本的快照** —— 条件只存在于版本快照里，关联表上没有。

        `review_status` 可传 `proposed` / `confirmed` 过滤。**默认不过滤**：
        当前还没有人工裁决入口（批次 5），只看 confirmed 会搜不到任何东西。
        但要知道 `confirmed` 才是可信的那一档。
        """
        owns_session = session is None
        session = session or SessionLocal()
        clauses = [
            "fc.capability_id = :cid",
            "f.status = 'active'",
            "m.status = 'active'",
        ]
        params: dict[str, object] = {"cid": capability_id, "limit": int(limit)}
        if review_status:
            clauses.append("fc.review_status = :rs")
            params["rs"] = review_status
        if constraint_key:
            # 条件命中「命中词表的正式键」或「未命中的原文」都算 ——
            # 后者尚未结构化，但它确实写在需求里，搜不到才是漏
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM requirement_version v
                    WHERE v.requirement_id = m.id AND v.status = 'current'
                      AND EXISTS (
                          SELECT 1 FROM jsonb_array_elements(v.constraint_snapshot) cst
                          WHERE cst->>'raw' = :ck OR cst->>'constraint_key' = :ck
                      )
                )
                """
            )
            params["ck"] = constraint_key.strip()
        try:
            rows = session.execute(
                text(
                    f"""
                    SELECT DISTINCT m.requirement_key, m.requirement_name, m.status,
                           m.current_version, fc.review_status,
                           c.action, c.object, c.display_name
                    FROM feature_capability fc
                    JOIN requirement_feature f ON f.id = fc.feature_id
                    JOIN requirement_master m ON m.id = f.requirement_id
                    JOIN capability c ON c.id = fc.capability_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY m.requirement_key
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
        finally:
            if owns_session:
                session.close()
        return [
            {
                "requirement_key": row["requirement_key"],
                "requirement_name": row["requirement_name"],
                "status": row["status"],
                "current_version": int(row["current_version"] or 0),
                "review_status": row["review_status"],
                "action": row["action"],
                "object": row["object"],
                "display_name": row["display_name"],
            }
            for row in rows
        ]

    def list_for_requirement(
        self,
        requirement_id: int,
        *,
        review_status: str | None = None,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        """某条需求下全部「功能 ↔ 能力」关联（含 feature 与能力的关键字段）。

        只返回**仍生效**的 feature（`status='active'`）—— 已删功能上的关联
        不该出现在当前视图里（它仍然留在表中，供历史版本查询）。
        """
        owns_session = session is None
        session = session or SessionLocal()
        clauses = ["f.requirement_id = :rid", "f.status = 'active'"]
        params: dict[str, object] = {"rid": requirement_id}
        if review_status:
            clauses.append("fc.review_status = :rs")
            params["rs"] = review_status
        try:
            rows = session.execute(
                text(
                    f"""
                    SELECT fc.feature_id, fc.capability_id, fc.raw_text, fc.confidence,
                           fc.review_status, fc.decided_by,
                           f.feature_key, f.content AS feature_content,
                           c.action, c.object, c.display_name, c.status AS capability_status
                    FROM feature_capability fc
                    JOIN requirement_feature f ON f.id = fc.feature_id
                    JOIN capability c ON c.id = fc.capability_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY f.ordinal, c.action, c.object
                    """
                ),
                params,
            ).mappings().all()
        finally:
            if owns_session:
                session.close()
        return [self._normalize(row) for row in rows]

    def list_for_feature(
        self, feature_id: int, *, session: Session | None = None
    ) -> list[dict[str, object]]:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT fc.feature_id, fc.capability_id, fc.raw_text, fc.confidence,
                           fc.review_status, fc.decided_by,
                           NULL::TEXT AS feature_key, NULL::TEXT AS feature_content,
                           c.action, c.object, c.display_name, c.status AS capability_status
                    FROM feature_capability fc
                    JOIN capability c ON c.id = fc.capability_id
                    WHERE fc.feature_id = :fid
                    ORDER BY c.action, c.object
                    """
                ),
                {"fid": feature_id},
            ).mappings().all()
        finally:
            if owns_session:
                session.close()
        return [self._normalize(row) for row in rows]

    def update_status(
        self,
        *,
        feature_id: int,
        capability_id: int,
        status: str,
        decided_by: str | None = None,
        session: Session | None = None,
    ) -> dict[str, object] | None:
        """人工裁决一条关联（confirmed / dismissed）。**这是能力正式成立的唯一入口。**"""
        owns_session = session is None
        session = session or SessionLocal()
        try:
            row = session.execute(
                text(
                    """
                    UPDATE feature_capability
                    SET review_status = :status, decided_by = :decided_by
                    WHERE feature_id = :fid AND capability_id = :cid
                    RETURNING feature_id, capability_id, review_status
                    """
                ),
                {"fid": feature_id, "cid": capability_id, "status": status, "decided_by": decided_by},
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
        if not row:
            return None
        return {
            # 这两个会作为 PATCH /feature-capabilities 的请求体参数回传，必须能安全回传。
            "feature_id": to_sid(row["feature_id"]),
            "capability_id": to_sid(row["capability_id"]),
            "review_status": row["review_status"],
        }

    @staticmethod
    def _normalize(row) -> dict[str, object]:
        return {
            # 这两个会作为 PATCH /feature-capabilities 的请求体参数回传，必须能安全回传。
            "feature_id": to_sid(row["feature_id"]),
            "capability_id": to_sid(row["capability_id"]),
            "feature_key": row.get("feature_key"),
            "feature_content": row.get("feature_content"),
            "action": row["action"],
            "object": row["object"],
            "display_name": row["display_name"],
            "capability_status": row.get("capability_status"),
            "raw_text": row.get("raw_text"),
            "confidence": float(row["confidence"]) if row.get("confidence") is not None else None,
            "review_status": row["review_status"],
            "decided_by": row.get("decided_by"),
        }
