"""审核相关工具（提交人工审核决策）。

原则：只做转发，走 `ReviewService`（强事务、含审计 / outbox），不直接写库。
"""

from __future__ import annotations

from requirement_agent.config.settings import settings
from requirement_agent.tools._deps import review_service


def submit_review_decision(
    source_id: int,
    decision: str,
    reviewer_name: str | None = None,
    comment: str | None = None,
    edited_requirement: str | None = None,
) -> dict[str, str]:
    """提交对某条需求来源的人工审核结果（approved / rejected / returned）。"""
    return review_service.submit_decision(
        source_id=source_id,
        decision=decision,
        reviewer_id=settings.tool_actor_id,
        reviewer_name=reviewer_name,
        comment=comment,
        edited_requirement=edited_requirement,
    )


__all__ = ["submit_review_decision"]
