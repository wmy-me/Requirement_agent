"""需求相关 API Schema：提交请求/响应。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class RequirementSubmitResponse(BaseModel):
    """提交需求的响应体，`source_id` 供后续审核/回放引用。"""

    message: str
    source_type: str
    status: str = "pending_review"
    source_id: int | None = None


class RequirementRevertRequest(BaseModel):
    """把一条需求主线回滚到某个历史版本的请求体。

    - `target_version`：要回到的版本号（≥1，且必须存在于该主线、且不等于当前版本）。
    - `expected_current_version`：**可选的前端 STS 检查** —— 前端页面加载时看到的
      `current_version`。`lock_version` 只防得住同一事务窗口内的并发，防不住
      「人盯着五分钟前的页面点回滚」；传了就在这里拦。不传则不校验。
    """

    model_config = ConfigDict(extra="forbid")

    target_version: int = Field(gt=0)
    comment: str | None = Field(default=None, max_length=5_000)
    expected_current_version: int | None = Field(default=None, gt=0)

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """可选文本字段：trim 空白，空串归一为 None。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RequirementRelationUpdateRequest(BaseModel):
    """需求关系的裁决请求体。

    只允许 confirmed / dismissed：`proposed` 是分析写入时的初始态，
    由人工改回 proposed 没有语义（那是「撤回裁决」，需要的是重新分析）。
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "dismissed"]
