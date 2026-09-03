"""Risk agent for quality, change, and technical impact evaluation."""

from __future__ import annotations

from pydantic import BaseModel

from src.agents.extract_agent import ExtractedRequirement


class RiskAssessment(BaseModel):
    quality_risk: str = "low"
    change_risk: str = "low"
    technical_impact_risk: str = "low"
    confidence: float = 0.7


class RiskAgent:
    """Minimal risk classification aligned with the project design."""

    def assess(self, extracted: ExtractedRequirement) -> RiskAssessment:
        quality_risk = self._evaluate_quality(extracted)
        change_risk = self._evaluate_change(extracted)
        technical_impact_risk = self._evaluate_technical(extracted)
        confidence = 0.8 if any(item in extracted.tags for item in ["登录", "权限", "支付", "审批"]) else 0.7

        return RiskAssessment(
            quality_risk=quality_risk,
            change_risk=change_risk,
            technical_impact_risk=technical_impact_risk,
            confidence=confidence,
        )

    def _evaluate_quality(self, extracted: ExtractedRequirement) -> str:
        if len(extracted.requirements) >= 3 and extracted.priority == "high":
            return "medium"
        if extracted.priority == "high":
            return "medium"
        return "low"

    def _evaluate_change(self, extracted: ExtractedRequirement) -> str:
        if extracted.business_domain in {"auth", "workflow", "data"}:
            return "medium"
        if extracted.priority == "high":
            return "high"
        return "low"

    def _evaluate_technical(self, extracted: ExtractedRequirement) -> str:
        if extracted.business_domain in {"auth", "data"}:
            return "medium"
        if len(extracted.tags) >= 3:
            return "medium"
        return "low"
