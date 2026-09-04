"""用于知识与相似度检索的检索服务。"""

from __future__ import annotations

from collections.abc import Mapping

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
        for item in self.master_repo.list():
            if not self._matches_filters(item, filters):
                continue
            title = str(item.requirement_name)
            summary = str(item.final_requirement)
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
            if self._is_user_submitted_requirement(item.requirement_key):
                score += 0.35
            if score > 0:
                candidates.append({
                    "requirement_key": item.requirement_key,
                    "requirement_name": title,
                    "summary": summary,
                    "score": round(min(score, 1.0), 2),
                    "business_domain": "general",
                    "status": item.status,
                    "match_type": "keyword",
                })

        vector_results = self.search_by_vector(cleaned, limit=limit)
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

    def search_by_vector(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        vector = self.embedding_service.embed(query)
        results = self.vector_repo.search(vector, limit=limit)
        normalized: list[dict[str, object]] = []
        for row in results:
            normalized.append({
                "requirement_key": row.get("requirement_key", ""),
                "title": row.get("title", "相关需求"),
                "summary": row.get("summary", ""),
                "score": float(row.get("score", 0.0)),
                "business_domain": "general",
                "status": row.get("status", "active"),
            })
        return normalized[: max(1, min(limit, 10))]

    def _matches_filters(self, item: object, filters: Mapping[str, object] | None) -> bool:
        if not filters:
            return True
        for key, value in filters.items():
            if getattr(item, key, None) != value:
                return False
        return True

    @staticmethod
    def _is_user_submitted_requirement(requirement_key: str) -> bool:
        return requirement_key.startswith("REQ-000") or requirement_key.startswith("REQ-00")

    @staticmethod
    def _requirement_sort_rank(requirement_key: str) -> int:
        digits = "".join(ch for ch in requirement_key if ch.isdigit())
        if not digits:
            return 10**9
        return int(digits)
