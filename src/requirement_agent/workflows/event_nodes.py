"""节点级事件适配器（B2.1）。

**它做的事**：把现有图节点包一层，产出 `node_started` / `node_completed` 以及该节点
专属的事件（`candidate_found` / `relation_found` / `risk_found` / `review_required`）
到 `state["run_events"]` 这个追加通道。

**它刻意不做的事**：

1. **不改节点本体。** 追加文档 §3.5 明确要求「为分析图节点增加事件适配器，
   **不改变节点核心业务逻辑**」。包装只发生在 `graphs.build_analysis_graph` 建图时，
   `agents_nodes.py` 一行不动 —— 单独调节点的测试（`test_workflow_tool_wiring.py`
   直接调 `retrieve_node`）因此完全不受影响。

2. **不写库。** 事件只进 state 通道，落库由调用方（Application 层）在拿到图的结果后
   统一做。理由：分析图的节点在 B3 就定下「纯计算，不写库」的纪律，
   让它一边算一边写会把事务边界搞乱；而且异常路径上 LangGraph 的
   `invoke` 会抛、累积的 state 会丢 —— 靠节点落库在失败时恰好什么都留不下。

3. **不分配序号。** 序号是落库时按 run 分配的（见 `migrations/021` 文件头③）。

## 失败路径为什么要包一层异常

节点抛错时，state 通道里的东西**全丢了**（`invoke` 直接抛，没有返回值可读）。
所以「失败在哪一步」不能靠事件通道传递，只能随异常带出去 ——
`NodeFailure` 把节点名附在异常上，调用方接住后据此写 `current_node`。

⚠️ 这会**改变经图调用时看到的异常类型**（`ToolInvocationError` → `NodeFailure`）。
已核对：全仓没有生产代码 `except ToolInvocationError`（只有两条测试**直接调
`retrieve_node`**，不走图，所以不受影响），而 outbox 任务捕的是宽泛的 `Exception`。
原始异常挂在 `__cause__` 上，traceback 完整保留；`NodeFailure` 的消息里也带上
原始文本，所以 `outbox_event.last_error` 仍能看到真正的原因。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from requirement_agent.domain.agent_run import RunEvent

__all__ = ["NodeFailure", "traced"]

NodeFn = Callable[[dict[str, Any]], dict[str, Any]]


class NodeFailure(RuntimeError):
    """某个图节点抛错，附上节点名。

    **消息里要带原始原因**：`outbox_event.last_error` 存的是 `str(exc)`，
    如果这里只说「节点 retrieve 失败」而不带底层原因，排障时等于没写。
    """

    def __init__(self, node: str, cause: BaseException) -> None:
        super().__init__(f"节点 {node} 失败：{cause}")
        self.node = node
        self.cause = cause


def traced(name: str, node_fn: NodeFn) -> NodeFn:
    """把节点包成「先发 node_started、跑完发 node_completed + 专属事件」。"""

    def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        try:
            output = node_fn(state)
        except Exception as exc:  # noqa: BLE001 —— 包一层就为把它带出去，不吞
            raise NodeFailure(name, exc) from exc

        events = [
            RunEvent("node_started", node=name).to_event_dict(),
            *(event.to_event_dict() for event in _extra_events(name, output)),
            RunEvent("node_completed", node=name, payload=_summary(name, output)).to_event_dict(),
        ]
        # ⚠️ **不能覆盖调用方已有的事件**：`{**output, "run_events": events}` 会把
        # 节点内部（比如 retrieve_node 目前不产事件，但将来可能）已经攒下的事件丢掉。
        # 追加通道语义是 `add`，这里也照那个语义拼。
        existing = list(output.get("run_events") or [])
        return {**output, "run_events": [*events, *existing]}

    wrapper.__name__ = f"traced_{name}"
    return wrapper


def _summary(name: str, output: dict[str, Any]) -> dict[str, Any]:
    """`node_completed` 的载荷：**只要摘要，不要产物本身**。

    产物（`extracted` 全文、`candidates` 全表）已经落在 `requirement_source.metadata`
    里了，事件里再存一份就是数据副本 —— 而事件是要长期保留、要经 SSE 推送的。
    这里每条都控制在几十个字符内（`redact_payload` 还有一道总长兜底）。
    """
    if name == "extract":
        extracted = output.get("extracted") or {}
        return {
            "requirement_title": extracted.get("requirement_title"),
            "business_domain": extracted.get("business_domain"),
        }
    if name == "retrieve":
        return {"candidate_count": len(output.get("candidates") or [])}
    if name == "analyze":
        analysis = output.get("analysis") or {}
        return {
            "duplicate": analysis.get("duplicate"),
            "related": analysis.get("related"),
            "conflict": analysis.get("conflict"),
        }
    if name == "risk":
        risk = output.get("risk") or {}
        return {
            "quality_risk": risk.get("quality_risk"),
            "change_risk": risk.get("change_risk"),
            "technical_impact_risk": risk.get("technical_impact_risk"),
        }
    if name == "decide":
        return {"decision": output.get("decision")}
    return {}


def _extra_events(name: str, output: dict[str, Any]) -> list[RunEvent]:
    """节点专属事件（追加文档 §3.4 的那几个）。

    它们与 `node_completed` 不是二选一：`node_completed` 说「这一步跑完了」，
    这些说「这一步产出了什么」。合并成一条会让「按事件类型过滤」变得不可能。
    """
    if name == "retrieve":
        candidates = output.get("candidates") or []
        if not candidates:
            return []
        # 只记前几条的编号与余弦 —— 与工具留痕的 sample 同一取舍（审计日志不该成为数据副本）
        return [
            RunEvent(
                "candidate_found",
                node=name,
                payload={
                    "count": len(candidates),
                    "sample": [
                        {
                            "requirement_key": item.get("requirement_key"),
                            "vector_similarity": item.get("vector_similarity"),
                        }
                        for item in candidates[:3]
                    ],
                },
            )
        ]
    if name == "analyze":
        analysis = output.get("analysis") or {}
        hit = analysis.get("duplicate") or analysis.get("related") or analysis.get("conflict")
        if not hit:
            return []
        return [
            RunEvent(
                "relation_found",
                node=name,
                payload={
                    "duplicate": bool(analysis.get("duplicate")),
                    "related": bool(analysis.get("related")),
                    "conflict": bool(analysis.get("conflict")),
                },
            )
        ]
    if name == "risk":
        risk = output.get("risk") or {}
        if not any(str(risk.get(key)) == "high" for key in
                   ("quality_risk", "change_risk", "technical_impact_risk")):
            return []
        return [RunEvent("risk_found", node=name, payload={"level": "high"})]
    if name == "decide":
        decision = str(output.get("decision") or "")
        if decision != "manual_review":
            return []
        return [RunEvent("review_required", node=name, payload={"decision": decision})]
    return []
