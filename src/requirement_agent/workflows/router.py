"""受限的动态工作流路由器。

模型只可以在有限动作集中建议下一步；Python 会重新检查前置条件并在不可用、
超时或 JSON 非法时退回确定性计划。这使模型不能绕过检索/风险评估直接声称冲突或
生成待审结果。
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from requirement_agent.infrastructure.llm.openai_provider import LLMProvider
from requirement_agent.infrastructure.llm.model_registry import ModelRegistry
from requirement_agent.config.settings import settings

ACTION_SET = frozenset({"answer", "clarify", "extract", "retrieve", "analyze", "risk", "prepare_review", "finish"})
Intent = Literal["general_question", "requirement", "unclear"]


class RouteProposal(BaseModel):
    intent: Intent
    action: str
    reason: str = Field(min_length=1, max_length=300)
    missing_information: list[str] = Field(default_factory=list, max_length=4)


def _is_requirement_text(text: str) -> bool:
    words = ("需求", "功能", "新增", "修改", "优化", "支持", "导出", "导入", "审批", "权限", "接口", "页面", "流程", "字段", "报表", "我想", "我需要")
    return any(word in text for word in words)


def _fallback(state: dict[str, Any]) -> RouteProposal:
    text = str(state.get("source_text") or "").strip()
    completed = set(state.get("completed_steps") or [])
    intent = str(state.get("intent") or "")
    if not intent:
        intent = "requirement" if _is_requirement_text(text) else "general_question"
    if intent == "general_question":
        return RouteProposal(intent="general_question", action="answer", reason="输入是普通问答，未出现明确需求表达。")
    if "extract" not in completed:
        return RouteProposal(intent="requirement", action="extract", reason="需要先把需求材料整理为结构化字段。")
    extracted = state.get("extracted") or {}
    missing: list[str] = []
    if state.get("conversation_mode") and (len(text) < 12 or not extracted.get("requirements")):
        missing.append("请说明目标用户、期望行为或验收标准。")
    if missing:
        return RouteProposal(intent="requirement", action="clarify", reason="需求缺少可验证的行为描述。", missing_information=missing)
    if "retrieve" not in completed:
        return RouteProposal(intent="requirement", action="retrieve", reason="需要检索既有需求作为重复或冲突判断的证据。")
    if "analyze" not in completed:
        return RouteProposal(intent="requirement", action="analyze", reason="检索已完成，可基于候选证据进行关系分析。")
    if "risk" not in completed:
        return RouteProposal(intent="requirement", action="risk", reason="生成待审结论前必须完成风险评估。")
    if "prepare_review" not in completed:
        return RouteProposal(intent="requirement", action="prepare_review", reason="抽取、检索、关系分析和风险评估均已完成。")
    return RouteProposal(intent="requirement", action="finish", reason="待审分析包已准备完成。")


def _model_proposal(state: dict[str, Any]) -> RouteProposal | None:
    # 路由模型必须显式配置，避免在每个节点上悄悄额外消耗全局默认模型。
    # MODEL_ROUTES["router"] 仍沿用项目既有 provider/model/fallback 配置形状。
    if not settings.model_routes.get("router"):
        return None
    provider = LLMProvider(spec=ModelRegistry().get_chat_model("router"), task_type="router")
    if not provider.is_configured():
        return None
    prompt = {
        "input": str(state.get("source_text") or "")[:1200],
        "completed_steps": state.get("completed_steps") or [],
        "has_candidates": bool(state.get("candidates")),
        "has_analysis": bool(state.get("analysis")),
        "has_risk": bool(state.get("risk")),
        "allowed_actions": sorted(ACTION_SET),
    }
    system = "你是需求工作流路由器。只返回 JSON：intent、action、reason、missing_information。不得给出其他文本。"
    try:
        raw = provider.generate(json.dumps(prompt, ensure_ascii=False), system_prompt=system)
        return RouteProposal.model_validate(json.loads(raw))
    except Exception:  # 网络/超时/非法 JSON 都必须回到确定性安全路径
        return None


def _allowed(proposal: RouteProposal, state: dict[str, Any]) -> bool:
    completed = set(state.get("completed_steps") or [])
    if proposal.action not in ACTION_SET:
        return False
    if proposal.intent == "general_question":
        return proposal.action in {"answer", "finish"}
    # 已执行节点不可被模型重复调度，防止“看起来合法”的循环。
    if proposal.action in completed:
        return False
    prerequisites = {
        "retrieve": {"extract"}, "analyze": {"extract", "retrieve"},
        "risk": {"extract", "analyze"}, "prepare_review": {"extract", "retrieve", "analyze", "risk"},
    }
    return prerequisites.get(proposal.action, set()).issubset(completed)


def router_node(state: dict[str, Any]) -> dict[str, Any]:
    """选择一跳并留下可审计理由；非法模型建议退回到确定性安全计划。"""
    count = int(state.get("route_count") or 0) + 1
    max_routes = int(state.get("max_routes") or 12)
    elapsed = time.monotonic() - float(state.get("started_at") or time.monotonic())
    if count > max_routes or elapsed > 120:
        proposal = RouteProposal(intent="unclear", action="finish", reason="已达到路由次数或耗时上限，停止继续路由。")
        error = {"code": "route_limit", "message": proposal.reason}
    else:
        provider_configured = bool(settings.model_routes.get("router"))
        candidate = _model_proposal(state)
        fallback = _fallback(state)
        candidate_allowed = bool(candidate and _allowed(candidate, state))
        proposal = candidate if candidate_allowed else fallback
        error = ({"code": "invalid_router_output", "message": "模型输出非法、超时或不满足前置条件，已使用安全回退。"}
                 if not candidate_allowed and provider_configured else None)
    record = {"action": proposal.action, "reason": proposal.reason, "input_summary": str(state.get("source_text") or "")[:160], "route_count": count}
    output: dict[str, Any] = {"intent": proposal.intent, "route_action": proposal.action, "route_reason": proposal.reason,
                              "missing_information": proposal.missing_information, "route_count": count, "route_history": [record],
                              "run_events": [{"event_type": "route_selected", "node": "router", "payload": record}]}
    if not state.get("decision"):
        output["next_action"] = proposal.action
    if error:
        output["error_state"] = error
    return output


def answer_node(state: dict[str, Any]) -> dict[str, Any]:
    # 实际文本由 SSE 适配器流式调用问答模型；这里仅声明图已选择 answer 能力。
    return {"answer": "model_answer", "completed_steps": ["answer"]}


def clarify_node(state: dict[str, Any]) -> dict[str, Any]:
    missing = list(state.get("missing_information") or ["请补充目标用户、期望行为和验收标准。"])
    return {"clarification_question": "为了继续分析，" + "；".join(missing), "completed_steps": ["clarify"]}


def prepare_review_node(state: dict[str, Any]) -> dict[str, Any]:
    return {"decision": "manual_review" if (state.get("analysis") or {}).get("duplicate") else "can_commit",
            "completed_steps": ["prepare_review"]}
