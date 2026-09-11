"""tools 子包共享的服务 / 仓库单例（避免各工具模块重复实例化）。"""

from __future__ import annotations

from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.application.retrieval_service import RetrievalService
from requirement_agent.application.review_service import ReviewService
from requirement_agent.infrastructure.db.repositories import (
    RequirementMasterRepository,
    RequirementVersionRepository,
)

requirement_service = RequirementService()
retrieval_service = RetrievalService()
review_service = ReviewService()
master_repo = RequirementMasterRepository()
version_repo = RequirementVersionRepository()

__all__ = [
    "requirement_service",
    "retrieval_service",
    "review_service",
    "master_repo",
    "version_repo",
]
