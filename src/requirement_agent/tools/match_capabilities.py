"""工具：拿一条来源的抽取候选，对**当前**能力词表重匹配（纯预演）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.requirement_query import RequirementQueryService
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class MatchCapabilitiesInput(ToolInput):
    source_id: str = Field(
        min_length=1, max_length=40, description="来源编号（字符串，雪花 ID）。"
    )


@register
class MatchCapabilitiesTool(BaseTool):
    """### ⚠️ 为什么这个工具收 `source_id`，而不是「一段需求描述」

    `CapabilityMatchService.match()` 吃的是**抽取出的 `action`/`object` 候选**
    （`extracted["capabilities"]`）—— 而那个东西**只有 LLM 抽取之后才存在**，
    纯文本推不出来。所以想做成只读工具，只能反过来：
    **拿一条已经抽取过的来源，对当前的词表重跑一遍匹配。**

    这恰好也是它区别于 `get_capabilities` 的地方：

    | | 看什么 |
    |---|---|
    | `get_capabilities` | 当初分析时**落库的**能力关联（可能建于旧词表） |
    | `match_capabilities` | 用**现在的**词表重匹配 —— 词表更新后能看出差异 |

    ### ⚠️ 必须显式传 `persist=False`

    `match()` 的默认参数是 **`persist=True`** —— 它会**真的往库里写**能力提案
    （`capability` 行 + 关联）。这个工具是只读层的，漏传这个参数就会在「查询」的名义下写库。
    `persist=False` 时它一行都不写，只回报「会提议什么」（源码原话：*预演：如实回报
    「会提议这条」，但不建行*）。
    """

    name = "match_capabilities"
    description = (
        "拿一条**已抽取过的来源**，用**当前**的能力词表重新匹配一遍，"
        "返回命中哪些能力、会提议哪些新能力。**纯预演，不写库、不新建提案。**"
        "想看在库的（当初落库的）关联用 `get_capabilities`。"
    )
    input_model = MatchCapabilitiesInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "source_id": {"type": "string"},
            "business_object": {"type": ["string", "null"]},
            "capabilities": {"type": "array", "items": {"type": "object"}},
            "constraints": {"type": "object"},
            "summary": {"type": "object"},
        },
    }
    allowed_consumers = ("analysis", "assistant")

    def __init__(self, query_service: RequirementQueryService | None = None) -> None:
        self.query_service = query_service or RequirementQueryService()

    def execute(self, params: MatchCapabilitiesInput) -> ToolResult:
        outcome = self.query_service.match_capabilities(int(params.source_id))
        if not outcome.found:
            return ToolResult.empty(
                f"来源 {params.source_id} 不存在，或它还没有可匹配的能力候选"
                "（可能尚未抽取）",
                result=outcome.value,
            )
        if not outcome.value.get("capabilities"):
            return ToolResult.empty("这份抽取结果里没有可匹配的能力候选", result=outcome.value)
        return ToolResult.success(outcome.value)
