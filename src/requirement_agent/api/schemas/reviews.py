"""审核相关 API Schema：审核裁决请求。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
