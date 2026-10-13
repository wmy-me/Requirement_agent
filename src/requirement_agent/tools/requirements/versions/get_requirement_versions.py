"""工具：某条需求的版本历史。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_VERSION_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class GetRequirementVersionsInput(ToolInput):
    requirement_key: str = Field(min_length=1, max_length=80, description="需求编号，形如 REQ-000015。")
    limit: int = Field(
        default=10, ge=1, le=MAX_VERSION_LIMIT, description=f"最多返回几版，上限 {MAX_VERSION_LIMIT}。"
    )


@register
class GetRequirementVersionsTool(BaseTool):
    """⚠️ **刻意不返回正文快照** —— 每版几百字，全带上会把上下文淹掉。要看某版内容，
    用 `get_requirement_features` 传 `at_version`。"""

    name = "get_requirement_versions"
    description = (
        "取某条需求的版本历史（新→旧）：第几版、什么类型的变更、什么时候、谁审的、"
        "基于哪一版、本版改了几条功能。判断「这条需求是不是一直在改」用它。"
        "不含正文快照。"
    )
    input_model = GetRequirementVersionsInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement_key": {"type": "string"},
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "versions": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_VERSION_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: GetRequirementVersionsInput) -> ToolResult:
        outcome = self.query_service.get_versions(params.requirement_key, limit=params.limit)
        if not outcome.found:
            return ToolResult.empty(
                f"没有编号为 {params.requirement_key} 的需求", result=outcome.value
            )
        if not outcome.value.get("versions"):
            return ToolResult.empty(f"{params.requirement_key} 还没有任何版本记录")
        return ToolResult.success(outcome.value)
