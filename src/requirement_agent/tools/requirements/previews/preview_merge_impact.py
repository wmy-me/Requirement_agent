"""工具：预演「把这条来源并进某条 REQ」会造成什么影响。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from requirement_agent.application.review_service import ReviewService
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult
from requirement_agent.tools.registry import register


class PreviewMergeImpactInput(ToolInput):
    source_id: str = Field(
        min_length=1, max_length=40, description="待审来源的编号（字符串，雪花 ID）。"
    )
    target_requirement_key: str = Field(
        min_length=1, max_length=80, description="要并进哪条需求，形如 REQ-000015。"
    )
    merge_mode: str = Field(
        default="union",
        pattern="^(union|replace)$",
        description="union（并集，默认：来源没提到的现有功能保留）/ replace（以来源为准，未命中的现有功能会被删）。",
    )


@register
class PreviewMergeImpactTool(BaseTool):
    """**这是 B2 里唯一一个「预演」工具**，也是文档点名的「影响预览」。

    ⚠️ **只读，但只对 `pending_review` 的来源有意义** —— 来源必须还没被处理过。
    它和真正落库的那条路**共用同一个 diff 内核**（`preview_sync`），
    所以这里列出的增删改就是提交后会真实发生的（项目里有测试钉着「预览不撒谎」）。

    ⚠️ `merge_mode=replace` 会列出**将被删除的现有功能** —— 那是破坏性操作，
    展示时必须把删除清单和 `warnings` 一起给人看。
    """

    name = "preview_merge_impact"
    description = (
        "预演：把一条待审来源并进某条需求，会造成哪些功能级的新增 / 改写 / 删除。"
        "回答「如果并进去会怎样」用它 —— **它不会真的改任何东西**。"
        "⚠️ `replace` 模式会删掉来源没提到的现有功能，删除清单在结果的 `deleted` 与 `warnings` 里。"
    )
    input_model = PreviewMergeImpactInput
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "source_id": {"type": "string"},
            "merge_mode": {"type": "string"},
            "target": {"type": "object"},
            "next_version": {"type": "integer"},
            "groups": {"type": "array", "items": {"type": "object"}},
            "summary": {"type": "object"},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }
    # 只有审核场景会问「并进去会怎样」；助手将来若要答这类问题再放开
    allowed_consumers = ("analysis", "assistant")

    def __init__(self, review_service: ReviewService | None = None) -> None:
        self.review_service = review_service or ReviewService()

    def execute(self, params: PreviewMergeImpactInput) -> ToolResult:
        try:
            preview = self.review_service.preview_merge(
                source_id=int(params.source_id),
                target_requirement_key=params.target_requirement_key,
                merge_mode=params.merge_mode,
            )
        except LookupError as exc:
            # 「来源/目标不存在」是**合法答案**，不是工具故障 —— 模型可能是拼错了编号
            return ToolResult.empty(str(exc))
        # ValueError（来源不在 pending_review）不在这里吞：那是**前置条件不满足**，
        # 让 run() 兜成 ERROR —— 「这条来源已经处理过了」和「没有这条来源」不是一回事。

        warnings = list(preview.get("warnings") or [])
        if not any(group.get("added") or group.get("modified") or group.get("deleted")
                   for group in preview.get("groups") or []):
            return ToolResult.empty(
                "并进去不会产生任何功能级变更"
                + (f"（{warnings[0]}）" if warnings else ""),
                result=preview,
            )
        return ToolResult.success(preview)
