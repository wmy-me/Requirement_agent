"""需求主线的**只读**查询服务（L3 层的查询内核）。

**为什么要有这一层。** 上一版工具层直接薄封装了 Repository —— 那是分层错误：
Repository 是数据访问边界，不是「查询用例」的边界。它缺了三样东西：

1. **跨仓储的组合**：取一条需求的详情要同时问 master repo 与 feature repo，
   这个编排不该由工具做（工具是薄封装，编排是应用层的职责）；
2. **业务语义的校验**：`at_version` 越界时仓储会**静默返回当前功能集**
   （SQL 判据 `origin_version_no <= N` 对未来版本恒真），必须在有业务语义的这一层拦；
3. **统一的输出形状**：工具要对模型输出稳定的字段，而仓储返回的是数据库行的形状。

**这一层是只读的 —— 一个写方法都没有，测试会断言这一点。**
需求主线的正式写入只有一条路：人工审核 → `ReviewService.submit_decision` →
`commit_requirement_node`（见 `docs/流程_需求从提交到入库.md`）。

**它不返回 ORM 对象、不返回 session、不返回原始行** —— 一律是普通 dict / list，
可以被直接序列化进模型上下文。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from requirement_agent.application.retrieval_service import RetrievalService
from requirement_agent.infrastructure.db.repositories import (
    CapabilityRepository,
    FeatureCapabilityRepository,
    RequirementFeatureRepository,
    RequirementMasterRepository,
    RequirementRelationRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)

# —— 硬上限 ——
# 这些不是「默认值」，是**上限**：调用方可以要得更少，但不能要得更多。
# 理由：L3 的结果最终会进模型上下文，无上限的查询等于把库倒进 prompt。
MAX_SEARCH_LIMIT = 10
MAX_LIST_LIMIT = 20
MAX_FEATURE_LIMIT = 50
MAX_VERSION_LIMIT = 20
MAX_RELATION_LIMIT = 30
MAX_SOURCE_LIMIT = 20
MAX_FEATURE_SEARCH_LIMIT = 20
MAX_STREAM_LIMIT = 20
MAX_CAPABILITY_CANDIDATES = 5


@dataclass(frozen=True, slots=True)
class QueryOutcome:
    """查询结果 + **「空」与「失败」的区分**。

    ⚠️ 上一版把所有「查不到」都当成错误返回。但「没有相似需求」「这条需求不存在」
    对模型来说是**合法答案**，不是工具故障 —— 混在一起会让模型以为工具坏了而反复重试。

    - `found=True` + 空列表 → 查了，没有（正常）
    - `found=False` → 目标对象不存在（也是正常答案，但语义不同：是「谁」不存在）
    """

    found: bool
    value: Any

    @staticmethod
    def hit(value: Any) -> "QueryOutcome":
        return QueryOutcome(found=True, value=value)

    @staticmethod
    def miss(value: Any = None) -> "QueryOutcome":
        """查了，但目标不存在。**不是错误。**"""
        return QueryOutcome(found=False, value=value)


class RequirementQueryService:
    """需求主线的只读查询（供 L3 工具与将来的通用助手使用）。

    所有方法：
    - **只读**：不写任何表、不开写事务；
    - **有硬上限**：`limit` 会被夹到本模块顶部的常量；
    - **返回普通结构**：dict / list，可直接序列化；
    - **不抛「查不到」**：用 `QueryOutcome.found` 表达，只有真正的数据访问失败才抛。
    """

    def __init__(
        self,
        retrieval_service: RetrievalService | None = None,
        master_repo: RequirementMasterRepository | None = None,
        feature_repo: RequirementFeatureRepository | None = None,
        version_repo: RequirementVersionRepository | None = None,
        relation_repo: RequirementRelationRepository | None = None,
        feature_capability_repo: FeatureCapabilityRepository | None = None,
        capability_repo: CapabilityRepository | None = None,
        source_repo: RequirementSourceRepository | None = None,
    ) -> None:
        self.retrieval_service = retrieval_service or RetrievalService()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.feature_repo = feature_repo or RequirementFeatureRepository()
        self.version_repo = version_repo or RequirementVersionRepository()
        self.relation_repo = relation_repo or RequirementRelationRepository()
        self.feature_capability_repo = feature_capability_repo or FeatureCapabilityRepository()
        self.capability_repo = capability_repo or CapabilityRepository()
        self.source_repo = source_repo or RequirementSourceRepository()

    # ── 检索 ──────────────────────────────────────────────────────────────

    def search_requirements(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        """按语义检索历史需求。

        **没有命中是完全正常的结果** —— 返回空列表，不是错误。
        """
        cleaned = (query or "").strip()
        if not cleaned:
            return []
        rows = self.retrieval_service.search(cleaned, limit=_clamp(limit, MAX_SEARCH_LIMIT))
        return [
            {
                "requirement_key": row.get("requirement_key"),
                "requirement_name": row.get("requirement_name") or row.get("title"),
                "similarity": row.get("score") or row.get("similarity"),
            }
            for row in rows
        ]

    # ── 取单条 ────────────────────────────────────────────────────────────

    def get_requirement(self, requirement_key: str) -> QueryOutcome:
        """取一条需求的基本情况（不含功能明细 —— 那个用 `get_features`）。"""
        master = self.master_repo.get_by_key(requirement_key)
        if master is None or master.id is None:
            return QueryOutcome.miss({"requirement_key": requirement_key})
        active = self.feature_repo.list_active(int(master.id))
        return QueryOutcome.hit(
            {
                "requirement_key": master.requirement_key,
                "requirement_name": master.requirement_name,
                "final_requirement": master.final_requirement,
                "current_version": int(master.current_version or 0),
                "status": master.status,
                "feature_count": len(active),
            }
        )

    # ── 功能明细 ──────────────────────────────────────────────────────────

    def get_features(
        self, requirement_key: str, at_version: int | None = None, limit: int = 30
    ) -> QueryOutcome:
        """某条需求（或它某个版本时刻）的功能明细。

        ⚠️ **`at_version` 必须在这里校验范围。** 仓储的判据是
        `origin_version_no <= N AND (removed_version_no IS NULL OR > N)` ——
        对**未来版本**这个条件对所有当前行都成立，于是 `at_version=99` 会
        **静默返回今天的功能集**，看起来像「v99 长这样」。
        那是假数据，而工具不能对模型说假话。（实测踩到过。）
        """
        master = self.master_repo.get_by_key(requirement_key)
        if master is None or master.id is None:
            return QueryOutcome.miss({"requirement_key": requirement_key})

        current_version = int(master.current_version or 0)
        if at_version is not None and at_version > current_version:
            # 不是「查不到」，是**参数指向一个不存在的版本** —— 由工具层翻成明确的错
            raise ValueError(
                f"{requirement_key} 目前只到 V{current_version}，没有 V{at_version}"
            )
        if at_version is not None and at_version < 1:
            raise ValueError("at_version 必须 ≥ 1")

        rows = self.feature_repo.list_by_requirement_key(requirement_key, at_version=at_version)
        return QueryOutcome.hit(
            {
                "requirement_key": requirement_key,
                "at_version": at_version,
                "count": len(rows),
                "truncated": len(rows) > limit,
                "features": [
                    {
                        "feature_key": row["feature_key"],
                        "content": row["content"],
                        "module": row.get("module_name"),
                        "origin_version_no": row.get("origin_version_no"),
                    }
                    for row in rows[: _clamp(limit, MAX_FEATURE_LIMIT)]
                ],
            }
        )

    # ── 版本 ──────────────────────────────────────────────────────────────

    def get_versions(self, requirement_key: str, limit: int = 10) -> QueryOutcome:
        """版本历史（新→旧）。**不含正文快照** —— 那个很大，要看用 `get_features`。"""
        rows = self.version_repo.list_by_requirement_key(requirement_key)
        if not rows:
            # 需求不存在、与「需求存在但没有版本」在数据上分不开，用 master 判一下
            master = self.master_repo.get_by_key(requirement_key)
            if master is None:
                return QueryOutcome.miss({"requirement_key": requirement_key})
        capped = rows[: _clamp(limit, MAX_VERSION_LIMIT)]
        return QueryOutcome.hit(
            {
                "requirement_key": requirement_key,
                "count": len(rows),
                "truncated": len(rows) > len(capped),
                "versions": [
                    {
                        "version_no": row.get("version_no"),
                        "change_type": row.get("change_type"),
                        "status": row.get("status"),
                        "change_summary": row.get("change_summary"),
                        "created_at": row.get("created_at"),
                        "based_on_version": row.get("parent_version_no"),
                        "change_count": len(row.get("feature_changes") or []),
                    }
                    for row in capped
                ],
            }
        )

    # ── 列举 ──────────────────────────────────────────────────────────────

    def list_requirements(
        self, limit: int = 10, status: str = "active"
    ) -> list[dict[str, object]]:
        """列举需求主线（按创建时间倒序）。

        **只返回编号、名称、当前版本、功能数** —— 不给正文。
        想回答「现在一共有哪些需求」用这个；想知道某条具体内容再调 `get_requirement`。
        """
        rows = self.master_repo.search("", limit=_clamp(limit, MAX_LIST_LIMIT))
        result = []
        for row in rows:
            if status and str(row.get("status") or "") != status:
                continue
            result.append(
                {
                    "requirement_key": row.get("requirement_key"),
                    "requirement_name": row.get("requirement_name"),
                    "current_version": row.get("current_version"),
                    "feature_count": row.get("feature_count"),
                    "status": row.get("status"),
                }
            )
        return result


    # ── 关系 ──────────────────────────────────────────────────────────────

    def list_relations(self, requirement_key: str, limit: int = 20) -> QueryOutcome:
        """该需求的关系边（双向：它指向别人的 + 别人指向它的）。

        ⚠️ `status='proposed'` 的是**模型的提议、还没经人工确认** —— 展示时必须标出来。
        """
        master = self.master_repo.get_by_key(requirement_key)
        if master is None:
            return QueryOutcome.miss({"requirement_key": requirement_key})
        rows = self.relation_repo.list_for_requirement(requirement_key)
        return QueryOutcome.hit(
            {
                "requirement_key": requirement_key,
                "count": len(rows),
                "truncated": len(rows) > limit,
                "relations": [
                    {
                        "other_requirement_key": row.get("other_requirement_key"),
                        "other_requirement_name": row.get("other_requirement_name"),
                        "relation_type": row.get("relation_type"),
                        "direction": row.get("direction"),
                        "similarity": row.get("similarity"),
                        "status": row.get("status"),
                        "reason": row.get("reason"),
                    }
                    for row in rows[: _clamp(limit, MAX_RELATION_LIMIT)]
                ],
            }
        )

    # ── 溯源：这一版的来源链 ──────────────────────────────────────────────

    def trace_sources(self, requirement_key: str, limit: int = 10) -> QueryOutcome:
        """这条需求的每一版**从哪些来源来**（渠道、发起人、原文摘要）。

        这是本系统里真实存在的跨实体关系（合并是「来源 → REQ」，见
        `docs/流程_需求从提交到入库.md`）。回滚产生的版本**没有来源**，其 `sources` 为空。
        """
        trace = self.version_repo.trace_by_requirement_key(requirement_key)
        if trace is None:
            return QueryOutcome.miss({"requirement_key": requirement_key})
        versions = (trace.get("versions") or [])[: _clamp(limit, MAX_VERSION_LIMIT)]
        return QueryOutcome.hit(
            {
                "requirement_key": requirement_key,
                "versions": [
                    {
                        "version_no": row.get("version_no"),
                        "change_type": row.get("change_type"),
                        "status": row.get("status"),
                        "sources": [
                            {
                                "source_id": item.get("source_id"),
                                "source_type": item.get("source_type"),
                                "requester_name": item.get("requester_name"),
                                "submitted_at": item.get("submitted_at"),
                                # 原文只给摘要 —— 完整原文是给人看的，进上下文会淹掉判断
                                "text_excerpt": str(item.get("original_text") or "")[:200],
                            }
                            for item in (row.get("sources") or [])
                        ],
                    }
                    for row in versions
                ],
            }
        )

    # ── 待审来源 ──────────────────────────────────────────────────────────

    def list_sources(self, status: str = "pending_review", limit: int = 10) -> list[dict[str, object]]:
        """列出来源（默认待审）。

        ⚠️ **只给编号、标题、渠道、时间 —— 不给正文与模型分析。**
        待审材料是**给审核人做判断用的**，全量喂给模型等于让它替人看材料、替人下结论。
        """
        rows = self.source_repo.list_by_status(status or "pending_review", limit=_clamp(limit, MAX_SOURCE_LIMIT))
        result = []
        for row in rows:
            metadata = row.get("metadata") or {}
            extracted = metadata.get("extracted") or {}
            title = str(extracted.get("requirement_title") or "").strip()
            if not title:
                first_line = str(row.get("original_text") or "").strip().splitlines()
                title = first_line[0] if first_line else ""
            result.append(
                {
                    "source_id": row.get("source_id"),
                    "title": title[:80],
                    "source_type": row.get("source_type"),
                    "requester_name": row.get("requester_name"),
                    "submitted_at": row.get("submitted_at"),
                }
            )
        return result

    # ── 风险 ──────────────────────────────────────────────────────────────

    def get_risks(self, requirement_key: str, limit: int = 5) -> QueryOutcome:
        """这条需求历史上被评过哪些风险。

        风险**不在需求主表上**，而在每版的 `diff_payload.risk` 里（提交时由分析结果写入）——
        所以「风险」天然是**按版本**的，这里按版本倒序给。
        """
        rows = self.version_repo.list_by_requirement_key(requirement_key)
        if not rows:
            master = self.master_repo.get_by_key(requirement_key)
            if master is None:
                return QueryOutcome.miss({"requirement_key": requirement_key})
        risks = []
        for row in rows[: _clamp(limit, MAX_VERSION_LIMIT)]:
            risk = (row.get("diff_payload") or {}).get("risk") or {}
            if not risk:
                continue
            risks.append(
                {
                    "version_no": row.get("version_no"),
                    "quality_risk": risk.get("quality_risk"),
                    "change_risk": risk.get("change_risk"),
                    "technical_impact_risk": risk.get("technical_impact_risk"),
                    "confidence": risk.get("confidence"),
                    "source": risk.get("source"),
                }
            )
        return QueryOutcome.hit({"requirement_key": requirement_key, "count": len(risks), "risks": risks})

    # ── 两版对比 ──────────────────────────────────────────────────────────

    def compare_versions(
        self, requirement_key: str, from_version: int, to_version: int
    ) -> QueryOutcome:
        """两版之间的功能级增删改。

        ⚠️ 与 `get_features` 同样的范围校验：版本号越界要**报错而不是给一份假数据**。
        """
        master = self.master_repo.get_by_key(requirement_key)
        if master is None:
            return QueryOutcome.miss({"requirement_key": requirement_key})
        current = int(master.current_version or 0)
        for label, value in (("from_version", from_version), ("to_version", to_version)):
            if value < 1 or value > current:
                raise ValueError(f"{label}={value} 越界：{requirement_key} 目前只到 V{current}")

        diff = self.feature_repo.diff_by_requirement_key(
            requirement_key, from_version=from_version, to_version=to_version
        )
        return QueryOutcome.hit(
            {
                "requirement_key": requirement_key,
                "from_version": diff.get("from_version"),
                "to_version": diff.get("to_version"),
                "summary": {
                    "added": len(diff.get("added") or []),
                    "removed": len(diff.get("removed") or []),
                    "modified": len(diff.get("modified") or []),
                    "unchanged": diff.get("unchanged"),
                },
                "added": [
                    {"feature_key": r.get("feature_key"), "content": r.get("content")}
                    for r in (diff.get("added") or [])[:MAX_FEATURE_LIMIT]
                ],
                "modified": [
                    {"feature_key": r.get("feature_key"), "before": r.get("before"), "after": r.get("after")}
                    for r in (diff.get("modified") or [])[:MAX_FEATURE_LIMIT]
                ],
                "removed": [
                    {"feature_key": r.get("feature_key"), "content": r.get("content")}
                    for r in (diff.get("removed") or [])[:MAX_FEATURE_LIMIT]
                ],
            }
        )

    # ── 跨需求搜功能 ──────────────────────────────────────────────────────

    def search_features(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        """按内容搜**功能条目**（跨需求）—— 精确到「哪条功能出现在哪条需求的哪一版」。

        与 `search_requirements` 的区别：那个搜的是**需求**，这个搜的是**功能**。
        问「哪些需求里有导出相关的能力」用它更准。
        """
        cleaned = (query or "").strip()
        if not cleaned:
            return []
        rows = self.retrieval_service.search_features(cleaned, limit=_clamp(limit, MAX_FEATURE_SEARCH_LIMIT))
        return [
            {
                "requirement_key": row.get("requirement_key"),
                "requirement_name": row.get("requirement_name"),
                "feature_key": row.get("feature_key"),
                "content": row.get("content"),
                "module": row.get("module_name"),
            }
            for row in rows
        ]

    # ── 按能力反查 ────────────────────────────────────────────────────────

    def search_by_capability(
        self, capability: str, constraint: str | None = None, review_status: str | None = None, limit: int = 10
    ) -> QueryOutcome:
        """按**能力名**反查哪些需求主线用到它。

        底层 `search_streams` 收的是能力 id，但调用方手里通常只有名字 —— 所以先解析。
        **匹配到多个能力时返回候选让调用方说清楚，不替它挑** —— 挑错会把两条不相干的需求
        混起来（「导出 Excel」与「导出 PDF」是两个能力）。

        ⚠️ **不按 `status='active'` 过滤**：库里的能力大多还是 `pending_confirmation`
        （AI 提议、未人工确认），按 active 筛等于什么都查不到（实测踩到过）。
        状态随结果一起返回，由调用方自己判断。
        """
        cleaned = (capability or "").strip()
        if not cleaned:
            return QueryOutcome.miss({"capability": capability})
        candidates = self.capability_repo.list(q=cleaned, limit=MAX_CAPABILITY_CANDIDATES)
        if not candidates:
            return QueryOutcome.miss({"capability": cleaned})
        if len(candidates) > 1:
            return QueryOutcome.miss(
                {
                    "need_disambiguation": True,
                    "capability": cleaned,
                    "candidates": [
                        {
                            "display_name": row.get("display_name"),
                            "action": row.get("action"),
                            "object": row.get("object"),
                            "status": row.get("status"),
                        }
                        for row in candidates
                    ],
                }
            )
        chosen = candidates[0]
        rows = self.feature_capability_repo.search_streams(
            capability_id=int(chosen["id"]),
            constraint_key=(constraint or "").strip() or None,
            review_status=review_status,
            limit=_clamp(limit, MAX_STREAM_LIMIT),
        )
        return QueryOutcome.hit(
            {
                "capability": chosen.get("display_name"),
                "capability_status": chosen.get("status"),
                "count": len(rows),
                "streams": [
                    {
                        "requirement_key": row.get("requirement_key"),
                        "requirement_name": row.get("requirement_name"),
                        "current_version": row.get("current_version"),
                        "review_status": row.get("review_status"),
                    }
                    for row in rows
                ],
            }
        )

    # ── 按条件反查 ────────────────────────────────────────────────────────

    def search_by_constraint(self, constraint: str, limit: int = 10) -> QueryOutcome:
        """按**限定条件**反查需求主线（「哪些需求要求按门店筛选」）。

        与 `search_by_capability` 是两个独立的查询轴：条件活在**版本快照**里，
        能力活在**功能关联**里，谁都不是谁的筛选条件（底层实现也不同）。
        """
        cleaned = (constraint or "").strip()
        if not cleaned:
            return QueryOutcome.miss({"constraint": constraint})
        rows = self.version_repo.search_by_constraint(cleaned, limit=_clamp(limit, MAX_LIST_LIMIT))
        return QueryOutcome.hit(
            {
                "constraint": cleaned,
                "count": len(rows),
                "streams": [
                    {
                        "requirement_key": row.get("requirement_key"),
                        "requirement_name": row.get("requirement_name"),
                        "constraint": row.get("constraint"),
                        "constraint_key": row.get("constraint_key"),
                    }
                    for row in rows
                ],
            }
        )



def _clamp(value: int, upper: int) -> int:
    """把 limit 夹进 [1, upper]。**上限是硬的，不是建议。**"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return min(10, upper)
    return max(1, min(number, upper))
