"""工具：对比某条需求的两个版本。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import RequirementQueryService
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class CompareRequirementVersionsInput(ToolInput):
    requirement_key: str = Field(min_length=1, max_length=80, description="需求编号，形如 REQ-000015。")
    from_version: int = Field(ge=1, description="起始版本号（较早的那版）。")
    to_version: int = Field(ge=1, description="目标版本号（较晚的那版）。")


@register
class CompareRequirementVersionsTool(BaseTool):
    """比 `get_requirement_features` 传 `at_version` 更进一步：直接告诉**改了什么**。"""

    name = "compare_requirement_versions"
    description = (
        "对比某条需求的两个版本，返回功能级的**新增 / 改写 / 删除**清单。"
        "想知道「V1 到 V3 到底改了什么」用它 —— 比分别拉两次功能明细再自己比要准。"
        "版本号越界会报错（不会给你一份看起来像结果的假数据）。"
    )
    input_model = CompareRequirementVersionsInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement_key": {"type": "string"},
            "from_version": {"type": "integer"},
            "to_version": {"type": "integer"},
            "summary": {"type": "object"},
            "added": {"type": "array", "items": {"type": "object"}},
            "modified": {"type": "array", "items": {"type": "object"}},
            "removed": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("analysis", "assistant")

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: CompareRequirementVersionsInput) -> ToolResult:
        outcome = self.query_service.compare_versions(
            params.requirement_key,
            from_version=params.from_version,
            to_version=params.to_version,
        )
        if not outcome.found:
            return ToolResult.empty(
                f"没有编号为 {params.requirement_key} 的需求", result=outcome.value
            )
        summary = outcome.value.get("summary") or {}
        if not (summary.get("added") or summary.get("modified") or summary.get("removed")):
            return ToolResult.empty(
                f"V{params.from_version} 到 V{params.to_version} 之间没有功能级差异",
                result=outcome.value,
            )
        return ToolResult.success(outcome.value)
