"""真实 LangGraph 图定义：分析子图 + 决策/落库子图。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.graph.agents_nodes import (
    analyze_node,
    decide_node,
    extract_node,
    retrieve_node,
    risk_node,
)
from src.graph.commit_nodes import (
    commit_requirement_node,
    record_review_node,
    reject_requirement_node,
    route_decision,
)
from src.graph.state import RequirementState


def build_analysis_graph():
    """分析图：抽取 → 检索 → 冲突/重复分析 → 风险 → 是否需要人工审核。"""
    graph = StateGraph(RequirementState)
    graph.add_node("extract", extract_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("risk", risk_node)
    graph.add_node("decide", decide_node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "retrieve")
    graph.add_edge("retrieve", "analyze")
    graph.add_edge("analyze", "risk")  # 风险恒在决策前执行
    graph.add_edge("risk", "decide")
    graph.add_edge("decide", END)
    return graph.compile()


def build_decision_graph():
    """决策图：记录审核 → 按 decision 路由 → commit 落库 / reject。"""
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


def run_analysis(
    *,
    source_id: int | None = None,
    source_text: str,
    source_type: str = "web",
    requester_name: str | None = None,
    standardized_text: str | None = None,
    segments: list[dict[str, Any]] | None = None,
    normalized_fields: dict[str, Any] | None = None,
) -> RequirementState:
    """便捷入口：跑一遍分析图并返回最终 state。"""
    initial: dict[str, Any] = {
        "source_id": source_id,
        "source_text": source_text,
        "source_type": source_type,
        "requester_name": requester_name,
    }
    if standardized_text:
        initial["standardized_text"] = standardized_text
    if segments:
        initial["segments"] = segments
    if normalized_fields:
        initial["normalized_fields"] = normalized_fields
    return analysis_graph.invoke(initial)


def run_decision(ctx: dict[str, Any]) -> RequirementState:
    """便捷入口：携带 repos+session+审核入参运行决策图，返回含 outcome 的 state。"""
    return decision_graph.invoke({"ctx": ctx})
