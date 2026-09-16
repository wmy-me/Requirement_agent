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
        duplicate_threshold: float = 0.80,
        related_threshold: float = 0.72,
    ) -> AnalysisResult:
        """分析当前需求与历史需求的关系，并尽量返回一致的布尔结论。

        `duplicate_threshold` / `related_threshold` 由 AnalyzeAgent 按 analysis_mode 下发；
        这里的默认值与 strict 模式保持一致（校准依据见 `analyze_agent.ANALYSIS_MODE_THRESHOLDS`）。
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
            # duplicate=true 但没有达到重复阈值的候选佐证 → 降为 false（防假阳性）
            if duplicate and max_similarity < duplicate_threshold:
                duplicate = False
                related = related or max_similarity >= related_threshold
            # 这里**刻意没有**任何「达到阈值就补判」的分支（duplicate 与 related 都没有）。
            # 那类规则的前提是 similarity 为模型的独立判断；实测它只是把检索分数原样回显
            # （0.7313209960078035 逐位相同），于是规则等价于「向量分数高就直接下判」，
            # 会推翻模型结论并产出「独立」配「关联=是」这类矛盾卡片。
            #
            # 实测教训：本 embedding 模型在**语义无关**的中文业务文本上也能给到 0.79，
            # 所以绝对阈值本身就不可靠，拿它覆盖模型判断更是雪上加霜。
            # 漏报的代价远小于自相矛盾：宁可为空，不可打架。
            independent = not (duplicate or related or conflict)

            result = AnalysisResult(
                duplicate=duplicate,
                related=related,
                conflict=conflict,
                independent=independent,
                reasoning=str(payload.get("reasoning") or fallback.reasoning),
                candidates=normalized_candidates,
                suggestion=_coerce_suggestion(payload.get("suggestion")),
            )
            return result
        except Exception as exc:
            # 不做“半模型半规则”混用，失败时整体回退，保证结果语义稳定。
            logger.warning("event=skill_fallback skill=analyze error=%s", exc)
            return fallback


def _coerce_suggestion(value: object) -> dict[str, object] | None:
    """把模型给的主线判定建议归一；给不出有效建议时返回 None。

    **不编默认值**：报告里写一条没依据的「建议追加」比不写更糟 —— 审核人会当成
    模型判断过。判不准就让模型压低 confidence，或者干脆不给。

    防呆：`append_to` 却不给 `target_requirement_key` 是无意义的（追加到哪条？），
    这种情况降级为 `create_new` 并清掉目标 —— 而不是带着一个空的追加目标往下传。
    """
    if not isinstance(value, dict):
        return None
    action = str(value.get("action") or "").strip()
    if action not in {"create_new", "append_to"}:
        return None
    target = str(value.get("target_requirement_key") or "").strip() or None
    if action == "append_to" and not target:
        action, target = "create_new", None
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "action": action,
        "target_requirement_key": target,
        "confidence": confidence,
        "reason": str(value.get("reason") or "").strip()[:500],
    }

