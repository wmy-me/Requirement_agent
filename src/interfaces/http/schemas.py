"""HTTP 接口的 Pydantic 请求/响应模型。

约定：
- `model_config = ConfigDict(extra="forbid")`：拒绝未声明字段，防止拼写错误被静默吞掉。
- 所有可选文本字段统一经 `mode="before"` 校验器 trim，空串归一为 None。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    """健康检查响应体。"""

    status: str = Field(default="ok")


class RequirementSubmitRequest(BaseModel):
    """提交一条待分析需求的请求体（文本通道入口）。

    - `source_type`：输入渠道，当前限 web/email/meeting/manual（扩展渠道需同步放宽此处枚举）。
    - `source_event_id`：渠道侧事件 ID，预留做幂等去重（当前仍以原文哈希为主）。
    - `metadata`：调用方附加的自定义元信息，随 source 一并落库（JSONB）。
    """

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["web", "email", "meeting", "manual"] = "web"
    source_event_id: str | None = Field(default=None, max_length=200)
    requester_id: str | None = Field(default=None, max_length=120)
    requester_name: str | None = Field(default=None, max_length=120)
    original_text: str = Field(min_length=1, max_length=20_000)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("source_event_id", "requester_id", "requester_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """可选文本字段：trim 空白，空串归一为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("original_text")
    @classmethod
    def require_text(cls, value: str) -> str:
        """正文必填且不能为空白，避免空需求入库。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("original_text must not be blank")
        return normalized


class ReviewSubmitRequest(BaseModel):
    """人工审核裁决的请求体。

    - `decision`：approved（通过并生成 REQ/版本）/ rejected（退回）/ returned（打回修改）。
    - `target_requirement_key`：通过时希望合并进的目标 REQ（为空则新建独立 REQ）。
    - `edited_requirement`：审核人修订后的最终需求文本（覆盖抽取结果）。
    - `feature_overrides`：按 feature_key 的逐条增删改裁决，交给 commit 节点合并。
    """

    model_config = ConfigDict(extra="forbid")

    source_id: int = Field(gt=0)
    decision: Literal["approved", "rejected", "returned"]
    target_requirement_key: str | None = Field(default=None, max_length=80)
    reviewer_name: str | None = Field(default=None, max_length=120)
    comment: str | None = Field(default=None, max_length=5_000)
    edited_requirement: str | None = Field(default=None, max_length=20_000)
    feature_overrides: list[dict[str, object]] = Field(default_factory=list)

    @field_validator("target_requirement_key", "reviewer_name", "comment", "edited_requirement", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """可选文本字段：trim 空白，空串归一为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


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


class RequirementSubmitResponse(BaseModel):
    """提交需求的响应体，`source_id` 供后续审核/回放引用。"""

    message: str
    source_type: str
    status: str = "pending_review"
    source_id: int | None = None
