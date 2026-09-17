"""工具：按能力反查需求主线。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_STREAM_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class SearchByCapabilityInput(ToolInput):
    capability: str = Field(
        min_length=1,
        max_length=120,
        description="能力名，一般是「动作 + 对象」，如「导出 Excel」「创建巡检计划」。",
    )
    constraint: str | None = Field(
        default=None, max_length=120, description="可选。再用一个限定条件收窄（如「按门店」）。"
    )
    review_status: str | None = Field(
        default=None,
        description="可选。只看该状态的关联：confirmed（人工确认过）/ proposed（AI 提议）/ dismissed。",
    )
    limit: int = Field(
        default=10, ge=1, le=MAX_STREAM_LIMIT, description=f"返回条数，上限 {MAX_STREAM_LIMIT}。"
    )


@register
class SearchByCapabilityTool(BaseTool):
    """反查方向：从「能做什么」回到「谁需要它」。

    ⚠️ **匹配到多个能力时不会替你挑一个** —— 它会把候选列出来让你说清楚。
    挑错会把两条不相干的需求混起来（「导出 Excel」与「导出 PDF」是两个能力）。

    ⚠️ 返回的每条都带 `capability_status` / `review_status`：`pending_confirmation` /
    `proposed` 表示**能力或关联还没有经人工确认**，是不确定的证据。
    """

    name = "search_by_capability"
    description = (
        "按能力查哪些需求主线用到它。例如「导出 Excel」这个能力被哪几条需求用到。"
        "匹配到多个能力时会返回候选列表，请用一个更完整的名字重试。"
        "⚠️ 能力名必须是库里的叫法；不确定就先用 `search_features` 探一下实际怎么写。"
    )
    input_model = SearchByCapabilityInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "capability_status": {"type": ["string", "null"]},
            "count": {"type": "integer"},
            "streams": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_STREAM_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: SearchByCapabilityInput) -> ToolResult:
        outcome = self.query_service.search_by_capability(
            params.capability,
            constraint=params.constraint,
            review_status=params.review_status,
            limit=params.limit,
        )
        value = outcome.value or {}
        if value.get("need_disambiguation"):
            # **不猜** —— 挑错会把两条不相干的需求混起来
            names = "、".join(f"{c['display_name']}（{c['status']}）" for c in value["candidates"])
            return ToolResult.empty(
                f"「{params.capability}」匹配到多个能力，请用更完整的名字重试：{names}",
                result=value,
            )
        if not outcome.found:
            return ToolResult.empty(f"没有找到与「{params.capability}」匹配的能力", result=value)
        if not value.get("streams"):
            return ToolResult.empty(
                f"没有需求用到能力「{value.get('capability')}」" + (
                    f"（且带条件「{params.constraint}」）" if params.constraint else ""
                )
            )
        return ToolResult.success(value)
