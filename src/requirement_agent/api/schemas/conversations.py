"""会话相关 API Schema：新建 / 更新 / 追加消息（子批次 3.4 迁移）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationCreateRequest(BaseModel):
    """新建会话请求体（默认 title 为「新对话」，后续可由 LLM 摘要/人工改名覆盖）。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    actor_id: str | None = Field(default=None, max_length=120)


class ConversationUpdateRequest(BaseModel):
    """会话信息更新请求体（改名/更新摘要/归档）。

    约定：`title` 一旦被用户改名，`finalize` 只更新 `summary`、不覆盖 `title`。
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None


class ConversationMessageCreateRequest(BaseModel):
    """向既有会话追加一条用户消息的请求体（轻量入口，不走 Agent 分析）。"""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=10_000)
    client_message_id: str | None = Field(default=None, max_length=200)
    actor_id: str | None = Field(default=None, max_length=120)

    @field_validator("message", "client_message_id", "actor_id", mode="before")
    @classmethod
    def normalize_message_text(cls, value: str | None) -> str | None:
        """trim 空白，空串归一为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
