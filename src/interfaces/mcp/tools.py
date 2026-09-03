"""MCP tool surface for business operations."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService

mcp = FastMCP("requirement-agent")
retrieval_service = RetrievalService()
review_service = ReviewService()


@mcp.tool()
def search_requirements(query: str, limit: int = 10) -> dict[str, object]:
    """Semantic or exact search for requirements."""
    return {"query": query, "limit": limit, "results": retrieval_service.search(query, limit=limit)}


@mcp.tool()
def submit_review_decision(
    source_id: int,
    decision: str,
    reviewer_id: str,
    reviewer_name: str | None = None,
    comment: str | None = None,
    edited_requirement: str | None = None,
) -> dict[str, str]:
    """Submit a human review result for a requirement."""
    return review_service.submit_decision(
        source_id=source_id,
        decision=decision,
        reviewer_id=reviewer_id,
        reviewer_name=reviewer_name,
        comment=comment,
        edited_requirement=edited_requirement,
    )


__all__ = ["mcp", "search_requirements", "submit_review_decision"]
