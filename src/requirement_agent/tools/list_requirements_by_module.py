"""Read-only paginated requirement lookup by the real feature-module relation."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from requirement_agent.application.requirement_query import MAX_LIST_LIMIT, RequirementQueryService
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class ListRequirementsByModuleInput(ToolInput):
    module_name: str | None = Field(
        default=None,
        max_length=120,
        description="功能模块展示名；留空仅查询未归类功能所属的需求。",
    )
    page: int = Field(default=1, ge=1, description="从 1 开始的页码。")
    limit: int = Field(default=10, ge=1, le=MAX_LIST_LIMIT, description="每页最多返回的需求数。")
    status: str = Field(default="active", max_length=40, description="需求主线状态；空串表示不限。")


@register
class ListRequirementsByModuleTool(BaseTool):
    name = "list_requirements_by_module"
    description = (
        "按功能模块分页列出需求主线。模块来自功能条目的 module_key/module_name，"
        "不是从需求标题猜测；返回总数、页码、是否还有下一页及每条命中的功能数。"
    )
    input_model = ListRequirementsByModuleInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "module_name": {"type": ["string", "null"]},
            "scope": {"type": "string"},
            "total": {"type": "integer"},
            "page": {"type": "integer"},
            "limit": {"type": "integer"},
            "has_more": {"type": "boolean"},
            "items": {"type": "array", "items": {"type": "object"}},
        },
    }
    allowed_consumers = ("assistant",)
    max_result_count = MAX_LIST_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: ListRequirementsByModuleInput) -> ToolResult:
        result = self.query_service.list_requirements_by_module(
            params.module_name, page=params.page, limit=params.limit, status=params.status
        )
        if not result["items"]:
            scope = "未归类功能" if params.module_name is None else f"模块 {params.module_name}"
            return ToolResult.empty(f"{scope}下没有匹配的需求", result=result)
        return ToolResult.success(result)
