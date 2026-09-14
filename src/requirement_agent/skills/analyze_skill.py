"""用于需求分析的 LLM 技能。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from requirement_agent.skills.base_skill import BaseSkill
from requirement_agent.skills.prompts import ANALYZE_SYSTEM_PROMPT, build_analyze_user_prompt

if TYPE_CHECKING:
    from requirement_agent.agents.analyze_agent import AnalysisResult, CandidateMatch
    from requirement_agent.agents.extract_agent import ExtractedRequirement

logger = logging.getLogger(__name__)


class AnalyzeSkill(BaseSkill):
    """LLM 关系分析技能。

    它接收抽取结果和检索候选，返回结构化关系判断；
    同时会用本地证据阈值对模型输出做二次校验，压制假阳性。
    """

    def analyze(
        self,
        extracted: ExtractedRequirement,
        historical_requirements: list[dict[str, object]] | None = None,
        *,
        duplicate_threshold: float = 0.7,
        related_threshold: float = 0.45,
    ) -> AnalysisResult:
        """分析当前需求与历史需求的关系，并尽量返回一致的布尔结论。

        `duplicate_threshold` / `related_threshold` 由 AnalyzeAgent 按 analysis_mode 下发。
        """
        from requirement_agent.agents.analyze_agent import AnalyzeAgent, AnalysisResult, CandidateMatch

        fallback = AnalyzeAgent._heuristic_analyze(
            extracted,
            historical_requirements,
            duplicate_threshold=duplicate_threshold,
            related_threshold=related_threshold,
        )
        if not self.provider.is_configured():
            return fallback

        system_prompt = ANALYZE_SYSTEM_PROMPT
        history = historical_requirements or []
        prompt = build_analyze_user_prompt(
            extracted_json=extracted.model_dump(mode="json"), history=history
        )

        try:
            from requirement_agent.agents.analyze_agent import AnalysisResult, CandidateMatch

            payload = self._generate_json(prompt, system_prompt)

            # 统一 similarity 到 [0,1]，避免模型返回百分数或脏值污染后续判定。
            normalized_candidates: list[CandidateMatch] = []
            max_similarity = 0.0
            for item in payload.get("candidates") or []:
                try:
                    similarity = float(item.get("similarity") or 0.0)
                except (TypeError, ValueError):
                    similarity = 0.0
                similarity = max(0.0, min(1.0, similarity))
                max_similarity = max(max_similarity, similarity)
                normalized_candidates.append(
                    CandidateMatch(
                        requirement_key=str(item.get("requirement_key") or "REQ-UNKNOWN"),
                        title=str(item.get("title") or "历史需求"),
                        similarity=similarity,
                        reason=str(item.get("reason") or "相似"),
                        evidence=[str(v) for v in item.get("evidence") or []],
                    )
                )

            # 用候选证据反向约束布尔结论，避免 duplicate=true 但无有效证据的幻觉。
            duplicate = bool(payload.get("duplicate"))
            related = bool(payload.get("related"))
            conflict = bool(payload.get("conflict"))
            # duplicate=true 但没有任何 ≥0.7 的相似候选 → 降为 false（防假阳性）
            if duplicate and max_similarity < duplicate_threshold:
                duplicate = False
                related = related or max_similarity >= related_threshold
            # 达到重复阈值但 LLM 漏报 → 按证据补上
            if not duplicate and max_similarity >= duplicate_threshold:
                duplicate = True
                related = True
            if not related and max_similarity >= related_threshold:
                related = True
            independent = not (duplicate or related or conflict)

            result = AnalysisResult(
                duplicate=duplicate,
                related=related,
                conflict=conflict,
                independent=independent,
                reasoning=str(payload.get("reasoning") or fallback.reasoning),
                candidates=normalized_candidates,
            )
            return result
        except Exception as exc:
            # 不做“半模型半规则”混用，失败时整体回退，保证结果语义稳定。
            logger.warning("event=skill_fallback skill=analyze error=%s", exc)
            return fallback
