"""Retrieval service for knowledge and similarity search."""

from __future__ import annotations

from collections.abc import Mapping

from src.infrastructure.embedding.embedding_service import EmbeddingService
from src.infrastructure.vector.pgvector_repository import RequirementVectorRepository


class RetrievalService:
    """Backs semantic and exact retrieval from requirement records."""

    _catalog: list[dict[str, object]] = [
        {
            "requirement_key": "REQ-000001",
            "requirement_name": "用户登录",
            "summary": "支持邮箱和手机号登录，并支持验证码校验。",
            "business_domain": "auth",
            "status": "active",
        },
        {
            "requirement_key": "REQ-000002",
            "requirement_name": "角色权限管理",
            "summary": "支持用户角色分配、菜单权限控制与审批授权。",
            "business_domain": "auth",
            "status": "active",
        },
        {
            "requirement_key": "REQ-000003",
            "requirement_name": "订单支付审批",
            "summary": "订单支付需要审批流并支持退款审计。",
            "business_domain": "workflow",
            "status": "active",
        },
        {
            "requirement_key": "REQ-000004",
            "requirement_name": "报表导出",
            "summary": "支持报表查询、导出和筛选条件保存。",
            "business_domain": "report",
            "status": "active",
        },
    ]

    def __init__(
        self,
        vector_repo: RequirementVectorRepository | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.vector_repo = vector_repo or RequirementVectorRepository()
        self.embedding_service = embedding_service or EmbeddingService()

    def search(self, query: str, limit: int = 10, filters: Mapping[str, object] | None = None) -> list[dict[str, object]]:
        cleaned = (query or "").strip()
        if not cleaned:
            return []

        candidates: list[dict[str, object]] = []
        query_tokens = {token for token in cleaned.lower().split() if token}
        for item in self._catalog:
            if not self._matches_filters(item, filters):
                continue
            title = str(item.get("requirement_name", ""))
            summary = str(item.get("summary", ""))
            haystack = f"{title} {summary}".lower()
            score = 0.0
            for token in query_tokens:
                if token in haystack:
                    score += 0.35
            if any(token in haystack for token in ["登录", "权限", "审批", "报表", "支付", "导出"]):
                score += 0.25
            if title.lower().startswith(cleaned.lower()[:4]):
                score += 0.2
            if score > 0:
                candidates.append({
                    "requirement_key": item.get("requirement_key"),
                    "requirement_name": title,
                    "summary": summary,
                    "score": round(min(score, 1.0), 2),
                    "business_domain": item.get("business_domain"),
                    "status": item.get("status"),
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

        ranked = sorted(candidates, key=lambda item: float(item.get("score", 0.0)), reverse=True)
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

    def _matches_filters(self, item: Mapping[str, object], filters: Mapping[str, object] | None) -> bool:
        if not filters:
            return True
        for key, value in filters.items():
            if item.get(key) != value:
                return False
        return True
