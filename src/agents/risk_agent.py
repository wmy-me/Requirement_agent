"""对质量风险、变更风险和技术影响进行评估的风险 Agent。"""

from __future__ import annotations

from pydantic import BaseModel

from src.agents.extract_agent import ExtractedRequirement
from src.skills.risk_skill import RiskSkill


class RiskAssessment(BaseModel):
    quality_risk: str = "low"
    change_risk: str = "low"
    technical_impact_risk: str = "low"
    confidence: float = 0.7


class RiskAgent:
    """遵循项目设计的轻量风险评估 Agent。"""

    def __init__(self, skill: RiskSkill | None = None) -> None:
        self.skill = skill or RiskSkill()

    def assess(self, extracted: ExtractedRequirement) -> RiskAssessment:
        if not self.skill.provider.is_configured():
            return self._heuristic_assess(extracted)
        return self.skill.assess(extracted)

    @staticmethod
    def _heuristic_assess(extracted: ExtractedRequirement) -> RiskAssessment:
        quality_risk = RiskAgent()._evaluate_quality(extracted)
        change_risk = RiskAgent()._evaluate_change(extracted)
        technical_impact_risk = RiskAgent()._evaluate_technical(extracted)
        confidence = RiskAgent()._confidence_score(extracted)

        return RiskAssessment(
            quality_risk=quality_risk,
            change_risk=change_risk,
            technical_impact_risk=technical_impact_risk,
            confidence=confidence,
        )

    def _evaluate_quality(self, extracted: ExtractedRequirement) -> str:
        if not extracted.summary.strip():
            return "medium"
        if len(extracted.requirements) >= 4 and extracted.priority == "high":
            return "high"
        if len(extracted.requirements) >= 3:
            return "medium"
        if extracted.priority == "high":
            return "medium"
        return "low"

    def _evaluate_change(self, extracted: ExtractedRequirement) -> str:
        if extracted.business_domain in {"auth", "workflow", "data", "reporting"}:
            return "medium"
        if extracted.priority == "high" and len(extracted.requirements) >= 2:
            return "high"
        return "low"

    def _evaluate_technical(self, extracted: ExtractedRequirement) -> str:
        if extracted.business_domain in {"auth", "data", "integration"}:
            return "medium"
        if len(extracted.tags) >= 4 or any(tag in extracted.tags for tag in ["导出", "筛选", "报表", "权限", "登录"]):
            return "medium"
        if "接口" in extracted.summary or "第三方" in extracted.summary:
            return "high"
        if len(extracted.tags) >= 3:
            return "medium"
        return "low"

    def _confidence_score(self, extracted: ExtractedRequirement) -> float:
        confidence = 0.55
        if extracted.summary.strip():
            confidence += 0.1
        if len(extracted.requirements) >= 2:
            confidence += 0.1
        if extracted.priority in {"high", "medium"}:
            confidence += 0.05
        if extracted.business_domain in {"auth", "workflow", "data", "integration", "reporting"}:
            confidence += 0.05
        if len(extracted.tags) >= 4:
            confidence += 0.05
        return round(min(confidence, 0.92), 2)
