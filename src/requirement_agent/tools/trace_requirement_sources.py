"""工具：某条需求每一版的**来源**（这条需求是怎么来的）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_VERSION_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class TraceRequirementSourcesInput(ToolInput):
    requirement_key: str = Field(min_length=1, max_length=80, description="需求编号，形如 REQ-000015。")
    limit: int = Field(
        default=10, ge=1, le=MAX_VERSION_LIMIT, description=f"最多看几版，上限 {MAX_VERSION_LIMIT}。"
    )


@register
class TraceRequirementSourcesTool(BaseTool):
    """**这是本系统里真实存在的跨实体关系** —— 合并是「来源 → REQ」，
    不是「REQ → REQ」（后者不存在，见 `docs/方案_对话状态机与Git式版本管理.md` §3.3(a)）。"""

    name = "trace_requirement_sources"
    description = (
        "取某条需求每一版**从哪些来源来**：来源编号、渠道、发起人、原文摘要。"
        "想知道「这条需求是谁提的、经过几次合并进来的」用它。"
        "回滚产生的版本没有来源（`sources` 为空是正常的）。"
    )
    input_model = TraceRequirementSourcesInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement_key": {"type": "string"},
            "versions": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_VERSION_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: TraceRequirementSourcesInput) -> ToolResult:
        outcome = self.query_service.trace_sources(params.requirement_key, limit=params.limit)
        if not outcome.found:
            return ToolResult.empty(
                f"没有编号为 {params.requirement_key} 的需求", result=outcome.value
            )
        if not outcome.value.get("versions"):
            return ToolResult.empty(f"{params.requirement_key} 还没有版本，也就没有来源链")
        return ToolResult.success(outcome.value)
