"""渠道接入的应用服务：把归一化的入站需求落库并排队分析。

与 `RequirementService.submit_requirement` 的关键区别：本服务**不等分析结果**。
渠道（如飞书）要求在数秒内应答，而分析图要跑 4 个 LLM 步骤，所以这里只做
「幂等去重 → 落库 → 入队」，分析交给 `RequirementAnalysisTask` 在后台补上。
"""

from __future__ import annotations

import logging
from typing import Any

from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.domain.requirement import RequirementSource
from requirement_agent.infrastructure.channels.base import InboundRequirement
from requirement_agent.infrastructure.db.repositories import RequirementSourceRepository
from requirement_agent.infrastructure.worker.tasks import RequirementAnalysisTask

logger = logging.getLogger(__name__)


class ChannelIngestService:
    """渠道事件 → 需求来源的接入服务。"""

    def __init__(
        self,
        analysis_task: RequirementAnalysisTask,
        source_repo: RequirementSourceRepository | None = None,
        requirement_service: RequirementService | None = None,
    ) -> None:
        self.analysis_task = analysis_task
        self.source_repo = source_repo or RequirementSourceRepository()
        self.requirement_service = requirement_service or RequirementService()

    def ingest(self, inbound: InboundRequirement) -> dict[str, Any]:
        """落库一条渠道事件并排队分析，返回接入结果。

        幂等：同一 (channel, event_id) 只入库一次 —— 重复投递直接返回既有来源，
        既不重复入队也不重复分析。语义上渠道重投是**投递重试**而非编辑操作，
        所以后到的正文不覆盖已入库内容。
        """
        if not inbound.is_usable():
            # 渠道会推送大量非需求事件（菜单点击、成员变更等），在这里挡掉
            return {"accepted": False, "reason": "empty_text", "channel": inbound.channel}

        if inbound.event_id:
            existing = self.source_repo.find_by_channel_event(inbound.channel, inbound.event_id)
            if existing is not None:
                logger.info(
                    "event=channel_event_duplicated channel=%s event_id=%s source_id=%s",
                    inbound.channel,
                    inbound.event_id,
                    existing.id,
                )
                return {
                    "accepted": True,
                    "deduplicated": True,
                    "queued": False,
                    "source_id": existing.id,
                    "status": existing.processing_status,
                    "channel": inbound.channel,
                }

        source = RequirementSource(
            idempotency_key=self._idempotency_key(inbound),
            source_type=inbound.channel,
            source_event_id=inbound.event_id,
            requester_id=inbound.requester_id,
            requester_name=inbound.requester_name,
            original_text=inbound.text,
            original_payload=inbound.payload or {},
            metadata={**inbound.metadata, "ingest": "channel"},
        )
        saved = self.requirement_service.accept_requirement(source)

        # 已有来源（同 idempotency_key 的重复提交）会带着非 received 状态返回，
        # 此时说明它早已在路上，不重复入队。
        queued = False
        if saved.processing_status in {"received", "failed"}:
            self.analysis_task.enqueue(source_id=int(saved.id or 0))
            queued = True

        return {
            "accepted": True,
            "deduplicated": False,
            "queued": queued,
            "source_id": saved.id,
            "status": saved.processing_status,
            "channel": inbound.channel,
        }

    @staticmethod
    def _idempotency_key(inbound: InboundRequirement) -> str:
        """构造幂等键；有渠道事件 ID 时以事件 ID 为准（渠道内唯一），否则退回内容指纹。"""
        if inbound.event_id:
            return f"{inbound.channel}:event:{inbound.event_id}"
        return f"{inbound.channel}:{inbound.requester_id or 'anonymous'}:{inbound.text}"
