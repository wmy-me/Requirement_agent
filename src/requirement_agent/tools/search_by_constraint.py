"""工具：按限定条件反查需求主线。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_LIST_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class SearchByConstraintInput(ToolInput):
    constraint: str = Field(
        min_length=1, max_length=120, description="条件原文或正式键，如「按门店」「按周期」。"
    )
    limit: int = Field(
        default=10, ge=1, le=MAX_LIST_LIMIT, description=f"返回条数，上限 {MAX_LIST_LIMIT}。"
    )


@register
class SearchByConstraintTool(BaseTool):
    """**与 `search_by_capability` 是两条独立的查询轴，不是谁的筛选条件。**

    条件活在**版本快照**里（`constraint_snapshot`），能力活在**功能关联**里 ——
    所以「哪些需求要求按门店筛选」只能从这里问，从能力那侧问不到。
    """

    name = "search_by_constraint"
    description = (
        "按限定条件反查需求主线：「哪些需求要求在按门店筛选」。"
        "只查各自的**当前版本**。返回每条需求命中时那个条件的原文。"
    )
    input_model = SearchByConstraintInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "constraint": {"type": "string"},
            "count": {"type": "integer"},
            "streams": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_LIST_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: SearchByConstraintInput) -> ToolResult:
        outcome = self.query_service.search_by_constraint(params.constraint, limit=params.limit)
        if not outcome.found:
            return ToolResult.empty(f"约束为空：{params.constraint}", result=outcome.value)
        if not outcome.value.get("streams"):
            # 条件大部分还没入词表（`constraint_key` 为 null），所以查不到是常态
            return ToolResult.empty(
                f"没有需求的当前版本带条件「{params.constraint}」"
                "（注意：未入词表的条件只在版本快照里，可能写法不同）"
            )
        return ToolResult.success(outcome.value)
