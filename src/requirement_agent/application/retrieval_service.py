"""用于知识与相似度检索的检索服务。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from requirement_agent.infrastructure.db.repositories import RequirementFeatureRepository, RequirementMasterRepository
from requirement_agent.infrastructure.embedding.embedding_service import EmbeddingService
from requirement_agent.infrastructure.vector.pgvector_repository import RequirementVectorRepository


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
        """合并关键词 + 向量候选，按 score 排序并去重后返回。

        计分要点：查询 token 命中标题/摘要/feature 加权；整句命中加分；
        命中 feature 行再加分；用户新提交的 REQ（REQ-000*/REQ-00*）给予小幅加权，
        便于新需求原文在“待确认相似”中前置。filters 见 _matches_filters。
        """
        cleaned = (query or "").strip()
        if not cleaned:
            return []

        candidates: list[dict[str, object]] = []
        query_tokens = [token for token in cleaned.lower().split() if token]
        phrase = cleaned.lower()
        for item in self.master_repo.list_with_source_context():
            if not self._matches_filters(item, filters):
                continue
            title = str(item["requirement_name"])
            summary = str(item["final_requirement"])
            feature_contents = [str(value) for value in item.get("feature_contents") or []]
            haystack = f"{title} {summary} {' '.join(feature_contents)}".lower()
            matched_tokens = 0
            score = 0.0
            matched_features = [content for content in feature_contents if phrase and phrase in content.lower()]
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
            if any(token in haystack for token in ["登录", "权限", "审批", "报表", "支付", "导出", "验证码"]):
                score += 0.2
            if title.lower().startswith(cleaned.lower()[:4]):
                score += 0.15
            if self._is_user_submitted_requirement(str(item["requirement_key"])):
                score += 0.35
            if score > 0:
                candidates.append({
                    "requirement_key": item["requirement_key"],
                    "requirement_name": title,
                    "summary": summary,
                    "score": round(min(score, 1.0), 2),
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

        vector_results = self.search_by_vector(cleaned, limit=limit, filters=filters)
        for row in vector_results:
            key = str(row.get("requirement_key") or "")
            vector_score = float(row.get("score", 0.0))
            existing = next((item for item in candidates if item["requirement_key"] == key), None)
            if existing is None:
                candidates.append({
                    "requirement_key": key,
                    "requirement_name": row.get("title", "相关需求"),
                    "summary": row.get("summary", ""),
                    "score": vector_score,
                    "business_domain": row.get("business_domain", "general"),
                    "status": row.get("status", "active"),
                    "match_type": "vector",
                    "source_types": row.get("source_types", []),
                    "requester_names": row.get("requester_names", []),
                    "departments": row.get("departments", []),
                    "sensitivity_levels": row.get("sensitivity_levels", []),
                    "current_version": row.get("current_version", 0),
                    "feature_count": row.get("feature_count", 0),
                    "matched_features": [],
                })
            elif vector_score > float(existing.get("score", 0.0)):
                # 语义命中比关键词命中更相关时，用向量分覆盖并标记来源，让真实语义排序生效
                existing["score"] = vector_score
                existing["match_type"] = "vector"

        ranked = sorted(
            candidates,
            key=lambda item: (
                -float(item.get("score", 0.0)),
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
        except Exception:
            # 向量服务不可用：退化到关键词召回（上层 search() 合并）
            return []
        results = self.vector_repo.search(vector, limit=limit, filters=filters)
        normalized: list[dict[str, object]] = []
        for row in results:
            normalized.append({
                "requirement_key": row.get("requirement_key", ""),
                "title": row.get("title", "相关需求"),
                "summary": row.get("summary", ""),
                "score": float(row.get("score", 0.0)),
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
