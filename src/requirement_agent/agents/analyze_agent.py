""""对重复、关联、冲突和独立性进行分析的 Agent。"""

from __future__ import annotations

import re

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, Field

from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.config.settings import settings
from requirement_agent.domain.similarity_scale import (
    SimilarityCalibration,
    SimilarityGates,
    VerdictLevel,
    verdict,
)
from requirement_agent.skills.analyze_skill import AnalyzeSkill


# 判定标尺（B4）。**数值出自实跑校准，不是拍的** —— 见
# `scripts/calibrate_similarity.py` 与 `docs/baseline/similarity_calibration_*.json`。
#
# 这里曾经写着一组绝对余弦阈值（重复 0.80 / 关联 0.72 / 候选 0.60），依据是注释里
# 一段**无法复现**的实测。2026-09-17 用 52 对构造语料重测，结论是那组分不开：
#
#   真重复 P05 = 0.8948      无关 P95 = 0.8120      → 这两类**分得开**
#   同能力异对象 P95 = 0.9496 > 真重复 P05 0.8948   → 这两类**完全重叠**
#
# 旧阈值 0.80 的实际表现：真重复 12/12 判中，但「导出 Excel 报表 / 导出员工数据」
# 这类同能力异对象 **10 对全部被误判成重复**。只看向量救不了 —— 它们的余弦就是更高。
#
# 真正能分开的是**落差**（同一 query 内 top1 与其余候选的间距）：真重复 0.110~0.229、
# 无关 0.027~0.131、红区 0.021~0.133。所以判定改成两把锁，见 `domain/similarity_scale.py`。

# 分析模式 → **展示闸门**（`candidate_relevance`）的缩放系数。
#
# ⚠️ **判定闸门绝不随模式缩放。** `broad` 的用途是「宁可多给人看几条」，
# 不是「宁可多判几个重复」。第一版实现缩放了三个 relevance，于是 broad 会在
# relevance 处于中间带时真的多判重复 —— 与这句说明自相矛盾，且方向危险。
# 现在模式只动 `candidate_relevance`；重复/关联的闸门恒定。
# `tests/unit/test_analysis_mode.py` 里有测试钉着这条。
ANALYSIS_MODE_DISPLAY_FACTOR: dict[str, float] = {
    "strict": 1.0,
    "balanced": 0.7,
    "broad": 0.4,
}

# 级别 → 展示标签。**按级别映射，不按相似度分档** —— 两把锁下同一个相似度在不同查询里
# 可能判成不同级别（取决于该批候选的落差），拿相似度反推标签必然与判定打架，
# 那正是 F 批修过的「展示与判定不一致」。
_LEVEL_LABELS: dict[str, str] = {
    "duplicate": "高",
    "related": "中",
    "candidate": "低",
    "none": "低",
    "unverifiable": "—",  # 没有余弦，不给「高/中/低」这种会被当成相似度的标签
}


def calibration() -> SimilarityCalibration:
    """当前生效的相似度标尺。数值来自 settings（出自校准报告）。"""
    return settings.similarity_calibration()


def gates_for(analysis_mode: str | None) -> SimilarityGates:
    """按分析模式取判定闸门；未知/缺省模式回退 strict（保持历史默认行为）。

    模式**只**缩放展示闸门；判定闸门恒定。理由见上面 `ANALYSIS_MODE_DISPLAY_FACTOR`。
    """
    key = (analysis_mode or "strict").strip().lower()
    factor = ANALYSIS_MODE_DISPLAY_FACTOR.get(key, ANALYSIS_MODE_DISPLAY_FACTOR["strict"])
    return settings.similarity_gates().with_candidate_relevance_scaled(factor)


def score_label(level: str) -> str:
    """把判定级别映射为展示用的「高/中/低 / —」。

    未知级别返回 `"—"` 而不是默认「低」：把不认识的东西显示成「低相似度」
    会让它看起来像「评估过了，结论是不像」。
    """
    return _LEVEL_LABELS.get(str(level), "—")


def booleans_from_levels(candidates: Iterable[object]) -> tuple[bool, bool]:
    """由候选级别推出 `(duplicate, related)`。

    **LLM 路径与启发式路径共用这一个表达式** —— 两条路各写一遍是 B4 之前那个烂摊子的
    根源（同一个「相似度阈值」在两条路上指的是两个不同的数）。共用一个函数，
    它们不可能再漂移。

    语义：`duplicate` 级别不蕴含 `related`（一条近重复的候选不会同时被记成「关联」）,
    这样两条路的行为逐字一致。
    """
    levels = [str(getattr(item, "level", "") or "") for item in candidates]
    return ("duplicate" in levels), ("related" in levels)


