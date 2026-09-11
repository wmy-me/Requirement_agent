"""用于查找语义相关需求的检索 Agent。"""

from __future__ import annotations

from src.requirement_agent.application.retrieval_service import RetrievalService


class RetrievalAgent:
    """检索服务的薄封装层。

    负责规范输入格式，并把检索作为主需求处理链路中的核心业务步骤对待。
    """

    def __init__(self, retrieval_service: RetrievalService | None = None) -> None:
        self.retrieval_service = retrieval_service or RetrievalService()

    def retrieve(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        cleaned = (query or "").strip()
        if not cleaned:
            return []
        rows = self.retrieval_service.search(cleaned, limit=limit)
        return [dict(row) for row in rows]
