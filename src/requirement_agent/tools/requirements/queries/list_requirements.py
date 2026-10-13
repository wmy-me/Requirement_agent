"""工具：列举需求主线。

**这个工具是补一个此前被漏掉的缺口。** 上一版工具层有「按语义搜」和「按编号取」，
但**没有「浏览/列举」** —— 模型答不出「现在一共有哪些需求」。

刻意只给**编号、名称、当前版本、功能数**，不给正文：列举类工具最容易把整个库
灌进上下文，而它存在的意义只是让模型知道「有哪些」。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_LIST_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class ListRequirementsInput(ToolInput):
    limit: int = Field(
        default=10, ge=1, le=MAX_LIST_LIMIT, description=f"返回条数，1-{MAX_LIST_LIMIT}，默认 10。"
    )
    status: str = Field(
        default="active",
        description="按状态过滤：active（默认）/ archived / deleted。传空串表示不限。",
    )


@register
class ListRequirementsTool(BaseTool):
    """⚠️ **只给摘要不给正文。** 想回答「库里有什么」用它；想知道某条具体内容，
    拿编号去调 `get_requirement_detail`。"""

    name = "list_requirements"
    description = (
        "列举需求库里的需求主线（按创建时间倒序），每条只给编号、名称、当前版本与功能数。"
        "回答「现在一共有哪些需求」「有没有关于 X 的需求」这类问题时用它 —— "
        "但**找相似需求应该用 `search_requirements`**（那是语义检索，更准）。"
    )
    input_model = ListRequirementsInput
    output_schema: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "requirement_key": {"type": "string"},
                "requirement_name": {"type": "string"},
                "current_version": {"type": ["integer", "null"]},
                "feature_count": {"type": ["integer", "null"]},
                "status": {"type": "string"},
            },
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_LIST_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: ListRequirementsInput) -> ToolResult:
        rows = self.query_service.list_requirements(limit=params.limit, status=params.status)
        if not rows:
            scope = f"状态为 {params.status} 的" if params.status else ""
            return ToolResult.empty(f"需求库里没有{scope}需求")
        return ToolResult.success(rows)
