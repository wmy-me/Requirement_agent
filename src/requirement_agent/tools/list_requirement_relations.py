"""工具：某条需求的关联 / 重复 / 冲突边。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_RELATION_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class ListRequirementRelationsInput(ToolInput):
    requirement_key: str = Field(min_length=1, max_length=80, description="需求编号，形如 REQ-000015。")
    limit: int = Field(
        default=20, ge=1, le=MAX_RELATION_LIMIT, description=f"最多返回几条，上限 {MAX_RELATION_LIMIT}。"
    )


@register
class ListRequirementRelationsTool(BaseTool):
    """关系边是**分析阶段算出来、审核通过时落表**的（不是模型此刻的判断）。"""

    name = "list_requirement_relations"
    description = (
        "取某条需求与其它需求的关系边（双向：它指向别人的 + 别人指向它的），"
        "每条含关系类型（重复/关联/冲突/依赖）、相似度、状态与理由。"
        "⚠️ `status` 为 `proposed` 的是**模型的提议、还没经过人工确认**，"
        "当作参考而不是结论。"
    )
    input_model = ListRequirementRelationsInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement_key": {"type": "string"},
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "relations": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_RELATION_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: ListRequirementRelationsInput) -> ToolResult:
        outcome = self.query_service.list_relations(params.requirement_key, limit=params.limit)
        if not outcome.found:
            return ToolResult.empty(
                f"没有编号为 {params.requirement_key} 的需求", result=outcome.value
            )
        if not outcome.value.get("relations"):
            return ToolResult.empty(f"{params.requirement_key} 目前没有任何关系边")
        return ToolResult.success(outcome.value)
