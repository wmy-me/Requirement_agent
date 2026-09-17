"""工具：取一条需求的基本情况。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import RequirementQueryService
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class GetRequirementDetailInput(ToolInput):
    requirement_key: str = Field(
        min_length=1,
        max_length=80,
        description="需求编号，形如 REQ-000015。",
    )


@register
class GetRequirementDetailTool(BaseTool):
    """拿到 `search_requirements` 或 `list_requirements` 的结果后跟进调用。"""

    name = "get_requirement_detail"
    description = (
        "按需求编号取该需求的名称、正文、当前版本号与功能条数。"
        "要功能明细用 `get_requirement_features`，要看版本演进用 `get_requirement_versions`。"
    )
    input_model = GetRequirementDetailInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement_key": {"type": "string"},
            "requirement_name": {"type": "string"},
            "final_requirement": {"type": "string"},
            "current_version": {"type": "integer"},
            "status": {"type": "string"},
            "feature_count": {"type": "integer"},
        },
    }
    allowed_consumers = ("analysis", "assistant")

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: GetRequirementDetailInput) -> ToolResult:
        outcome = self.query_service.get_requirement(params.requirement_key)
        if not outcome.found:
            # 「这条需求不存在」是**合法答案**（模型可能拼错了编号），不是工具故障
            return ToolResult.empty(
                f"没有编号为 {params.requirement_key} 的需求", result=outcome.value
            )
        return ToolResult.success(outcome.value)
