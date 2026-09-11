"""目标命名空间：API 请求/响应 Schema（Pydantic 模型）。

按域拆分：common（健康检查）、requirements（需求提交）、reviews（审核）、
agent（Agent 分析/聊天）、conversations（会话）。
"""

from __future__ import annotations

from src.requirement_agent.api.schemas.agent import AgentChatRequest, AgentRunRequest
from src.requirement_agent.api.schemas.common import HealthResponse
from src.requirement_agent.api.schemas.conversations import (
    ConversationCreateRequest,
    ConversationMessageCreateRequest,
    ConversationUpdateRequest,
)
from src.requirement_agent.api.schemas.requirements import (
    RequirementSubmitRequest,
    RequirementSubmitResponse,
)
from src.requirement_agent.api.schemas.reviews import ReviewSubmitRequest

__all__ = [
    "AgentChatRequest",
    "AgentRunRequest",
    "ConversationCreateRequest",
    "ConversationMessageCreateRequest",
    "ConversationUpdateRequest",
    "HealthResponse",
    "RequirementSubmitRequest",
    "RequirementSubmitResponse",
    "ReviewSubmitRequest",
]
