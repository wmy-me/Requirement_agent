"""真实 LangGraph 图定义：分析子图 + 决策/落库子图。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from langgraph.graph import END, START, StateGraph

from requirement_agent.workflows.agents_nodes import (
    analyze_node,
    decide_node,
    extract_node,
    retrieve_node,
    risk_node,
)
from requirement_agent.workflows.commit_nodes import (
    commit_requirement_node,
    record_review_node,
    reject_requirement_node,
    route_decision,
)
from requirement_agent.workflows.event_nodes import traced
from requirement_agent.workflows.router import (
    answer_node,
    clarify_node,
    prepare_review_node,
    router_node,
)
from requirement_agent.workflows.state import RequirementState


def _route(state: RequirementState) -> str:
    """条件边的唯一出口；动作来自已校验的 router_node。"""
    return str(state.get("route_action") or "finish")


def build_analysis_graph():
    """动态分析图：router 根据意图和产物决定下一跳。

    `prepare_review` 有硬前置条件（extract/retrieve/analyze/risk），因此即使模型
    建议跳步也会被 router 的 Python 校验回退。图仍保持纯计算，不写正式需求。

    **每个节点外面包了事件适配器**（`event_nodes.traced`，B2.1）—— 包装只发生在
    建图这一层，`agents_nodes.py` 的节点本体一行不动（追加文档 §3.5 的要求）。
    事件进 `state["run_events"]` 的追加通道，由调用方统一落库；图本身仍然
    **纯计算、不写库**。
    """
    graph = StateGraph(RequirementState)
    # router 自己产出 route_selected；不伪装成业务执行节点，保持历史 node_started 时间线兼容。
    graph.add_node("router", router_node)
    graph.add_node("answer", traced("answer", answer_node))
    graph.add_node("clarify", traced("clarify", clarify_node))
    graph.add_node("extract", traced("extract", extract_node))
    graph.add_node("retrieve", traced("retrieve", retrieve_node))
    graph.add_node("analyze", traced("analyze", analyze_node))
    graph.add_node("risk", traced("risk", risk_node))
    graph.add_node("decide", traced("decide", decide_node))
    graph.add_node("prepare_review", prepare_review_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", _route, {
        "answer": "answer", "clarify": "clarify", "extract": "extract", "retrieve": "retrieve",
        "analyze": "analyze", "risk": "risk", "prepare_review": "prepare_review", "finish": END,
    })
    graph.add_edge("extract", "router")
    graph.add_edge("retrieve", "router")
    graph.add_edge("analyze", "router")
    graph.add_edge("risk", "router")
    # 保留历史 decide 节点的产物和事件语义，prepare_review 只负责护栏。
    graph.add_edge("prepare_review", "decide")
    graph.add_edge("decide", "router")
    graph.add_edge("answer", END)
    graph.add_edge("clarify", END)
    return graph.compile()


def build_decision_graph():
    """决策图：记录审核 → 按 decision 路由 → commit 落库 / reject。

    图本身不持有事务；调用方 ReviewService 负责 session、commit 与 rollback。
    """
    graph = StateGraph(RequirementState)
    graph.add_node("record", record_review_node)
    graph.add_node("commit", commit_requirement_node)
    graph.add_node("reject", reject_requirement_node)
    graph.add_edge(START, "record")
    graph.add_conditional_edges(
        "record",
        route_decision,
        {"commit": "commit", "reject": "reject"},
    )
    graph.add_edge("commit", END)
    graph.add_edge("reject", END)
    return graph.compile()


analysis_graph = build_analysis_graph()
decision_graph = build_decision_graph()


def stream_analysis(initial: dict[str, Any]) -> Iterator[dict[str, dict[str, Any]]]:
    """逐节点产生动态图增量，供 SSE 适配器在每个安全点写 PostgreSQL checkpoint。"""
    seeded = {**initial, "started_at": time.monotonic(), "max_routes": int(initial.get("max_routes") or 12)}
    yield from analysis_graph.stream(seeded, config={"recursion_limit": 32}, stream_mode="updates")


def run_analysis(
    *,
    source_id: int | None = None,
    source_text: str,
    source_type: str = "web",
    requester_name: str | None = None,
    standardized_text: str | None = None,
    segments: list[dict[str, Any]] | None = None,
    normalized_fields: dict[str, Any] | None = None,
    conversation_mode: bool = False,
    intent: str | None = "requirement",
) -> RequirementState:
    """便捷入口：跑一遍分析图并返回最终 state。

    允许 RequirementService 预先传入标准化文档与字段，避免在图内重复清洗。
    """
    initial: dict[str, Any] = {
        "source_id": source_id,
        "source_text": source_text,
        "source_type": source_type,
        "requester_name": requester_name,
        "started_at": time.monotonic(),
        "max_routes": 12,
        "conversation_mode": conversation_mode,
    }
    if intent:
        initial["intent"] = intent
    if standardized_text:
        initial["standardized_text"] = standardized_text
    if segments:
        initial["segments"] = segments
    if normalized_fields:
        initial["normalized_fields"] = normalized_fields
    # 路由上限是业务护栏；给图略高于该上限的递归预算，避免 LangGraph 自身先报错。
    return analysis_graph.invoke(initial, config={"recursion_limit": 32})


def run_decision(ctx: dict[str, Any]) -> RequirementState:
    """便捷入口：携带 repos+session+审核入参运行决策图，返回含 outcome 的 state。"""
    return decision_graph.invoke({"ctx": ctx})
