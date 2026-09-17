"""用于知识与相似度检索的检索服务。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime

from requirement_agent.infrastructure.db.repositories import RequirementFeatureRepository, RequirementMasterRepository
from requirement_agent.infrastructure.embedding.embedding_service import EmbeddingService
from requirement_agent.infrastructure.vector.pgvector_repository import RequirementVectorRepository

logger = logging.getLogger(__name__)

# RRF（Reciprocal Rank Fusion）的排名平滑常数。
# 60 是 Cormack 等人提出的标准取值，本项目的规模下不需要调：它让「第 1 名 vs 第 2 名」
# 的差距远小于「第 1 名 vs 第 50 名」，于是某个榜单的头部不会压倒另一个榜单的整体共识。
_RRF_K = 60


class RetrievalService:
    """需求检索：关键词 + 向量混合召回，并统一排序、去重与过滤。

    关键词分支扫描 requirement_master 的标题/最终描述/feature 内容做多因子打分；
    向量分支走 pgvector 余弦相似度（依赖 embedding，缺失时自动退化关键词）。
    两条结果按 score 合并排序，供相似需求分析、前端检索与记忆召回复用。
    """

    def __init__(
        self,
        vector_repo: RequirementVectorRepository | None = None,
        embedding_service: EmbeddingService | None = None,
        master_repo: RequirementMasterRepository | None = None,
        feature_repo: RequirementFeatureRepository | None = None,
    ) -> None:
        self.vector_repo = vector_repo or RequirementVectorRepository()
        self.embedding_service = embedding_service or EmbeddingService()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.feature_repo = feature_repo or RequirementFeatureRepository()

    def search(self, query: str, limit: int = 10, filters: Mapping[str, object] | None = None) -> list[dict[str, object]]:
        """合并关键词 + 向量两条召回，按 **RRF 融合名次**排序后返回。

        ## 两条路的分工（B4 批 3 定下）

        - **关键词分**只用来在**关键词榜内**排名。它是「命中几个 token 乘以经验权重再加和」，
          量纲随查询词数、短语长度变化 —— 拿它当相似度是范畴错误。
        - **余弦**是唯一能回答「像不像」的量（判定见 `domain/similarity_scale.py`）。
        - 两者**不放在同一个尺度上比大小**。

        ## 为什么融合改成 RRF

        此前是「向量分高于关键词分就覆盖 `score`」—— 拿两个不同量纲的数直接比大小，
        于是同一个 `score` 字段有时候是余弦、有时候是手工加和，消费方无从分辨。
        实测就撞上过：查询「习惯打卡小程序」时关键词分封顶到 **1.0000**（看起来像
        「100% 相似」），实际那条的余弦只有 0.86。

        现在用 RRF：两个榜单各自的名次加权求和。**单位无关、不需要校准**，
        榜首的「绝对值有多高」不参与融合。

        ⚠️ **为什么不用 min-max 之类的归一化**：那会把**每个榜单的第一名都变成 1.0** ——
        于是一个关键词榜全是垃圾的查询也会产出一个 1.0。同一个病换个写法。

        ## `score` 的含义变了

        它现在是**名次分**（量级 ~0.01–0.03），不再是 0~1 的相似度样数字。
        字段名保留是为了不动既有消费者，但**任何把它当百分比渲染的地方都必须改**：
        要看相似度请读 `vector_similarity`，没有余弦就诚实地说没有。
        """
        cleaned = (query or "").strip()
        if not cleaned:
            return []

        keyword_rows = self._keyword_candidates(cleaned, filters)
        vector_rows = self.search_by_vector(cleaned, limit=limit, filters=filters)

        merged: dict[str, dict[str, object]] = {}
        for row in keyword_rows:
            merged[str(row["requirement_key"])] = row
        for row in vector_rows:
            key = str(row.get("requirement_key") or "")
            existing = merged.get(key)
            if existing is None:
                merged[key] = row
            else:
                # 同一个 key 两条路都召回 → 合成一条，两个分数都留着。
                # ⚠️ 余弦**无条件落上**，不能只在它「赢了」关键词分时才记 ——
                # 那样「关键词分更高、但确实有向量命中」的候选看起来和纯关键词命中
                # 一模一样，判定层会把它当 `unverifiable` 丢掉，而它其实有可用的余弦。
                existing["vector_similarity"] = row.get("vector_similarity")
                existing["match_type"] = "hybrid"

        # ── RRF：两个榜单各自排名，再按名次融合 ──
        for rank, row in enumerate(
            sorted(keyword_rows, key=lambda r: -float(r["keyword_score"])), start=1
        ):
            merged[str(row["requirement_key"])]["_kw_rank"] = rank
        for rank, row in enumerate(
            sorted(vector_rows, key=lambda r: -float(r.get("vector_similarity") or 0.0)), start=1
        ):
            merged[str(row["requirement_key"])]["_vec_rank"] = rank

        for row in merged.values():
            fused = 0.0
            keyword_rank = row.pop("_kw_rank", None)
            vector_rank = row.pop("_vec_rank", None)
            if keyword_rank:
                fused += 1.0 / (_RRF_K + float(keyword_rank))
            if vector_rank:
                fused += 1.0 / (_RRF_K + float(vector_rank))
            row["retrieval_score"] = fused
            row["score"] = fused  # 别名，见 docstring「`score` 的含义变了」

        ranked = sorted(
            merged.values(),
            key=lambda item: (
                -float(item.get("retrieval_score", 0.0)),
                # 用户新提交的 REQ（REQ-000*/REQ-00*）前置 —— 它是**排序偏好**，
                # 不是相似度，所以只在这里当排序键，不再往分里加。
                # 此前它同时做这两件事（分数里 +0.35、排序键里再来一次），是重复计数。
                -int(self._is_user_submitted_requirement(str(item.get("requirement_key") or ""))),
                self._requirement_sort_rank(str(item.get("requirement_key") or "")),
            ),
        )
        deduped: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in ranked:
            key = str(item.get("requirement_key") or "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[: max(1, min(limit, 20))]

    def _keyword_candidates(
        self, query: str, filters: Mapping[str, object] | None
    ) -> list[dict[str, object]]:
        """关键词榜。

        ⚠️ `keyword_score` **只用来在这个榜里排名**，不要当相似度读，
        也不要用它跟余弦比大小。下面的权重是历年累积的经验值，不是校准出来的。

        B4 批 3 删掉了其中一条：原先只要候选正文含
        `["登录","权限","审批","报表","支付","导出","验证码"]` 之一就无条件 `+0.2` ——
        它与查询**完全无关**，而这七个词覆盖了绝大多数中文业务需求。
        实测它确实改变排序（REQ-000001/002 命中、REQ-000013/015 不命中），
        但「为什么这条排在前面」在界面上看不见、在注释里也没写。
        它不是召回手段（召回由 token 命中负责），纯粹是噪声。
        """
        candidates: list[dict[str, object]] = []
        query_tokens = [token for token in query.lower().split() if token]
        phrase = query.lower()
        for item in self.master_repo.list_with_source_context():
            if not self._matches_filters(item, filters):
                continue
            title = str(item["requirement_name"])
            summary = str(item["final_requirement"])
            feature_contents = [str(value) for value in item.get("feature_contents") or []]
            haystack = f"{title} {summary} {' '.join(feature_contents)}".lower()
            matched_tokens = 0
            score = 0.0
            matched_features = [
                content for content in feature_contents if phrase and phrase in content.lower()
            ]
            for token in query_tokens:
                if token in haystack:
                    matched_tokens += 1
                    score += 0.35
                    if token in title.lower():
                        score += 0.1
                    if any(token in content.lower() for content in feature_contents):
                        score += 0.1
            if title.lower().find(phrase) >= 0 or summary.lower().find(phrase) >= 0:
                score += 0.4
            if matched_features:
                score += min(0.3, 0.12 * len(matched_features))
            if matched_tokens and matched_tokens == len(query_tokens):
                score += 0.25
            # ⚠️ 这里曾有一条与查询无关的 `+0.2`（命中固定词表），已删除，理由见 docstring。
            # 另有一条 `title[:4] 前缀相同 +0.15`：对中文来说是极粗的启发，**暂留**
            # （本批次的范围是删掉那条完全与查询无关的），但它是同类问题，将来应一并处理。
            if title.lower().startswith(phrase[:4]):
                score += 0.15
            if score > 0:
                candidates.append({
                    "requirement_key": item["requirement_key"],
                    "requirement_name": title,
                    "summary": summary,
                    "keyword_score": round(min(score, 1.0), 2),
                    # 关键词命中**没有余弦**；同一条若也被向量召回，合并时补上。
                    "vector_similarity": None,
                    "business_domain": self._first_value(item.get("business_domains"), "general"),
                    "status": item["status"],
                    "match_type": "keyword",
                    "source_types": item.get("source_types", []),
                    "requester_names": item.get("requester_names", []),
                    "departments": item.get("departments", []),
                    "sensitivity_levels": item.get("sensitivity_levels", []),
                    "current_version": item.get("current_version", 0),
                    "feature_count": item.get("feature_count", 0),
                    "matched_features": matched_features[:3],
                })
        return candidates

    def search_by_vector(
        self,
        query: str,
        limit: int = 10,
        filters: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        """纯向量召回：embedding 后按余弦相似度检索，返回归一化候选。

        向量缺失/服务不可用时此分支不报错，返回空候选，由上层合并逻辑退化为关键词。
        """
        try:
            vector = self.embedding_service.embed(query)
        except Exception as exc:
            # 向量服务不可用：退化到关键词召回（上层 search() 合并）
            logger.warning("event=vector_recall_fallback error=%s", exc)
            return []
        results = self.vector_repo.search(vector, limit=limit, filters=filters)
        normalized: list[dict[str, object]] = []
        for row in results:
            cosine = float(row.get("score", 0.0))
            normalized.append({
                "requirement_key": row.get("requirement_key", ""),
                "title": row.get("title", "相关需求"),
                "summary": row.get("summary", ""),
                # 这一路只有余弦，没有关键词分；融合名次 `retrieval_score` 由 `search()` 统一算。
                "vector_similarity": cosine,
                "keyword_score": None,
                "match_type": "vector",
                "business_domain": row.get("business_domain", "general"),
                "status": row.get("status", "active"),
                "source_types": row.get("source_types", []),
                "departments": row.get("departments", []),
                "sensitivity_levels": row.get("sensitivity_levels", []),
            })
        return normalized[: max(1, min(limit, 10))]

    def _matches_filters(self, item: object, filters: Mapping[str, object] | None) -> bool:
        """对单个候选做多维过滤：渠道/来源、输入人、部门、领域、密级、提交时间区间、版本号下限。

        所有维度均走“候选聚合值集合”与期望值比较；任一不匹配即剔除。
        时间用 latest_source_submitted_at 与 submitted_from/to 比较。
        """
        if not filters:
            return True
        normalized = {key: value for key, value in filters.items() if value not in (None, "")}
        if not normalized:
            return True

        checks = {
            "channel": item.get("source_types", []) if isinstance(item, dict) else [],
            "source_type": item.get("source_types", []) if isinstance(item, dict) else [],
            "requester": item.get("requester_names", []) if isinstance(item, dict) else [],
            "department": item.get("departments", []) if isinstance(item, dict) else [],
            "business_domain": item.get("business_domains", []) if isinstance(item, dict) else [],
            "sensitivity_level": item.get("sensitivity_levels", []) if isinstance(item, dict) else [],
        }
        for key, candidates in checks.items():
            expected = normalized.get(key)
            if expected and str(expected) not in {str(candidate) for candidate in candidates}:
                return False

        submitted_from = normalized.get("submitted_from")
        submitted_to = normalized.get("submitted_to")
        latest_submitted = item.get("latest_source_submitted_at") if isinstance(item, dict) else None
        if latest_submitted and submitted_from and self._parse_time(str(latest_submitted)) < self._parse_time(str(submitted_from)):
            return False
        if latest_submitted and submitted_to and self._parse_time(str(latest_submitted)) > self._parse_time(str(submitted_to)):
            return False
        has_version_ge = normalized.get("has_version_ge")
        current_version = int(item.get("current_version") or 0) if isinstance(item, dict) else 0
        if has_version_ge is not None and current_version < int(has_version_ge):
            return False
        return True

    def search_features(
        self,
        query: str,
        *,
        status: str | None = None,
        requester: str | None = None,
        has_version_ge: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """按 feature 行级检索：精确到“哪条功能出现在哪个 REQ 的哪个版本”。

        供功能溯源/表格明细使用；支持状态、输入人、版本号下限筛选。
        """
        cleaned = (query or "").strip()
        if not cleaned:
            return []
        return self.feature_repo.search_features(
            cleaned,
            status=status,
            requester=requester,
            has_version_ge=has_version_ge,
            limit=limit,
        )

    @staticmethod
    def _first_value(values: object, default: str) -> str:
        if isinstance(values, list) and values:
            return str(values[0])
        return default

    @staticmethod
    def _parse_time(value: str) -> datetime:
        """解析时间串为展示时区的 aware datetime，naive 视为展示时区本地时间。"""
        from requirement_agent.common.time import parse_display_time

        return parse_display_time(value)

    @staticmethod
    def _is_user_submitted_requirement(requirement_key: str) -> bool:
        return requirement_key.startswith("REQ-000") or requirement_key.startswith("REQ-00")

    @staticmethod
    def _requirement_sort_rank(requirement_key: str) -> int:
        digits = "".join(ch for ch in requirement_key if ch.isdigit())
        if not digits:
            return 10**9
        return int(digits)
