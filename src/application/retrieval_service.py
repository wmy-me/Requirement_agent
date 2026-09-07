"""用于知识与相似度检索的检索服务。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from src.infrastructure.db.repositories import RequirementMasterRepository
from src.infrastructure.embedding.embedding_service import EmbeddingService
from src.infrastructure.vector.pgvector_repository import RequirementVectorRepository


class RetrievalService:
    """为需求记录提供关键词检索、语义检索与候选排序能力。"""

    def __init__(
        self,
        vector_repo: RequirementVectorRepository | None = None,
        embedding_service: EmbeddingService | None = None,
        master_repo: RequirementMasterRepository | None = None,
    ) -> None:
        self.vector_repo = vector_repo or RequirementVectorRepository()
        self.embedding_service = embedding_service or EmbeddingService()
        self.master_repo = master_repo or RequirementMasterRepository()

    def search(self, query: str, limit: int = 10, filters: Mapping[str, object] | None = None) -> list[dict[str, object]]:
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
            haystack = f"{title} {summary}".lower()
            matched_tokens = 0
            score = 0.0
            for token in query_tokens:
                if token in haystack:
                    matched_tokens += 1
                    score += 0.35
                    if token in title.lower():
                        score += 0.1
            if title.lower().find(phrase) >= 0 or summary.lower().find(phrase) >= 0:
                score += 0.4
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
                    "departments": item.get("departments", []),
                    "sensitivity_levels": item.get("sensitivity_levels", []),
                })

        vector_results = self.search_by_vector(cleaned, limit=limit, filters=filters)
        for row in vector_results:
            key = str(row.get("requirement_key") or "")
            if not any(item["requirement_key"] == key for item in candidates):
                candidates.append({
                    "requirement_key": key,
                    "requirement_name": row.get("title", "相关需求"),
                    "summary": row.get("summary", ""),
                    "score": float(row.get("score", 0.0)),
                    "business_domain": row.get("business_domain", "general"),
                    "status": row.get("status", "active"),
                    "match_type": "vector",
                    "source_types": row.get("source_types", []),
                    "departments": row.get("departments", []),
                    "sensitivity_levels": row.get("sensitivity_levels", []),
                })

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
        vector = self.embedding_service.embed(query)
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
        if not filters:
            return True
        normalized = {key: value for key, value in filters.items() if value not in (None, "")}
        if not normalized:
            return True

        checks = {
            "channel": item.get("source_types", []) if isinstance(item, dict) else [],
            "source_type": item.get("source_types", []) if isinstance(item, dict) else [],
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
        return True

    @staticmethod
    def _first_value(values: object, default: str) -> str:
        if isinstance(values, list) and values:
            return str(values[0])
        return default

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _is_user_submitted_requirement(requirement_key: str) -> bool:
        return requirement_key.startswith("REQ-000") or requirement_key.startswith("REQ-00")

    @staticmethod
    def _requirement_sort_rank(requirement_key: str) -> int:
        digits = "".join(ch for ch in requirement_key if ch.isdigit())
        if not digits:
            return 10**9
        return int(digits)
