"""用于需求分析的 LLM 技能。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from requirement_agent.skills.base_skill import BaseSkill
from requirement_agent.skills.prompts import ANALYZE_SYSTEM_PROMPT, build_analyze_user_prompt

if TYPE_CHECKING:
    from requirement_agent.agents.analyze_agent import AnalysisResult, CandidateMatch
    from requirement_agent.agents.extract_agent import ExtractedRequirement
    from requirement_agent.domain.similarity_scale import SimilarityCalibration, SimilarityGates

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
        gates: SimilarityGates | None = None,
        cal: SimilarityCalibration | None = None,
    ) -> AnalysisResult:
        """分析当前需求与历史需求的关系，并尽量返回一致的布尔结论。

        `gates` / `cal` 由 AnalyzeAgent 按 analysis_mode 下发（校准依据见
        `agents/analyze_agent.py` 顶部与 `domain/similarity_scale.py`）。
        """
        from requirement_agent.agents.analyze_agent import (
            AnalyzeAgent,
            AnalysisResult,
            CandidateMatch,
            booleans_from_levels,
            calibration,
            cosine_of,
            gates_for,
        )
        from requirement_agent.domain.similarity_scale import verdict

        gates = gates or gates_for(None)
        cal = cal or calibration()

        fallback = AnalyzeAgent._heuristic_analyze(
            extracted, historical_requirements, gates=gates, cal=cal
        )
        if not self.provider.is_configured():
            return fallback

        system_prompt = ANALYZE_SYSTEM_PROMPT
        history = historical_requirements or []
        prompt = build_analyze_user_prompt(
            extracted_json=extracted.model_dump(mode="json"), history=history
        )

        try:
            payload = self._generate_json(prompt, system_prompt)

            # 模型给的是「哪几条相关 + 为什么」；**相似度不采信模型**，由后端从检索结果里
            # 取真实余弦填入。提示词里已经不再索要 `similarity` —— 那是在要求模型返回一个
            # 它算不出来的数，实测它只能把检索分数原样回显（0.7313209960078035 逐位相同）。
            #
            # 副作用值得记一笔：**模型再也无法伪造相似度**。它若编一个不存在的
            # requirement_key，后端查不到余弦，那条候选就是 `unverifiable`，
            # 不会因为模型报了个 0.99 就升级成重复。
            history = list(historical_requirements or [])
            by_key = {str(item.get("requirement_key") or ""): item for item in history}
            cosines = [value for value in (cosine_of(item) for item in history) if value is not None]

            normalized_candidates: list[CandidateMatch] = []
            for item in payload.get("candidates") or []:
                key = str(item.get("requirement_key") or "REQ-UNKNOWN")
                source = by_key.get(key) or {}
                cos = cosine_of(source)
                judged = verdict(cos, cosines, cal, gates)
                normalized_candidates.append(
                    CandidateMatch(
                        requirement_key=key,
                        title=str(
                            item.get("title")
                            or source.get("requirement_name")
                            or "历史需求"
                        ),
                        similarity=cos if cos is not None else 0.0,
                        similarity_source="vector" if cos is not None else "keyword_only",
                        level=judged.level,
                        reason=str(item.get("reason") or judged.reason),
                        evidence=[str(v) for v in item.get("evidence") or []],
                    )
                )

            # ── 布尔量：**有候选证据时，级别说了算** ──
            #
            # `duplicate` / `related` 一律由候选级别推出，**不再采信模型对这两个字段的
            # 自报值**。两条规则合起来就是这个意思：
            #
            #   降级（模型说有、证据没有）—— 模型爱在只有 0.75 的候选上判 duplicate，
            #       而那条候选的级别可能只是 `candidate`。这个方向一直都在做。
            #   升级（证据有、模型说没有）—— **B4 才敢做这个方向。** 此前不升级的理由是
            #       「`similarity` 只是模型回显检索分，补判等价于『向量分高就直接下判』」；
            #       而级别现在是后端用真实余弦 + 落差算出来的，**那个理由不再成立**。
            #       决策依据：`duplicate=true` 在本系统里**不做任何自动动作**，
            #       只把 `next_action` 置为 `manual_review`，最终仍由人裁决 ——
            #       所以「够级别就标记出来给人看」比「藏起来」更符合审核人的利益。
            #       实跑走查里真的发生过：候选 `level=duplicate` 而结论是「独立」，
            #       审核人看到的是两张对不上的卡片。
            #
            # 模型仍然负责它擅长的那半件事：**选哪几条、为什么**（candidates 与 reason）。
            # 与启发式路径共用 `booleans_from_levels`，两条路不可能再漂移。
            duplicate, related = booleans_from_levels(normalized_candidates)
            conflict = bool(payload.get("conflict"))
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

