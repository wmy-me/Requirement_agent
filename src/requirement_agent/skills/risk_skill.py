"""用于需求风险评估的 LLM 技能。"""

from __future__ import annotations

from requirement_agent.skills.base_skill import BaseSkill
from requirement_agent.skills.prompts import RISK_SYSTEM_PROMPT, build_risk_user_prompt


class RiskSkill(BaseSkill):
    """LLM 风险评估技能。

    与 RiskAgent 的关系同样是“Skill 负责模型输出，Agent 负责入口与回退”；
    模型返回异常时直接回退到启发式结果，不阻塞审核决策。
    """

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
                confidence=max(0.0, min(0.95, float(payload.get("confidence") or fallback.confidence))),
            )
            return result
        except Exception:
            return fallback
