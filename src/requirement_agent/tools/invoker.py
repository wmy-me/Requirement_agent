"""统一工具调用入口（B3）。

**为什么要有这一层。** 让工作流直接 `create("search_requirements").run(...)` 也能跑，
但那样每个调用点都要自己记得：查注册表、校验消费方、处理「工具不存在」、把结果转成
可落库的形状。散在若干个节点里迟早漏一个。这里收成一个函数。

## 三条约定

1. **只走注册表** —— 调用方给名字，不给类、不给方法引用。名字错误会在**调用点**
   变成一条明确的错误结果，而不是 `AttributeError`。
2. **校验消费方白名单** —— 工具的 `allowed_consumers` 是声明式的权限边界，
   在这里强制执行（而不只是写在描述里给人看）。
3. **不吞失败** —— 返回的 `ToolResult` 会如实带着 `error` 状态，
   由调用方按**业务语义**决定是降级还是上抛。见 `ToolInvocationError`。

## ⚠️ 关于「降级」的一条重要判断

`docs/Requirement_agent后端任务与前端重构规划.docx` §三 B3 要求「统一处理异常、超时、
重试和**降级**」。本层把前三样做了（异常与超时在 `BaseTool.run` 里兜，
重试交给 outbox 的既有机制），但**降级不在这里做，也不该无条件做**：

**检索失败时不能静默降级成「没有候选」。** 检索是判重复的唯一依据，空候选会让
`analyze` 得出「独立」的结论 —— 而「错误合并污染版本链」正是这套系统要防的头号问题。
一个因为检索挂了而没查出重复的需求被当成独立需求入库，代价远大于「这次分析失败重来」。

所以约定是：**降级必须是调用方显式做的、且降级本身要被记录**。
`retrieve_node` 因此选择「记录 + 上抛」而不是吞掉。

> 若将来确实要降级继续，**必须同时**把「未做重复检查」写进 `metadata` 并在审核页显示 ——
> 那是一个跨前后端的改动，不该在后端悄悄做半截。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from requirement_agent.tools.base import ToolResult, ToolStatus
from requirement_agent.tools.registry import get_class

logger = logging.getLogger(__name__)

__all__ = [
    "ToolInvocationError",
    "invoke",
    "tool_call_record",
]


class ToolInvocationError(RuntimeError):
    """工具调用失败，且调用方判定**不可降级**。

    抛它而不是返回错误码，是为了让失败走**既有的可见路径**：
    分析图异常 → outbox 任务失败 → 重试 → 最终进死信（运维页看得到）。
    静默返回一个空结果就没人知道了。
    """

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(f"工具 {tool_name} 调用失败：{message}")
        self.tool_name = tool_name


@dataclass(frozen=True, slots=True)
class _CallRecord:
    """一次工具调用的可落库摘要。"""

    tool: str
    params: dict[str, Any]
    status: str
    duration_ms: float
    count: int | None
    message: str | None
    sample: list[dict[str, Any]] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "params": self.params,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "count": self.count,
            "message": self.message,
            "sample": self.sample,
        }


# 留痕里抽样几条结果。**刻意只取前几条，不是全部** —— 审计日志不该成为数据副本
# （与 `tools/base.py` 的 `_ARG_TEXT_CHARS` 同一条理由）。但「一条都不记」也不够用：
# 此前只记 `count`，于是留痕能告诉你「召回了 5 条」却告诉不了你是哪 5 条，
# 而排查「为什么这条没判重复」时，那正是唯一有用的信息。
_SAMPLE_SIZE = 3


def _result_sample(value: object) -> list[dict[str, Any]] | None:
    """从工具结果里抽前几条的「编号 + 余弦」。非列表结果返回 `None`。"""
    if not isinstance(value, list):
        return None
    rows: list[dict[str, Any]] = []
    for item in value[:_SAMPLE_SIZE]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {"requirement_key": item.get("requirement_key")}
        # 只有检索类结果才有余弦；没有就**不写这个键**，免得看起来像「余弦是 null」
        if "vector_similarity" in item:
            row["vector_similarity"] = item.get("vector_similarity")
        rows.append(row)
    return rows or None


def invoke(name: str, params: dict[str, Any] | None = None, *, consumer: str = "analysis") -> ToolResult:
    """经注册表调用一个工具。

    - 工具不存在 / 消费方不在白名单 → 返回 `ERROR`（不抛）——
      那是**编程错误**，让它出现在结果里比让它变成异常更容易定位。
    - 工具本身的失败（参数不合法、超时、依赖报错）→ 由 `BaseTool.run` 兜成 `ERROR`。
    """
    cls = get_class(name)
    if cls is None:
        logger.warning("event=tool_not_found tool=%s consumer=%s", name, consumer)
        return ToolResult.error(f"没有名为 {name} 的工具")
    if consumer not in cls.allowed_consumers:
        logger.warning(
            "event=tool_consumer_denied tool=%s consumer=%s allowed=%s",
            name, consumer, list(cls.allowed_consumers),
        )
        return ToolResult.error(f"消费方 {consumer} 不允许调用 {name}")

    return cls().run(params or {})


def tool_call_record(
    name: str, params: dict[str, Any] | None, result: ToolResult
) -> dict[str, Any]:
    """把一次调用整理成可落库的形状（进 state / metadata）。

    **参数原样记录** —— 当前工具的参数都是短标识（需求编号、查询串、limit），
    不像需求正文那样可能上千字。等出现会带长文本的工具时再引入摘要与截断
    （`BaseTool._audit` 里已有一份截断逻辑，可以搬）。
    """
    value = result.result
    count: int | None = None
    if isinstance(value, (list, dict)):
        count = len(value)
    return _CallRecord(
        tool=name,
        params=dict(params or {}),
        status=result.status.value,
        duration_ms=result.duration_ms,
        count=count,
        message=result.message,
        sample=_result_sample(value),
    ).as_dict()


def is_ok(result: ToolResult) -> bool:
    """成功或「查了但没有」都算拿到了可用答案；只有 `ERROR` 才算失败。

    ⚠️ 把 `EMPTY` 当成功是刻意的 —— 「没有相似需求」是合法答案，
    不是失败（这正是三态设计的意义，见 `tools/base.py`）。
    """
    return result.status is not ToolStatus.ERROR
