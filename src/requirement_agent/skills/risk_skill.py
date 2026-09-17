"""用于需求风险评估的 LLM 技能。"""

from __future__ import annotations

import logging

from requirement_agent.skills.base_skill import BaseSkill
from requirement_agent.skills.prompts import RISK_SYSTEM_PROMPT, build_risk_user_prompt

logger = logging.getLogger(__name__)

# 置信度的安全上限：避免错误的 100% 绝对表达
_CONFIDENCE_MAX = 0.95


def _coerce_confidence(value: object, default: float) -> float:
    """把模型的置信度收进 0~0.95；**非数值时退回默认值，而不是丢弃整个结果**。

    实测模型会给 `"low"` 这类词而非数字。此前写成 `float(payload.get("confidence") or ...)`，
    抛 ValueError 会让整个 `RiskAssessment(...)` 构造失败，于是三个本来有效的风险等级
    也一起被启发式覆盖——一个字段的格式偏差拖垮整份结果。
    """
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0.0, min(_CONFIDENCE_MAX, numeric))


class RiskSkill(BaseSkill):
    """LLM 风险评估技能。

    与 RiskAgent 的关系同样是“Skill 负责模型输出，Agent 负责入口与回退”；
    模型返回异常时直接回退到启发式结果，不阻塞审核决策。
    """

    task_type = "risk"

    def assess(self, extracted: object) -> object:
        """评估风险并把 confidence 约束在前端可展示的安全区间。"""
        from requirement_agent.agents.extract_agent import ExtractedRequirement
        from requirement_agent.agents.risk_agent import RiskAgent, RiskAssessment

        if not isinstance(extracted, ExtractedRequirement):
            extracted = ExtractedRequirement.model_validate(extracted)

        fallback = RiskAgent._heuristic_assess(extracted)
        if not self.provider.is_configured():
            return fallback

        system_prompt = RISK_SYSTEM_PROMPT
        prompt = build_risk_user_prompt(requirement_json=extracted.model_dump(mode="json"))

        try:
            payload = self._generate_json(prompt, system_prompt)
            result = RiskAssessment(
                quality_risk=str(payload.get("quality_risk") or fallback.quality_risk),
                change_risk=str(payload.get("change_risk") or fallback.change_risk),
                technical_impact_risk=str(payload.get("technical_impact_risk") or fallback.technical_impact_risk),
                # 置信度只作为解释信号，限制在 0~0.95，避免错误的 100% 绝对表达。
                # 用 _coerce_confidence：模型给出非数值时只回退这一个字段，不丢整份结果。
                confidence=_coerce_confidence(payload.get("confidence"), fallback.confidence),
                # 标明来源：前端据此决定写「模型置信度」还是「规则估算置信度」
                source="llm",
            )
            return result
        except Exception as exc:
            logger.warning("event=skill_fallback skill=risk error=%s", exc)
            return fallback
