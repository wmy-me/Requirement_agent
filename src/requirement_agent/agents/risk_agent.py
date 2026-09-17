"""对质量风险、变更风险和技术影响进行评估的风险 Agent。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.skills.risk_skill import RiskSkill


class RiskAssessment(BaseModel):
    """风险评估标准输出。

    三类风险供人工审核与前端标签展示使用；
    `confidence` 表示当前规则/模型对该判断的把握，不等于业务优先级。

    `source` 标明这组结论**来自模型还是规则**。这个字段是必需的：启发式兜底也会给出
    confidence（基底 0.55 起、按条件加分），而前端此前无条件渲染成「**模型**置信度」，
    等于替规则结论宣称了不存在的来源。
    """

    quality_risk: str = "low"
    change_risk: str = "low"
    technical_impact_risk: str = "low"
    confidence: float = 0.7
    source: Literal["llm", "heuristic"] = "heuristic"

    # —— 降级标记（B3.1b）——
    # ⚠️ 追加实施文档 §4.5：「**风险和冲突判断降级后必须标记，不得静默当作确定结果**」。
    #
    # 两种降级都算：① 主模型失败、走了备用模型；② 模型整条链路失败、退回启发式规则。
    # 两者的共同点都是「这个结论**不是主模型给的**」—— 审核人据此决定要不要更谨慎，
    # 而不是把它当成一次正常的模型判断。
    degraded: bool = False
    degraded_reason: str | None = None
    """降级原因，中文、可直接展示。未降级时为 None。"""



class RiskAgent:
    """风险评估入口。

    与其他 Agent 一样，优先走 Skill；模型不可用时回退到本地启发式，
    保证分析图始终能给出完整的风险面板。
    """

    def __init__(self, skill: RiskSkill | None = None) -> None:
        self.skill = skill or RiskSkill()

    def assess(self, extracted: ExtractedRequirement) -> RiskAssessment:
        """评估质量风险、变更风险与技术影响。"""
        if not self.skill.has_llm():
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
            degraded=True,
            degraded_reason="未配置模型，结论来自启发式规则",
        
            quality_risk=quality_risk,
            change_risk=change_risk,
            technical_impact_risk=technical_impact_risk,
            confidence=confidence,
            source="heuristic",
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
