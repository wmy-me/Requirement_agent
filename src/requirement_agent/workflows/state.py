"""需求工作流的 LangGraph 状态对象（TypedDict 通道）。"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


class RequirementState(TypedDict, total=False):
    """在 LangGraph 图内流转的状态。未列字段也可按需存在，仅作增量合并。"""

    # —— 输入 ——
    source_id: int | None
    source_text: str
    source_type: str
    requester_name: str | None

    # —— 文档规整（可选，RequirementService 已预先算好）——
    standardized_text: str
    segments: list[dict[str, Any]]
    normalized_fields: dict[str, Any]

    # —— 抽取结果（完整 ExtractedRequirement.model_dump，含领域/优先级/子需求）——
    extracted: dict[str, Any]

    # —— 检索与关系分析 ——
    candidates: list[dict[str, Any]]
    analysis: dict[str, Any]
    risk: dict[str, Any]

    # —— 决策（由 decision_rules 产出）——
    decision: str  # manual_review | can_commit
    next_action: str

    # —— 审核/落库（决策图使用）——
    review: dict[str, Any]
    outcome: dict[str, Any]

    # —— 运行时上下文：决策图注入 repos + session（不走 checkpointer，允许非序列化）——
    ctx: dict[str, Any]

    # —— 工具调用记录（B3）：每个节点把调过的工具追加进来，供落库与排障 ——
    # 形状见 `tools/invoker.tool_call_record`：tool / params / status / duration_ms / count
    tool_calls: Annotated[list[dict[str, Any]], add]

    # —— 错误累积 ——
    # ⚠️ 目前**没有任何消费者**（`decide_node` 与 `decision_rules` 都不读它）。
    # 保留是因为它与 `tool_calls` 是同一类东西（节点级留痕），
    # 而 B3 之后「让降级可见」要靠它。别把它当成已经在生效的机制。
    errors: Annotated[list[str], add]
