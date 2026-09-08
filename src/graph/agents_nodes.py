"""分析子图：Agent 编排节点（纯计算，不写库）。"""

from __future__ import annotations

from typing import Any

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent, ExtractedRequirement
from src.agents.retrieval_agent import RetrievalAgent
from src.agents.risk_agent import RiskAgent
from src.application.decision_rules import next_action_for


def extract_node(state: dict[str, Any]) -> dict[str, Any]:
    """抽取结构化需求，保留完整字段（领域/优先级/子需求/原文）供后续节点使用。"""
    extracted = ExtractAgent().extract(
        state.get("source_text") or "",
        source_type=state.get("source_type", "web"),
        requester_name=state.get("requester_name"),
    )
    return {"extracted": extracted.model_dump(mode="python")}


def retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """基于抽取结果检索已有相似/相关需求。"""
    extracted = state.get("extracted") or {}
    query = extracted.get("summary") or extracted.get("raw_text") or state.get("source_text") or ""
    candidates = RetrievalAgent().retrieve(query, limit=5)
    return {"candidates": candidates}


def _extracted_model(state: dict[str, Any]) -> ExtractedRequirement:
    return ExtractedRequirement.model_validate(state.get("extracted") or {})


def analyze_node(state: dict[str, Any]) -> dict[str, Any]:
    """用抽取后的完整字段与检索候选判断重复/关联/冲突。"""
    extracted = _extracted_model(state)
    candidates = state.get("candidates") or []
    analysis = AnalyzeAgent().analyze(extracted, candidates)
    return {"analysis": analysis.model_dump(mode="python")}


def risk_node(state: dict[str, Any]) -> dict[str, Any]:
    """评估质量/变更/技术影响风险（恒在决策前执行）。"""
    extracted = _extracted_model(state)
    risk = RiskAgent().assess(extracted)
    return {"risk": risk.model_dump(mode="python")}


def decide_node(state: dict[str, Any]) -> dict[str, Any]:
    """综合分析+风险给出是否需要人工审核的决策。"""
    analysis = state.get("analysis") or {}
    risk = state.get("risk") or {}
    action = next_action_for(analysis, risk)
    return {"decision": action, "next_action": action}
