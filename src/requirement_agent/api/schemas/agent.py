"""Agent 相关 API Schema：分析请求 / 流式聊天请求（子批次 3.4 迁移）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentRunRequest(BaseModel):
    """非流式 Agent 分析请求体（与 /chat/stream 共享管线，供回放/兼容）。"""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["web", "email", "meeting", "manual"] = "web"
    requester_name: str | None = Field(default=None, max_length=120)
    original_text: str = Field(min_length=1, max_length=20_000)

    @field_validator("requester_name", mode="before")
    @classmethod
    def normalize_requester_name(cls, value: str | None) -> str | None:
        """trim 空白，空串归一为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("original_text")
    @classmethod
    def require_original_text(cls, value: str) -> str:
        """正文必填且不能为空白。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("original_text must not be blank")
        return normalized


class AgentChatRequest(BaseModel):
    """对话式 Agent 流式聊天请求体（纯文本，SSE 响应）。

    - `session_id`：会话 ID，为空则新建会话。
    - `client_message_id`：客户端消息幂等键，断线重发/重试时按它回放已完成的结果，不重复计算。
    - `requirement_text`：可选的分析正文（用于富文本/多文件合并后传入管线），缺省时用 `message`。
    - `analysis_mode`：分析严格度（strict/balanced/broad），影响冲突判定阈值。
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=10_000)
    session_id: str | None = Field(default=None, max_length=120)
    client_message_id: str | None = Field(default=None, max_length=200)
    requirement_text: str | None = Field(default=None, max_length=20_000)
    source_type: Literal["web", "email", "meeting", "manual"] = "web"
    requester_name: str | None = Field(default=None, max_length=120)
    actor_id: str | None = Field(default=None, max_length=120)
    analysis_mode: Literal["strict", "balanced", "broad"] = "strict"

    @field_validator("message", "session_id", "client_message_id", "requirement_text", "requester_name", "actor_id", mode="before")
    @classmethod
    def normalize_optional_chat_text(cls, value: str | None) -> str | None:
        """可选文本字段：trim 空白，空串归一为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
