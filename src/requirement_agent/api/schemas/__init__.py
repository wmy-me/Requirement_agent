"""目标命名空间：API 请求/响应 Schema（Pydantic 模型）。

从 `src/interfaces/http/schemas.py` 逐步迁移而来；旧文件保留兼容转发（同一类对象）。
已迁移（子批次 3.1）：
    - common.HealthResponse
    - requirements.RequirementSubmitRequest / RequirementSubmitResponse
    - reviews.ReviewSubmitRequest
未迁移（归属待确认，暂留旧文件）：AgentRunRequest / AgentChatRequest（agent 域）、
ConversationCreateRequest / ConversationUpdateRequest / ConversationMessageCreateRequest（conversation 域）。
"""

from __future__ import annotations

from src.requirement_agent.api.schemas.common import HealthResponse
from src.requirement_agent.api.schemas.requirements import (
    RequirementSubmitRequest,
    RequirementSubmitResponse,
)
from src.requirement_agent.api.schemas.reviews import ReviewSubmitRequest

__all__ = [
    "HealthResponse",
    "RequirementSubmitRequest",
    "RequirementSubmitResponse",
    "ReviewSubmitRequest",
]
