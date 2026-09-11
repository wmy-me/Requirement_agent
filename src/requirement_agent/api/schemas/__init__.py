"""API 请求/响应 Schema（Pydantic 模型）。

按域拆分：requirements（需求提交）、reviews（审核）、agent（Agent 分析/聊天）、
conversations（会话）。
"""

from __future__ import annotations

from requirement_agent.api.schemas.agent import AgentChatRequest, AgentRunRequest
from requirement_agent.api.schemas.conversations import (
    ConversationCreateRequest,
    ConversationMessageCreateRequest,
    ConversationUpdateRequest,
)
from requirement_agent.api.schemas.requirements import (
    RequirementSubmitRequest,
    RequirementSubmitResponse,
)
from requirement_agent.api.schemas.reviews import ReviewSubmitRequest

__all__ = [
    "AgentChatRequest",
    "AgentRunRequest",
    "ConversationCreateRequest",
    "ConversationMessageCreateRequest",
    "ConversationUpdateRequest",
    "RequirementSubmitRequest",
    "RequirementSubmitResponse",
    "ReviewSubmitRequest",
]
