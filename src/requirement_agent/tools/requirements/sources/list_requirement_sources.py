"""工具：列出来源（默认待审队列）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_SOURCE_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register

_ALLOWED_STATUS = ("pending_review", "received", "approved", "rejected", "committed", "failed")


class ListRequirementSourcesInput(ToolInput):
    status: str = Field(
        default="pending_review",
        description=f"来源状态，取值：{'/'.join(_ALLOWED_STATUS)}。默认待审。",
    )
    limit: int = Field(
        default=10, ge=1, le=MAX_SOURCE_LIMIT, description=f"返回条数，1-{MAX_SOURCE_LIMIT}。"
    )


@register
class ListRequirementSourcesTool(BaseTool):
    """**刻意只给编号、标题、渠道、时间 —— 不给正文与模型分析。**

    待审材料里带着原文、分析结论、风险评级，那些是**给审核人做判断用的**。
    全量喂给模型等于让它替人看材料、替人下结论 —— 这不是它该做的事。

    想知道某条来源变成了哪条需求，用 `trace_requirement_sources`（从需求那侧看）。
    """

    name = "list_requirement_sources"
    description = (
        "列出来源（默认待审队列里的），每条给编号、标题、渠道、发起人与提交时间。"
        "回答「现在积压了多少、都是些什么」用它。"
        "**只给标题不给正文** —— 审核判断是人的职责。"
    )
    input_model = ListRequirementSourcesInput
    output_schema: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "title": {"type": "string"},
                "source_type": {"type": ["string", "null"]},
                "requester_name": {"type": ["string", "null"]},
                "submitted_at": {"type": ["string", "null"]},
            },
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_SOURCE_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: ListRequirementSourcesInput) -> ToolResult:
        rows = self.query_service.list_sources(status=params.status, limit=params.limit)
        if not rows:
            return ToolResult.empty(f"没有状态为 {params.status} 的来源")
        return ToolResult.success(rows)
