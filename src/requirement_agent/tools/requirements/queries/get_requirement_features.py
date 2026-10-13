"""工具：取某条需求（或它某个版本）的功能明细。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_FEATURE_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class GetRequirementFeaturesInput(ToolInput):
    requirement_key: str = Field(min_length=1, max_length=80, description="需求编号，形如 REQ-000015。")
    at_version: int | None = Field(
        default=None,
        ge=1,
        description="可选。看第几版**当时**的功能集（内容也回到那时）；不传则看当前版本。",
    )
    limit: int = Field(
        default=30, ge=1, le=MAX_FEATURE_LIMIT, description=f"最多返回几条，上限 {MAX_FEATURE_LIMIT}。"
    )


@register
class GetRequirementFeaturesTool(BaseTool):
    """功能条目是需求的组成单元 —— 一条需求的正文就是它所有生效功能的拼接。

    **这是「判重复」最需要的信息**：现在分析流程喂给模型的候选只有标题与相似度，
    模型是**看着标题判重复**的。有了这个工具，它能自己拉候选的功能明细再判断。
    """

    name = "get_requirement_features"
    description = (
        "取某条需求的功能明细（功能编号、内容、所属模块）。"
        "传 `at_version` 可以看**在那个版本时刻**它有哪些功能、内容是什么；不传就是当前的样子。"
    )
    input_model = GetRequirementFeaturesInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement_key": {"type": "string"},
            "at_version": {"type": ["integer", "null"]},
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "feature_key": {"type": "string"},
                        "content": {"type": "string"},
                        "module": {"type": ["string", "null"]},
                        "origin_version_no": {"type": ["integer", "null"]},
                    },
                },
            },
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_FEATURE_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: GetRequirementFeaturesInput) -> ToolResult:
        outcome = self.query_service.get_features(
            params.requirement_key, at_version=params.at_version, limit=params.limit
        )
        if not outcome.found:
            return ToolResult.empty(
                f"没有编号为 {params.requirement_key} 的需求", result=outcome.value
            )
        if not outcome.value.get("features"):
            version_hint = f"V{params.at_version}" if params.at_version else "当前版本"
            return ToolResult.empty(
                f"{params.requirement_key} 在{version_hint}没有任何功能条目", result=outcome.value
            )
        return ToolResult.success(outcome.value)
