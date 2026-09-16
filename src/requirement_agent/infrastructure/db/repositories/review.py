"""审核 Repository：人工审核记录 + 功能条目(feature)级版本治理。

- `RequirementReviewRepository`：审核决策留痕。
- `RequirementFeatureRepository`：feature 行的创建/同步/人工裁决、版本 diff 与溯源。
"""

from __future__ import annotations

import hashlib
import json

from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.common.snowflake import new_id, to_sid
from requirement_agent.domain.feature_diff import PlannedRow, normalize_feature_rows, plan_sync
from requirement_agent.domain.feature_history import (
    FeatureState,
    PlannedRevertRow,
    reconstruct_features_at_version,
)
from requirement_agent.domain.requirement import RequirementReview
from requirement_agent.infrastructure.db.session import SessionLocal


def _revert_provenance(version_no: int, requirement_key: str, kind: str) -> dict[str, object]:
    """回滚产生的 provenance 记录。

    `source_id` 恒为 `None`：回滚没有来源（它是人对库的操作，不是某条来源落库的结果）。
    如实留空比编一个强 —— 读者能一眼看出这条变更不是来源驱动的。
    """
    return {
        "version_no": version_no,
        "source_id": None,
        "requirement_key": requirement_key,
        "kind": kind,
    }


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
                        id, source_id, analysis_snapshot, decision, reviewer_id, reviewer_name,
                        review_comment, edited_requirement
                    ) VALUES (
                        :id, :source_id, :analysis_snapshot, :decision, :reviewer_id, :reviewer_name,
                        :review_comment, :edited_requirement
                    )
                    """
                ),
                {
                    "id": new_id(),
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
                       origin_source_id, origin_requirement_key, origin_version_no, removed_version_no, provenance,
                       module_key, module_name
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
        features: list[str] | list[dict[str, object]],
        *,
        source_id: int | None,
        requirement_key: str,
        version_no: int,
        session: Session,
    ) -> list[dict[str, object]]:
        """新建 REQ 时把来源的功能行整体落为 features（每个新 feature 记 provenance=add）。

        `features` 元素可以是字符串（不归属任何模块），也可以是
        `{"content", "module_key", "module_name"}` 的 dict（携带模块标签）。
        """
        created: list[dict[str, object]] = []
        for ordinal, row in enumerate(normalize_feature_rows(features), start=1):
            content = row.content
            module_key = row.module_key
            module_name = row.module_name
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
                        id, requirement_id, feature_key, content, status, ordinal,
                        origin_source_id, origin_requirement_key, origin_version_no, provenance, content_hash,
                        module_key, module_name
                    ) VALUES (
                        :id, :requirement_id, :feature_key, :content, 'active', :ordinal,
                        :origin_source_id, :origin_requirement_key, :origin_version_no, CAST(:provenance AS JSONB), :content_hash,
                        :module_key, :module_name
                    )
                    RETURNING id, requirement_id, feature_key, content, status, ordinal,
                              origin_source_id, origin_requirement_key, origin_version_no, removed_version_no, provenance,
                              module_key, module_name
                    """
                ),
                {
                    "id": new_id(),
                    "requirement_id": requirement_id,
                    "feature_key": feature_key,
                    "content": content,
                    "ordinal": ordinal,
                    "origin_source_id": source_id,
                    "origin_requirement_key": requirement_key,
                    "origin_version_no": version_no,
                    "provenance": json.dumps(provenance),
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "module_key": module_key,
                    "module_name": module_name,
                },
            ).mappings().one()
            created.append(self._row_to_feature(row))
            ordinal += 1
        return created

    def sync_features(
        self,
        requirement_id: int,
        features: list[str] | list[dict[str, object]],
        *,
        source_id: int | None,
        requirement_key: str,
        version_no: int,
        prune: bool = False,
        session: Session,
    ) -> list[dict[str, object]]:
        """无人工 overrides 时，把来源功能行同步进既有 REQ，返回本版本的变更清单。

        匹配规则全部在 `requirement_agent.domain.feature_diff.plan_sync`（纯函数、可单测），
        这里只负责把结论落库。**不要在这里重新引入「按 ordinal 配对」的匹配** ——
        那样的实现在中间插入/删除一行时，会把其后每一行都判成 modify 并真实覆写
        `content` 与 `content_hash`，污染会逐轮累积。

        默认 `prune=False`：来源没提到的现有功能**保留**（合并语义是并集）；`prune=True`
        时未命中的现有行软删（回滚语义是以来源为准整体替换）。
        """
        existing = self.list_active(requirement_id, session=session)
        by_id = {int(item["id"]): item for item in existing}
        plan = plan_sync(existing, normalize_feature_rows(features), prune=prune)
        changes: list[dict[str, object]] = []

        for item in plan:
            if item.op == "add":
                feature_key = self._next_feature_key(
                    requirement_id,
                    ordinal=item.ordinal,
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
                            id, requirement_id, feature_key, content, status, ordinal,
                            origin_source_id, origin_requirement_key, origin_version_no, provenance, content_hash,
                            module_key, module_name
                        ) VALUES (
                            :id, :requirement_id, :feature_key, :content, 'active', :ordinal,
                            :origin_source_id, :origin_requirement_key, :origin_version_no, CAST(:provenance AS JSONB), :content_hash,
                            :module_key, :module_name
                        )
                        """
                    ),
                    {
                        "id": new_id(),
                        "requirement_id": requirement_id,
                        "feature_key": feature_key,
                        "content": item.content,
                        "ordinal": item.ordinal,
                        "origin_source_id": source_id,
                        "origin_requirement_key": requirement_key,
                        "origin_version_no": version_no,
                        "provenance": json.dumps(provenance),
                        "content_hash": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                        "module_key": item.module_key,
                        "module_name": item.module_name,
                    },
                )
                changes.append({"op": "add", "feature_key": feature_key, "content": item.content})
                continue

            current = by_id.get(int(item.feature_id or 0))
            if current is None:
                # plan 由 existing 算出，正常不会缺；真缺了说明该行被并发改动，
                # 跳过比照着一个不存在的 id 写更强。
                continue

            provenance = list(current.get("provenance") or [])
            if item.op == "delete":
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
                        "id": current["id"],
                        "removed_version_no": version_no,
                        "provenance": json.dumps(provenance),
                    },
                )
                changes.append(
                    {
                        "op": "delete",
                        "feature_key": current["feature_key"],
                        "content": current["content"],
                    }
                )
                continue

            if item.op == "modify":
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
                            content_hash = :content_hash,
                            module_key = :module_key,
                            module_name = :module_name,
                            status = 'active',
                            removed_version_no = NULL,
                            provenance = CAST(:provenance AS JSONB),
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": current["id"],
                        "content": item.content,
                        "content_hash": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                        "module_key": item.module_key,
                        "module_name": item.module_name,
                        "provenance": json.dumps(provenance),
                    },
                )
                change: dict[str, object] = {
                    "op": "modify",
                    "feature_key": current["feature_key"],
                    "before": item.before,
                    "after": item.content,
                }
                if item.module_before is not None:
                    # 模块标签跟着变了：附在 modify 记录上，**不新增 op 值**，
                    # 免得 `_primary_change_type` 与前端 diff 渲染要认得新枚举。
                    change["module_before"] = item.module_before[0]
                    change["module_after"] = item.module_key
                changes.append(change)
                continue

            # keep：内容没变。仍要写回模块标签（可能是从「无模块」补上的）与序号。
            session.execute(
                text(
                    """
                    UPDATE requirement_feature
                    SET ordinal = :ordinal,
                        module_key = :module_key,
                        module_name = :module_name,
                        status = 'active',
                        removed_version_no = NULL,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": current["id"],
                    "ordinal": item.ordinal,
                    "module_key": item.module_key,
                    "module_name": item.module_name,
                },
            )

        return changes

    def reconcile_features(
        self,
        requirement_id: int,
        plan: list[PlannedRevertRow],
        *,
        requirement_key: str,
        version_no: int,
        session: Session,
    ) -> list[dict[str, object]]:
        """把功能行按 `plan` 对齐到「目标版本时刻」的样子（**回滚专用**），返回 `feature_changes`。

        与 `sync_features` 的关键差别：**不做内容匹配**。回滚的目标集合来自
        `domain.feature_history.reconstruct_features_at_version`，每条的 `feature_key` 是
        **已知的**，没有「猜谁是谁」的空间 —— 也正因如此不该复用 `plan_sync`：它是靠内容哈希
        猜身份，一旦现有集合里混入已软删的同内容行就会挑错配对，一删一活、两条行都错。

        三条与 `sync_features` **刻意不同**的写库规则，每条都有理由：

        1. `add` 且命中**已软删**的行 → UPDATE **复活原行**，不新建。新建会让 `feature_key`
           断裂，更要紧的是 `feature_capability.feature_id` 会指向那条不再生效的旧行，
           而 `list_for_requirement` 只认 `status='active'` —— 复活后这条功能的能力标签会凭空消失。
        2. `delete` 带 `AND status='active'` 守卫。少了它，对一行已经软删的行重复判定会
           **重写它的 `removed_version_no`**，静默污染「它当初是哪一版删的」这段历史。
        3. `keep` **不写** `status` / `removed_version_no`。`sync_features` 的 keep 分支会写
           `status='active'`，那是为并集语义服务的；回滚里若照抄，就会把一批不该复活的行
           集体复活。

        `provenance` 的 `source_id` 为 `None`：回滚没有来源，如实留空比编一个强。
        `kind` 只用既有的 `add` / `modify` / `delete` 三值，不新增枚举。
        """
        by_id = {int(item.row_id): item for item in plan if item.row_id is not None}
        changes: list[dict[str, object]] = []

        for item in plan:
            if item.op == "add":
                if item.row_id is None:
                    # 表里没有这个 key（防御路径）：用计划里的 key 建行，**不走
                    # `_next_feature_key`** —— 那个是「给全新内容分配新号」用的，
                    # 会把一个已知身份的行改成另一个身份，溯源链就断了。
                    session.execute(
                        text(
                            """
                            INSERT INTO requirement_feature (
                                id, requirement_id, feature_key, content, status, ordinal,
                                origin_source_id, origin_requirement_key, origin_version_no,
                                provenance, content_hash, module_key, module_name
                            ) VALUES (
                                :id, :requirement_id, :feature_key, :content, 'active', :ordinal,
                                NULL, :requirement_key, :version_no,
                                CAST(:provenance AS JSONB), :content_hash, :module_key, :module_name
                            )
                            """
                        ),
                        {
                            "id": new_id(),
                            "requirement_id": requirement_id,
                            "feature_key": item.feature_key,
                            "content": item.content,
                            "ordinal": item.ordinal,
                            "requirement_key": requirement_key,
                            "version_no": version_no,
                            "provenance": json.dumps([_revert_provenance(version_no, requirement_key, "add")]),
                            "content_hash": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                            "module_key": item.module_key,
                            "module_name": item.module_name,
                        },
                    )
                else:
                    # 复活已软删的原行（就地 UPDATE），保住 id / feature_key / 能力关联。
                    row = by_id[item.row_id]
                    provenance = self._provenance_for(item.row_id, session=session)
                    provenance.append(_revert_provenance(version_no, requirement_key, "add"))
                    session.execute(
                        text(
                            """
                            UPDATE requirement_feature
                            SET content = :content,
                                content_hash = :content_hash,
                                ordinal = :ordinal,
                                module_key = :module_key,
                                module_name = :module_name,
                                status = 'active',
                                removed_version_no = NULL,
                                provenance = CAST(:provenance AS JSONB),
                                updated_at = NOW()
                            WHERE id = :id
                            """
                        ),
                        {
                            "id": row.row_id,
                            "content": item.content,
                            "content_hash": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                            "ordinal": item.ordinal,
                            "module_key": item.module_key,
                            "module_name": item.module_name,
                            "provenance": json.dumps(provenance),
                        },
                    )
                changes.append({"op": "add", "feature_key": item.feature_key, "content": item.content})
                continue

            if item.op == "delete":
                provenance = self._provenance_for(item.row_id, session=session)
                provenance.append(_revert_provenance(version_no, requirement_key, "delete"))
                session.execute(
                    text(
                        """
                        UPDATE requirement_feature
                        SET status = 'deleted',
                            removed_version_no = :version_no,
                            provenance = CAST(:provenance AS JSONB),
                            updated_at = NOW()
                        WHERE id = :id AND status = 'active'
                        """
                    ),
                    {
                        "id": item.row_id,
                        "version_no": version_no,
                        "provenance": json.dumps(provenance),
                    },
                )
                changes.append({"op": "delete", "feature_key": item.feature_key, "content": item.content})
                continue

            if item.op == "modify":
                provenance = self._provenance_for(item.row_id, session=session)
                provenance.append(_revert_provenance(version_no, requirement_key, "modify"))
                session.execute(
                    text(
                        """
                        UPDATE requirement_feature
                        SET content = :content,
                            content_hash = :content_hash,
                            ordinal = :ordinal,
                            module_key = :module_key,
                            module_name = :module_name,
                            status = 'active',
                            removed_version_no = NULL,
                            provenance = CAST(:provenance AS JSONB),
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": item.row_id,
                        "content": item.content,
                        "content_hash": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                        "ordinal": item.ordinal,
                        "module_key": item.module_key,
                        "module_name": item.module_name,
                        "provenance": json.dumps(provenance),
                    },
                )
                change: dict[str, object] = {
                    "op": "modify",
                    "feature_key": item.feature_key,
                    "before": item.before,
                    "after": item.content,
                }
                if item.module_before is not None:
                    change["module_before"] = item.module_before[0]
                    change["module_after"] = item.module_key
                changes.append(change)
                continue

            # keep：内容没变，只可能要对齐 ordinal 与模块标签。
            # **不写 status / removed_version_no** —— 见 docstring 第 3 条。
            if item.row_id is not None:
                session.execute(
                    text(
                        """
                        UPDATE requirement_feature
                        SET ordinal = :ordinal,
                            module_key = :module_key,
                            module_name = :module_name,
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": item.row_id,
                        "ordinal": item.ordinal,
                        "module_key": item.module_key,
                        "module_name": item.module_name,
                    },
                )

        return changes

    def _provenance_for(self, row_id: int | None, *, session: Session) -> list[dict[str, object]]:
        """取某行的现有 provenance（读改写，避免覆盖掉此前的变更史）。"""
        if row_id is None:
            return []
        row = session.execute(
            text("SELECT provenance FROM requirement_feature WHERE id = :id"), {"id": row_id}
        ).mappings().first()
        return list((row or {}).get("provenance") or [])

    def preview_sync(
        self,
        requirement_id: int,
        features: list[str] | list[dict[str, object]],
        *,
        prune: bool = False,
        session: Session | None = None,
    ) -> list[PlannedRow]:
        """只读预演：与 `sync_features` 同一内核、同一份现有行，**不写库**。

        这是「预览不撒谎」的实现基础 —— 预览与落库走的是同一个 `plan_sync`，
        区别只在于一个把结论写下去、一个只返回。
        """
        owns_session = session is None
        session = session or SessionLocal()
        try:
            existing = self.list_active(requirement_id, session=session)
        finally:
            if owns_session:
                session.close()
        return plan_sync(existing, normalize_feature_rows(features), prune=prune)

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
                            id, requirement_id, feature_key, content, status, ordinal,
                            origin_source_id, origin_requirement_key, origin_version_no, provenance, content_hash,
                            module_key, module_name
                        ) VALUES (
                            :id, :requirement_id, :feature_key, :content, 'active', :ordinal,
                            :origin_source_id, :origin_requirement_key, :origin_version_no, CAST(:provenance AS JSONB), :content_hash,
                            :module_key, :module_name
                        )
                        """
                    ),
                    {
                        "id": new_id(),
                        "requirement_id": requirement_id,
                        "feature_key": next_key,
                        "content": content,
                        "ordinal": max_ordinal,
                        "origin_source_id": source_id,
                        "origin_requirement_key": requirement_key,
                        "origin_version_no": version_no,
                        "provenance": json.dumps(provenance),
                        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        # 人工新增的行也可以指定模块（与 create_features 同口径）
                        "module_key": str(raw.get("module_key") or "").strip() or None,
                        "module_name": str(raw.get("module_name") or "").strip() or None,
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
        """按 REQ 编号列 feature，支持指定版本生效区间或包含已删除项。

        ⚠️ `at_version=N` 返回的是**N 版本时刻的成员与内容**——不只是「当时哪些行存在」，
        `content` / `module_*` 也回到当时的值（由 `_list_rows_at_version` 沿
        `feature_changes` 反向回放得到）。这一点与 `include_deleted` 互斥：
        给了 `include_deleted` 就以它为准、不再做版本复原（保持既有行为）。
        """
        if at_version is not None and not include_deleted:
            return self._list_rows_at_version(requirement_key, at_version, session=session)
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
                       f.origin_source_id, f.origin_requirement_key, f.origin_version_no, f.removed_version_no, f.provenance,
                       f.module_key, f.module_name
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

    def list_feature_changes_after(
        self,
        *,
        requirement_id: int,
        after_version_no: int = 0,
        session: Session | None = None,
    ) -> list[dict[str, object]]:
        """取某主线**版本号大于** `after_version_no` 的 `feature_changes`（升序）。

        历史逆放的原料。刻意与 `list_by_requirement_key` 同住一个类：复原的读取与
        功能行的读取是同一份事实的两个入口，分到两个 repo 只会制造第二处需要同步的 owner。
        """
        owns_session = session is None
        session = session or SessionLocal()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT version_no, feature_changes
                    FROM requirement_version
                    WHERE requirement_id = :requirement_id AND version_no > :after_version_no
                    ORDER BY version_no ASC
                    """
                ),
                {"requirement_id": requirement_id, "after_version_no": after_version_no},
            ).mappings().all()
            return [
                {"version_no": int(row["version_no"]), "feature_changes": list(row["feature_changes"] or [])}
                for row in rows
            ]
        finally:
            if owns_session:
                session.close()

    def _list_rows_at_version(
        self,
        requirement_key: str,
        at_version: int,
        *,
        session: Session | None,
    ) -> list[dict[str, object]]:
        """某版本时刻的功能行：读当前**全部**行（含已软删）+ 其后的变更记录，交给
        `domain.feature_history` 反向回放。

        为什么不能只靠 SQL 的 `origin_version_no / removed_version_no` 区间：那两个列只描述
        **成员区间**，`content` 是就地覆写的当前值 —— 只按区间筛，得到的会是「当时存在哪些
        功能 + 今天的文字」。差集就出在这里。

        输出的字段集与不走 `at_version` 时**逐字段一致**（静态列取当前行），差异只在
        `content`/`module_*`/`ordinal` 与「当时是否存活」。
        已知近似：`provenance` 仍是截至当前的累积，不是当时的（既有实现也是如此，前端不渲染）。
        """
        owns_session = session is None
        session = session or SessionLocal()
        try:
            current = self.list_by_requirement_key(requirement_key, include_deleted=True, session=session)
            if not current:
                return []
            history = self.list_feature_changes_after(
                requirement_id=int(current[0]["requirement_id"]),
                after_version_no=at_version,
                session=session,
            )
            states = reconstruct_features_at_version(current, history, target_version=at_version)
            by_key = {str(row["feature_key"]): row for row in current}
            return [self._state_to_row(state, by_key.get(state.feature_key)) for state in states]
        finally:
            if owns_session:
                session.close()

    @staticmethod
    def _state_to_row(
        state: FeatureState,
        current_row: dict[str, object] | None,
    ) -> dict[str, object]:
        """把复原出来的动态字段与当前行的静态字段合回一行。

        `current_row` 为 None 只可能出现在「记录里有、表里没有」的防御路径上
        （软删时代不该发生）；此时静态列填 None 而不是丢行 —— 丢行会让复原**静默少一条**，
        而少一条比多一条难发现得多。
        """
        base: dict[str, object] = (
            dict(current_row)
            if current_row is not None
            else {
                "id": None,
                "requirement_id": None,
                "origin_source_id": None,
                "origin_requirement_key": None,
                "origin_version_no": None,
                "provenance": [],
            }
        )
        base.update(
            {
                "feature_key": state.feature_key,
                "content": state.content,
                "ordinal": state.ordinal,
                "module_key": state.module_key,
                "module_name": state.module_name,
                # 复原出来的都是「当时存活」的行。
                "status": "active",
                "removed_version_no": None,
            }
        )
        return base

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
                       f.module_key, f.module_name,
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
        # 雪花 ID 一律字符串化（见 `common.snowflake.to_sid` 的说明）：
        # `id`/`requirement_id`/`origin_source_id` 以及 provenance 里的 `source_id`
        # 都会以 JSON number 发出去，而它们全部超过 2^53。`ordinal`/`origin_version_no`
        # 是**序号不是 ID**，保持数字。
        return {
            "id": to_sid(row["id"]),
            "requirement_id": to_sid(row["requirement_id"]),
            "feature_key": row["feature_key"],
            "content": row["content"],
            "status": row["status"],
            "ordinal": int(row["ordinal"]),
            "origin_source_id": to_sid(row["origin_source_id"]),
            "origin_requirement_key": row["origin_requirement_key"],
            "origin_version_no": int(row["origin_version_no"]),
            "removed_version_no": row["removed_version_no"],
            "provenance": [
                {**dict(entry), "source_id": to_sid(dict(entry).get("source_id"))}
                if isinstance(entry, Mapping)
                else entry
                for entry in (row["provenance"] or [])
            ],
            "module_key": row.get("module_key"),
            "module_name": row.get("module_name"),
        }

