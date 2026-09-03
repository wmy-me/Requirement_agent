"""MCP tool surface for business operations."""

from __future__ import annotations


def search_requirements(query: str) -> dict[str, object]:
    """Placeholder for semantic or exact search of requirements."""
    return {"query": query, "results": []}


def submit_review_decision(decision: str, reviewer_id: str) -> dict[str, str]:
    """Placeholder for review approval/rejection submission."""
    return {"decision": decision, "reviewer_id": reviewer_id, "status": "recorded"}
