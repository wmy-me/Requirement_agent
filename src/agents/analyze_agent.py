""""对重复、关联、冲突和独立性进行分析的 Agent。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.agents.extract_agent import ExtractedRequirement
from src.skills.analyze_skill import AnalyzeSkill


class CandidateMatch(BaseModel):
    requirement_key: str
    title: str
    similarity: float
    reason: str


class AnalysisResult(BaseModel):
    duplicate: bool = False
    related: bool = False
    conflict: bool = False
    independent: bool = True
    reasoning: str = ""
    candidates: list[CandidateMatch] = Field(default_factory=list)


class AnalyzeAgent:
    """负责对需求与历史项进行关系判断的分类 Agent。"""

    def __init__(self, skill: AnalyzeSkill | None = None) -> None:
        self.skill = skill or AnalyzeSkill()

    def analyze(
        self,
        extracted: ExtractedRequirement,
        historical_requirements: list[dict[str, object]] | None = None,
    ) -> AnalysisResult:
        if not self.skill.provider.is_configured():
            return self._heuristic_analyze(extracted, historical_requirements)
        return self.skill.analyze(extracted, historical_requirements)

    @staticmethod
    def _heuristic_analyze(
        extracted: ExtractedRequirement,
        historical_requirements: list[dict[str, object]] | None = None,
    ) -> AnalysisResult:
        candidates: list[CandidateMatch] = []
        historical = historical_requirements or []

        for item in historical:
            title = str(item.get("requirement_name") or item.get("title") or "")
            summary = str(item.get("final_requirement") or item.get("summary") or "")
            score = AnalyzeAgent()._score_similarity(extracted, title, summary)
            if score >= 0.55:
                reason = "业务语义相近，存在重合功能面"
                if score >= 0.8:
                    reason = "高度相似，可能为重复需求"
                candidates.append(
                    CandidateMatch(
                        requirement_key=str(item.get("requirement_key") or "REQ-UNKNOWN"),
                        title=title or "历史需求",
                        similarity=round(score, 2),
                        reason=reason,
                    )
                )

        duplicate = any(candidate.similarity >= 0.8 for candidate in candidates)
        related = any(candidate.similarity >= 0.6 for candidate in candidates)
        conflict = "权限" in " ".join(extracted.tags) and any("权限" in str(item.get("requirement_name") or "") for item in historical)
        independent = not duplicate and not related and not conflict

        reasoning_parts = []
        if duplicate:
            reasoning_parts.append("存在高度相似需求候选，建议合并或复用现有版本。")
        if related:
            reasoning_parts.append("需求与历史项存在关联，需确认依赖关系。")
        if conflict:
            reasoning_parts.append("权限或状态控制逻辑存在冲突风险。")
        if independent:
            reasoning_parts.append("当前需求在现有历史需求中未发现明显冲突，建议作为独立需求处理。")

        return AnalysisResult(
            duplicate=duplicate,
            related=related,
            conflict=conflict,
            independent=independent,
            reasoning=" ".join(reasoning_parts) or "缺少足够历史相似度信号，按独立需求处理。",
            candidates=candidates,
        )

    def _score_similarity(self, extracted: ExtractedRequirement, title: str, summary: str) -> float:
        haystack = " ".join([extracted.requirement_title, extracted.summary, " ".join(extracted.tags), title, summary])
        score = 0.0
        for token in extracted.tags:
            if token in haystack:
                score += 0.2
        if extracted.business_domain in (title + summary).lower():
            score += 0.2
        if any(keyword in haystack for keyword in ["登录", "认证", "权限", "审批", "报表", "支付"]):
            score += 0.15
        return min(score, 1.0)
