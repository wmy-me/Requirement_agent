"""工具：某条需求历史上被评过哪些风险。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_VERSION_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class GetRequirementRisksInput(ToolInput):
    requirement_key: str = Field(min_length=1, max_length=80, description="需求编号，形如 REQ-000015。")
    limit: int = Field(
        default=5, ge=1, le=MAX_VERSION_LIMIT, description=f"最多看几版的风险，上限 {MAX_VERSION_LIMIT}。"
    )


@register
class GetRequirementRisksTool(BaseTool):
    """⚠️ **风险是按版本存的，不是按需求存的。** 它写在每版的 `diff_payload.risk` 里
    （提交时由分析结果落库），所以同一个需求的不同版本可能被评为不同的风险。"""

    name = "get_requirement_risks"
    description = (
        "取某条需求各版本的风险评估：质量 / 变更 / 技术影响三类等级 + 置信度。"
        "按版本倒序（新→旧）。判断「这条需求的改动风险大不大」用它。"
    )
    input_model = GetRequirementRisksInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement_key": {"type": "string"},
            "count": {"type": "integer"},
            "risks": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_VERSION_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: GetRequirementRisksInput) -> ToolResult:
        outcome = self.query_service.get_risks(params.requirement_key, limit=params.limit)
        if not outcome.found:
            return ToolResult.empty(
                f"没有编号为 {params.requirement_key} 的需求", result=outcome.value
            )
        if not outcome.value.get("risks"):
            # 版本可能是能力模型之前建的 —— 那时还没评风险，**空是正常的**
            return ToolResult.empty(f"{params.requirement_key} 的版本里没有风险评估记录")
        return ToolResult.success(outcome.value)
