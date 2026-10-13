"""工具：跨需求搜**功能条目**。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_FEATURE_SEARCH_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class SearchFeaturesInput(ToolInput):
    query: str = Field(
        min_length=1, max_length=200, description="要搜的功能内容，如「导出 Excel」「按门店筛选」。"
    )
    limit: int = Field(
        default=10, ge=1, le=MAX_FEATURE_SEARCH_LIMIT, description=f"返回条数，上限 {MAX_FEATURE_SEARCH_LIMIT}。"
    )


@register
class SearchFeaturesTool(BaseTool):
    """与 `search_requirements` 的分工：那个搜**需求**（整条），这个搜**功能**（行级）。

    问「哪些需求里有导出相关的能力」用这个 —— 它能精确到「哪条功能出现在哪条需求的哪一版」，
    而按需求搜会先把整条需求的相关性拉高，粒度更粗。
    """

    name = "search_features"
    description = (
        "按内容搜功能条目（跨需求），返回每条功能属于哪条需求、功能编号、内容与所属模块。"
        "要找**具体某个功能点**在哪些需求里出现过，用它。"
    )
    input_model = SearchFeaturesInput
    output_schema: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "requirement_key": {"type": "string"},
                "requirement_name": {"type": ["string", "null"]},
                "feature_key": {"type": ["string", "null"]},
                "content": {"type": "string"},
                "module": {"type": ["string", "null"]},
            },
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_FEATURE_SEARCH_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: SearchFeaturesInput) -> ToolResult:
        rows = self.query_service.search_features(params.query, limit=params.limit)
        if not rows:
            return ToolResult.empty(f"没有找到含「{params.query}」的功能条目")
        return ToolResult.success(rows)
