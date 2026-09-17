"""分析子图：Agent 编排节点（纯计算，不写库）。"""

from __future__ import annotations

from typing import Any

from requirement_agent.agents.analyze_agent import AnalyzeAgent
from requirement_agent.agents.extract_agent import ExtractAgent, ExtractedRequirement
from requirement_agent.agents.risk_agent import RiskAgent
from requirement_agent.application.decision_rules import next_action_for
from requirement_agent.config.settings import settings
from requirement_agent.tools.invoker import (
    ToolInvocationError,
    invoke,
    is_ok,
    tool_call_record,
)


def extract_node(state: dict[str, Any]) -> dict[str, Any]:
    """抽取结构化需求，保留完整字段（领域/优先级/子需求/原文）供后续节点使用。"""
    extracted = ExtractAgent().extract(
        state.get("source_text") or "",
        source_type=state.get("source_type", "web"),
        requester_name=state.get("requester_name"),
    )
    return {"extracted": extracted.model_dump(mode="python")}


# 与 `tools/search_requirements.py` 的 schema 上限一致。
# 实测待审来源的 query 都是 summary（42–58 字），不会触到它。
_RETRIEVE_QUERY_MAX = 500


def retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """基于抽取结果检索已有相似/相关需求。

    这里只做候选召回，不直接下业务判断；是否算重复/关联由 analyze_node 决定。

    **B3 起改走工具注册表**（`tools/invoker.invoke`）—— 与其它消费者走同一条路，
    于是超时、参数校验、消费方白名单、调用留痕都由那一层统一处理。
    查的是同一个 `RetrievalService.search`，所以**候选结果不变**。

    ⚠️ **检索失败不降级。** 检索是判重复的唯一依据，静默降级成「没有候选」
    会让 analyze 得出「独立」的结论 —— 而一个因检索挂掉而漏掉重复的需求被当成
    独立需求入库，代价远大于「这次分析失败、走 outbox 重试」。所以这里**上抛**，
    让失败走既有的可见路径（重试 → 死信 → 运维页）。理由详见 `tools/invoker.py`。
    """
    extracted = state.get("extracted") or {}
    query = str(
        extracted.get("summary") or extracted.get("raw_text") or state.get("source_text") or ""
    )
    # 候选条数从配置读（默认 10，原先是写死的 5）。
    # **不是「越大越好」**：它决定 contrast 统计的样本量，而 contrast 取的是中位数 ——
    # 样本太少时中位数只用 3~4 个数、噪声很大，实测同一条真重复两次走查会分别落到
    # related 与 duplicate。B3 时这里写死 5 是刻意的（那批只接线、不调检索策略）；
    # B4 有了 contrast 之后，样本量成了正确性问题，故解耦到 `SIMILARITY_RECALL_LIMIT`。
    limit = settings.similarity_recall_limit
    params = {"query": query[:_RETRIEVE_QUERY_MAX], "limit": limit}
    result = invoke("search_requirements", params, consumer="analysis")
    record = tool_call_record("search_requirements", params, result)

    if not is_ok(result):
        raise ToolInvocationError("search_requirements", str(result.message or "未知错误"))

    return {"candidates": result.result or [], "tool_calls": [record]}


def _extracted_model(state: dict[str, Any]) -> ExtractedRequirement:
    """把状态中的 dict 还原为强类型对象，统一后续节点输入。"""
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
    """综合分析+风险给出是否需要人工审核的决策。

    该节点只产出状态，不写库；HTTP 路由、RequirementService 与 SSE 回放都复用同一决策规则。
    """
    analysis = state.get("analysis") or {}
    risk = state.get("risk") or {}
    action = next_action_for(analysis, risk)
    return {"decision": action, "next_action": action}
