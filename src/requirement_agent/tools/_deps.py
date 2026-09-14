"""tools 子包共享的服务 / 仓库单例（避免各工具模块重复实例化）。"""

from __future__ import annotations

from requirement_agent.application.channel_service import ChannelIngestService
from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.application.retrieval_service import RetrievalService
from requirement_agent.application.review_service import ReviewService
from requirement_agent.infrastructure.db.repositories import (
    RequirementMasterRepository,
    RequirementVersionRepository,
)
from requirement_agent.infrastructure.worker.tasks import RequirementAnalysisTask

requirement_service = RequirementService()
retrieval_service = RetrievalService()
review_service = ReviewService()
master_repo = RequirementMasterRepository()
version_repo = RequirementVersionRepository()

# 渠道接入：分析任务需要应用层服务，装配点在此注入（见 RequirementAnalysisTask 的说明）
requirement_analysis_task = RequirementAnalysisTask(requirement_service.process_requirement)
channel_ingest_service = ChannelIngestService(requirement_analysis_task)

__all__ = [
    "requirement_service",
    "retrieval_service",
    "review_service",
    "master_repo",
    "version_repo",
    "requirement_analysis_task",
    "channel_ingest_service",
]
