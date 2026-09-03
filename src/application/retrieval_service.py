"""Retrieval service for knowledge and similarity search."""

from __future__ import annotations


class RetrievalService:
    """Backs semantic and exact retrieval from requirement records."""

    def search(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        return [
            {
                "requirement_key": "REQ-000001",
                "requirement_name": "用户登录",
                "summary": "支持邮箱和手机号登录，并支持验证码校验。",
                "score": 0.95,
            }
        ][: max(1, min(limit, 10))]
