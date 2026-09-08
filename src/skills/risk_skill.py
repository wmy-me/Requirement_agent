"""用于需求风险评估的 LLM 技能。"""

from __future__ import annotations

from src.skills.base_skill import BaseSkill


class RiskSkill(BaseSkill):
    """对需求的质量风险、变更风险和技术影响风险进行评估的技能。"""

    def assess(self, extracted: object) -> object:
        from src.agents.extract_agent import ExtractedRequirement
        from src.agents.risk_agent import RiskAgent, RiskAssessment

        if not isinstance(extracted, ExtractedRequirement):
            extracted = ExtractedRequirement.model_validate(extracted)

        fallback = RiskAgent._heuristic_assess(extracted)
        if not self.provider.is_configured():
            return fallback

        system_prompt = (
            "你是一名企业架构与风险评估专家。请基于业务质量、变更影响和技术复杂度，"
            "评估该需求的风险，并返回严格的 JSON 对象。"
        )
        prompt = (
            "请评估当前需求的风险，字段包括：quality_risk、change_risk、technical_impact_risk、confidence。\n"
            "quality_risk、change_risk、technical_impact_risk 的合法值为 low、medium、high。\n"
            f"需求内容：{extracted.model_dump(mode='json')}"
        )

        try:
            payload = self._generate_json(prompt, system_prompt)
            result = RiskAssessment(
                quality_risk=str(payload.get("quality_risk") or fallback.quality_risk),
                change_risk=str(payload.get("change_risk") or fallback.change_risk),
                technical_impact_risk=str(payload.get("technical_impact_risk") or fallback.technical_impact_risk),
                confidence=max(0.0, min(0.95, float(payload.get("confidence") or fallback.confidence))),
            )
            return result
        except Exception:
            return fallback