def cosine_of(candidate: Mapping[str, object]) -> float | None:
    """从检索候选里取**余弦**；纯关键词命中的候选返回 `None`。

    ⚠️ 刻意不退回 `similarity`：那是**融合分**（关键词多因子加和与余弦取大者），
    量纲随查询词数与短语长度变化，拿它比相似度阈值是范畴错误。
    没有余弦就诚实地说「判不了」（`unverifiable`），而不是拿一个不同量纲的数充数。
    """
    raw = candidate.get("vector_similarity")
    if raw is None:
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class CandidateMatch(BaseModel):
    """单个历史候选与当前需求的关系证据。"""

    requirement_key: str
    title: str
    similarity: float
    """**余弦**，由后端填入。

    ⚠️ 它以前装的是「模型自报的相似度」，而那是个幻觉：提示词要求模型返回一个它
    **算不出来**的数，模型只能把检索分数原样回显（`skills/analyze_skill.py` 记着实测
    `0.7313209960078035` 逐位相同）。后果是落库的 `requirement_relation.similarity`
    一直标着「模型判断」，实际是检索分。B4 起一律由后端填真实余弦，模型不再被要求
    返回它；没有余弦的候选（纯关键词命中）填 `0.0` 并把 `similarity_source` 标出来。
    """

    similarity_source: Literal["vector", "keyword_only"] = "vector"
    """余弦的来源。`keyword_only` = 这条只命中了关键词、没有向量分数。"""

    level: VerdictLevel = "candidate"
    """两把锁给出的判定级别。

    **下游一律读它，不要自己拿 `similarity` 去比阈值** —— 级别取决于整批候选的落差，
    是查询级属性，单个候选自己算不出来。
    """

    reason: str
    evidence: list[str] = Field(default_factory=list)


class StreamSuggestion(BaseModel):
    """「该新建需求主线，还是追加到既有主线」的建议。

    **它只是建议。** 按职责边界（方案 §11），主线归属必须人工确认 ——
    错误合并会污染整条版本链，错误拆分会造成重复需求。这里只给模型判断 +
    置信度，供审核页显示，**没有任何自动通道会照着它建版本**。

    ⚠️ 判定**不能只看能力是否相同**：能力相同但业务对象不同，仍是两条主线
    （「员工数据导出」与「订单数据导出」都是「导出 Excel」，但不是同一条）。
    """

    action: Literal["create_new", "append_to"] = "create_new"
    target_requirement_key: str | None = None
    confidence: float = 0.0
    reason: str = ""


class AnalysisResult(BaseModel):
    """重复/关联/冲突分析的标准输出。

    四个布尔量必须互相一致：`independent` 只能在其余判断都未命中时成立。
    `candidates` 是审核与前端展示的证据面板，不是所有检索命中都会进入这里。
    """

    duplicate: bool = False
    related: bool = False
    conflict: bool = False
    independent: bool = True
    reasoning: str = ""
    candidates: list[CandidateMatch] = Field(default_factory=list)
    # 主线归属建议；模型没给或判不准时为 None（**不编一个默认值出来**）
    suggestion: StreamSuggestion | None = None

    # —— 降级标记（B3.1b）——
    # ⚠️ 追加实施文档 §4.5：「**风险和冲突判断降级后必须标记，不得静默当作确定结果**」。
    #
    # 两种降级都算：① 主模型失败、走了备用模型；② 模型整条链路失败、退回启发式规则。
    # 两者的共同点都是「这个结论**不是主模型给的**」—— 审核人据此决定要不要更谨慎，
    # 而不是把它当成一次正常的模型判断。
    degraded: bool = False
    degraded_reason: str | None = None
    """降级原因，中文、可直接展示。未降级时为 None。"""



