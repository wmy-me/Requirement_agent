"""工具：按语义检索历史需求。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import (
    MAX_SEARCH_LIMIT,
    RequirementQueryService,
)
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class SearchRequirementsInput(ToolInput):
    """入参。**schema 由这个模型生成** —— 描述写在 Field 里，结构由类型保证。"""

    query: str = Field(
        min_length=1,
        max_length=500,
        description="要检索的内容，通常是一条需求的标题或摘要（自然语言）。",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description=f"返回条数，1-{MAX_SEARCH_LIMIT}，默认 5。",
    )
    channel: str | None = Field(
        default=None,
        max_length=40,
        description=(
            "只在指定渠道内检索（如 web / feishu）。**不传 = 不按渠道过滤**，这是默认，"
            "也是推荐用法 —— 同一条需求从两个渠道提本就该判重复，按渠道切会把它藏掉。"
            "仅在调用方明确知道自己只要某个渠道时再传。"
        ),
    )


@register
class SearchRequirementsTool(BaseTool):
    """最该被优先调用的工具：判断「这条需求是不是已经有过了」是一切后续判断的前提。"""

    name = "search_requirements"
    description = (
        "按语义检索历史需求，返回最相近的若干条（需求编号、名称、向量相似度）。"
        "想知道「有没有人提过类似的需求」时用它。"
        "⚠️ `vector_similarity` 为 null 表示这条只命中了关键词、没有向量分数，"
        "**不能**当作「相似度为 0」。"
        "⚠️ 相似度高**不等于**重复 —— 是否判定重复由后端按两把锁决定，这里只给候选。"
    )
    input_model = SearchRequirementsInput
    output_schema: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "requirement_key": {"type": "string"},
                "requirement_name": {"type": ["string", "null"]},
                # 余弦；纯关键词命中的候选为 null。**只给这一个数** —— 排序分是管道内部
                # 的东西，模型不需要，给了反而会被拿来下结论（见 requirement_query 的说明）。
                # 是否重复由后端按两把锁判定，不要拿这个数自己下结论。
                "vector_similarity": {"type": ["number", "null"]},
                # 这条需求是从哪些渠道进来的。给模型是为了让它知道来路；
                # 同时也是 `soft` 模式下「与本条来源是否同渠道」的判据（同一个字段，不另算）。
                "source_types": {"type": "array", "items": {"type": "string"}},
            },
        },
    }
    allowed_consumers = ("analysis", "assistant")
    max_result_count = MAX_SEARCH_LIMIT

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: SearchRequirementsInput) -> ToolResult:
        rows = self.query_service.search_requirements(
            params.query, limit=params.limit, channel=params.channel
        )
        if not rows:
            # **没有命中是正常答案**，不是错误 —— 消息要让模型能区分这两者
            return ToolResult.empty(f"没有找到与「{params.query}」相似的历史需求")
        return ToolResult.success(rows)
