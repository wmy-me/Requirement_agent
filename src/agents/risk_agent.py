"""对质量风险、变更风险和技术影响进行评估的风险 Agent。"""

from __future__ import annotations

from pydantic import BaseModel

from src.agents.extract_agent import ExtractedRequirement
from src.skills.risk_skill import RiskSkill


class RiskAssessment(BaseModel):
    """风险评估标准输出。

    三类风险供人工审核与前端标签展示使用；
    `confidence` 表示当前规则/模型对该判断的把握，不等于业务优先级。
    """

    quality_risk: str = "low"
    change_risk: str = "low"
    technical_impact_risk: str = "low"
    confidence: float = 0.7


class RiskAgent:
    """风险评估入口。

    与其他 Agent 一样，优先走 Skill；模型不可用时回退到本地启发式，
    保证分析图始终能给出完整的风险面板。
    """

    def __init__(self, skill: RiskSkill | None = None) -> None:
        self.skill = skill or RiskSkill()

    def assess(self, extracted: ExtractedRequirement) -> RiskAssessment:
        """评估质量风险、变更风险与技术影响。"""
        if not self.skill.provider.is_configured():
            return self._heuristic_assess(extracted)
        return self.skill.assess(extracted)

    @staticmethod
    def _heuristic_assess(extracted: ExtractedRequirement) -> RiskAssessment:
        """本地兜底规则：按需求完整度、领域和复杂度粗分层。"""
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
        """质量风险更关注需求是否清晰、是否包含足够多的可验证功能点。"""
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
        """变更风险偏向业务改动面与影响范围，而非技术难度本身。"""
        if extracted.business_domain in {"auth", "workflow", "data", "reporting"}:
            return "medium"
        if extracted.priority == "high" and len(extracted.requirements) >= 2:
            return "high"
        return "low"

    def _evaluate_technical(self, extracted: ExtractedRequirement) -> str:
        """技术风险偏向接口、集成、权限与数据链路复杂度。"""
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
        """根据证据完整度生成置信度，避免所有需求都显示同一固定分数。"""
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