class AnalyzeAgent:
    """需求关系分析入口。

    Agent 负责把抽取结果与历史候选接起来，并在 LLM 不可用时回退到启发式规则；
    Skill 负责真正的提示词与 JSON 归一化。
    """

    def __init__(self, skill: AnalyzeSkill | None = None) -> None:
        self.skill = skill or AnalyzeSkill()

    def analyze(
        self,
        extracted: ExtractedRequirement,
        historical_requirements: list[dict[str, object]] | None = None,
        *,
        analysis_mode: str = "strict",
    ) -> AnalysisResult:
        """判断当前需求与历史需求是重复、关联、冲突还是独立。

        `analysis_mode`（strict/balanced/broad）控制判定阈值；缺省 strict = 历史默认。
        """
        gates = gates_for(analysis_mode)
        cal = calibration()
        if not self.skill.has_llm():
            return self._heuristic_analyze(
                extracted, historical_requirements, gates=gates, cal=cal
            )
        return self.skill.analyze(
            extracted, historical_requirements, gates=gates, cal=cal
        )

    @staticmethod
    def _heuristic_analyze(
        extracted: ExtractedRequirement,
        historical_requirements: list[dict[str, object]] | None = None,
        *,
        gates: SimilarityGates | None = None,
        cal: SimilarityCalibration | None = None,
    ) -> AnalysisResult:
        """本地证据规则（LLM 不可用时）。

        **判定用两把锁，与 LLM 路径同一套**：每个候选过一个 `verdict()`，级别取决于
        它自己的余弦**和**整批候选的落差。把达到「值得展示」级别的候选放进结果，
        避免「检索命中」被误读成「业务相关」。

        ⚠️ 与旧版的区别：旧版拿 `_score_similarity()` 那个**第三代分数**（标签/领域/
        关键词加和）比阈值，于是同一个「相似度阈值」在启发式路径与 LLM 路径上
        指的是两个不同的数。现在两条路都只认余弦。
        """
        gates = gates or gates_for(None)
        cal = cal or calibration()
        historical = historical_requirements or []

        # 落差是**查询级**属性：先收齐整批余弦，每个候选再用同一个落差值配自己的 relevance。
        cosines = [value for value in (cosine_of(item) for item in historical) if value is not None]

        candidates: list[CandidateMatch] = []
        for item in historical:
            title = str(item.get("requirement_name") or item.get("title") or "")
            summary = str(item.get("final_requirement") or item.get("summary") or "")
            cos = cosine_of(item)
            judged = verdict(cos, cosines, cal, gates)
            if judged.level == "none":
                # 距离落在噪声区间内，连展示都不必 —— 展示它只会让人以为「评估过了」。
                continue
            # `_score_similarity` 的**分数不再参与判定**，只用它生成的证据文本 ——
            # 那是给审核人看的「为什么这条被认为相关」，与判定是两回事。
            _, evidence = AnalyzeAgent()._score_similarity(extracted, title, summary)
            candidates.append(
                CandidateMatch(
                    requirement_key=str(item.get("requirement_key") or "REQ-UNKNOWN"),
                    title=title or "历史需求",
                    similarity=cos if cos is not None else 0.0,
                    similarity_source="vector" if cos is not None else "keyword_only",
                    level=judged.level,
                    reason=judged.reason,
                    evidence=evidence,
                )
            )

        duplicate, related = booleans_from_levels(candidates)
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
            degraded=True,
            degraded_reason="未配置模型，结论来自启发式规则",
        
            duplicate=duplicate,
            related=related,
            conflict=conflict,
            independent=independent,
            reasoning=" ".join(reasoning_parts) or "缺少足够历史相似度信号，按独立需求处理。",
            candidates=candidates,
        )

    def _score_similarity(self, extracted: ExtractedRequirement, title: str, summary: str) -> float:
        """返回相似度与证据列表。

        分数来自标签、领域、共享业务关键词和短语重合四层证据；
        若没有任何证据，则强制衰减，避免弱向量候选在 UI 中看起来“很像”。
        """
        tokens = [token for token in extracted.tags if len(token) >= 2]
        title_text = title.lower()
        summary_text = summary.lower()
        source_text = " ".join([extracted.requirement_title, extracted.summary, extracted.business_domain, " ".join(extracted.tags)]).lower()
        target_text = f"{title} {summary}".lower()

        evidence: list[str] = []
        score = 0.0
        token_hits = [token for token in tokens if token in target_text]
        if token_hits:
            score += min(0.5, 0.12 * len(token_hits))
            evidence.extend(token_hits[:4])
        if extracted.business_domain and extracted.business_domain in target_text:
            score += 0.12
            evidence.append(f"domain:{extracted.business_domain}")
        shared_keywords = [kw for kw in ["登录", "认证", "权限", "审批", "报表", "支付", "导出", "筛选", "查询", "导入", "短信", "验证码"] if kw in source_text and kw in target_text]
        if shared_keywords:
            score += min(0.25, 0.08 * len(shared_keywords))
            evidence.extend(shared_keywords[:4])

        seq_matches = self._ordered_phrase_overlap(extracted.requirement_title, title) + self._ordered_phrase_overlap(extracted.summary, summary)
        if seq_matches:
            score += min(0.2, 0.05 * seq_matches)
            evidence.extend([f"phrase:{seq_matches}"])

        if not token_hits and not shared_keywords and not seq_matches:
            score *= 0.3
        return min(score, 1.0), evidence

    @staticmethod
    def _ordered_phrase_overlap(left: str, right: str) -> int:
        """粗粒度短语重合统计，辅助中文短句场景下的启发式判定。"""
        left_tokens = [token for token in re.split(r"\s+", left) if token]
        right_tokens = [token for token in re.split(r"\s+", right) if token]
        if not left_tokens or not right_tokens:
            return 0
        matches = 0
        for token in left_tokens:
            if token in right_tokens:
                matches += 1
        return matches
