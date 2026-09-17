"""Agent 运行与事件的领域模型（B2.1）。

**为什么要有这个模块。** 在它之前，系统里有两条互不相干的「运行」：

- **对话助手**有 `agent_run` 行（状态机、断点、续跑一应俱全）；
- **需求分析**跑在 LangGraph 分析图里，**跑完零留痕** —— 没有 run 行、没有节点
  时间线、没有失败记录，结果只被拆散塞进 `requirement_source.metadata` 的几个
  JSONB 键里。想知道「上个月那条需求是怎么被判成重复的」只能翻日志。

这个模块把「一次运行」和「一次运行里发生的事」变成有名字、有枚举、有终态概念的东西，
让两条腿能落在同一张表上。

纯函数 + 类型别名，**不接 session、不读 settings、不写库** —— 落库是
`infrastructure/db/repositories/agent_run.py` 的事。

## 三条容易搞错的地方

1. **`sequence` 是「每个 run 内」从 1 开始，不是全局自增。** `after_seq=N` 回放
   就是按它解释的。用自增列会得到跨 run 的全局序号，回放语义立刻不成立。

2. **终态与「未收尾」是两回事。** `completed`/`failed`/`cancelled` 是终态（到了就不
   再推进）；而 `waiting_review` **不是终态，但也不算「跑得动」** —— 分析跑完等人审
   时不该被再跑一遍。两者各有一份集合，别合并。

3. **payload 必须脱敏与截断之后才能落库或推送。** 它会经 SSE 给前端、也会长期保留，
   而分析过程的 payload 里很容易夹带完整 prompt、原文、甚至凭据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, Literal

__all__ = [
    "ACTIVE_STATUSES",
    "EVENT_TYPES",
    "MAX_PAYLOAD_CHARS",
    "NODE_STATUSES",
    "RUN_STATUSES",
    "TERMINAL_STATUSES",
    "EventType",
    "NodeStatus",
    "RunEvent",
    "RunStatus",
    "RunType",
    "redact_payload",
]

RunType = Literal["conversation", "analysis"]

#: 与 `migrations/021` 的 `agent_run_status_check` **必须一致**。
#: ⚠️ 不含 `paused` —— 它从来没被约束允许过，那个 pause 端点已删除。
RUN_STATUSES: Final = (
    "queued",
    "running",
    "waiting_review",
    "completed",
    "failed",
    "cancelled",
    "retrying",
)
RunStatus = Literal[
    "queued", "running", "waiting_review", "completed", "failed", "cancelled", "retrying"
]

#: 到了就不该再被推进。
TERMINAL_STATUSES: Final = frozenset({"completed", "failed", "cancelled"})

#: 「占着位置、还没跑完」—— 同一来源/同一对话同时只允许一个。
#: ⚠️ `waiting_review` 在这里但**不在** `TERMINAL_STATUSES`：分析跑完等人审时，
#: 不该被再跑一遍，但它也不是「完成」。
ACTIVE_STATUSES: Final = frozenset({"queued", "running", "retrying", "waiting_review"})

NODE_STATUSES: Final = ("pending", "running", "completed", "failed", "skipped")
NodeStatus = Literal["pending", "running", "completed", "failed", "skipped"]

#: 追加实施文档 §3.4 的事件清单，逐条采纳。
EVENT_TYPES: Final = (
    "run_started",
    "node_started",
    "node_completed",
    "tool_started",
    "tool_completed",
    "candidate_found",
    "relation_found",
    "risk_found",
    "review_required",
    "run_completed",
    "run_failed",
    "run_cancelled",
    "progress",
)
EventType = Literal[
    "run_started", "node_started", "node_completed", "tool_started", "tool_completed",
    "candidate_found", "relation_found", "risk_found", "review_required",
    "run_completed", "run_failed", "run_cancelled", "progress",
]

#: 单条事件 payload 的字符上限。超了截断并留标记。
#: 取值依据：正常事件（节点名 + 几十个字）在 200 字符内；2000 足够装一次
#: 分析结论的摘要，又不足以把完整 prompt 或原文灌进来。
MAX_PAYLOAD_CHARS: Final = 2000

#: 出现这些键就**整键丢掉**（而不是把值替换成 `***`）。
#: 为什么整键丢：留下 `"api_key": "***"` 会让人以为「这里本来有个可用的 key，
#: 只是被脱敏了」，而真正该传达的是「这个字段不该出现在事件里」。
SENSITIVE_KEY_MARKERS: Final = (
    "api_key", "apikey", "token", "authorization", "password", "secret",
    "prompt", "raw_text", "original_text", "text",  # 原文类：可能整篇需求正文
)

_REDACTED = "<已脱敏：该字段不进入运行事件>"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def redact_payload(payload: Any, *, max_chars: int = MAX_PAYLOAD_CHARS) -> dict[str, Any]:
    """脱敏 + 截断，返回可安全落库/推送的 payload。

    追加文档 §7.2 的验收项：「事件内容不会泄漏 API Key、完整 Prompt 或敏感原文」。
    这条是在**写入端**做的，而不是在读取端 —— 读的时候再脱敏，脏数据已经躺在库里了。

    三条规则：
    1. 命中敏感键名的**整个键替换**成一句说明（不是打码，见 `SENSITIVE_KEY_MARKERS`）；
    2. 超长字符串截断并留 `…<已截断>` 尾巴；
    3. 递归处理嵌套结构；深度超过 6 层直接截断（防环、防病态嵌套）。
    """
    if not isinstance(payload, dict):
        # 非 dict 的 payload 统一包一层，免得落库时形状随调用方变化
        payload = {"value": payload}

    def walk(node: Any, depth: int) -> Any:
        if depth > 6:
            return "<已截断：嵌套过深>"
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                name = str(key)
                out[name] = _REDACTED if _is_sensitive(name) else walk(value, depth + 1)
            return out
        if isinstance(node, (list, tuple)):
            return [walk(item, depth + 1) for item in node[:50]]
        if isinstance(node, str) and len(node) > max_chars:
            return node[:max_chars] + "…<已截断>"
        return node

    result = walk(payload, 0)
    # 最后再兜一道总量：单键不超、总量可能超（很多个长键拼起来）
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    if len(encoded) > max_chars:
        return {
            "truncated": True,
            "preview": encoded[:max_chars] + "…<已截断>",
            "note": "payload 超长，已整体截断；完整内容见对应节点的业务产物",
        }
    return result


@dataclass(frozen=True, slots=True)
class RunEvent:
    """一次运行里发生的一件事。

    **没有 `sequence` 字段**是刻意的：序号是落库时才分配的（见
    `infrastructure/db/repositories/agent_run.py`），生产事件的节点不该关心
    「我是第几条」—— 那会让同一段代码在不同调用路径下产生不同的序号。
    """

    event_type: str
    node: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_event_dict(self) -> dict[str, Any]:
        """转成 state 通道里的形状（与 `tool_calls` 同一种：**普通 dict**）。

        ⚠️ **这里不做脱敏。** 脱敏收在落库那一个点上
        （`infrastructure/db/repositories/agent_run.append_events`）——
        事件也可能从别的路径进来（比如对话管线手工构造），
        在每个生产端各做一次迟早漏一个。写入边界是唯一的必经之路。
        """
        return {"event_type": self.event_type, "node": self.node, "payload": dict(self.payload)}
