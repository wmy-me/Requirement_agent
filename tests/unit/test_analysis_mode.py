"""`analysis_mode` 的接线测试。

**这个参数的实际含义在 B4 收窄了**：它现在只控制**展示宽严**（「多给人看几条」），
不再影响重复/关联的判定。此前它缩放的是那三个绝对阈值 —— 而绝对阈值本身被校准
证明不可用（见 `src/requirement_agent/domain/similarity_scale.py`）。

新口径下三条不变量：

1. 缺省与未知模式一律回退 `strict`；
2. 越宽的模式展示闸门越低（能看到更多候选）；
3. **判定闸门与模式无关** —— `broad` 不多判任何一条重复。
"""

import pytest

from requirement_agent.agents.analyze_agent import AnalyzeAgent, gates_for, score_label
from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.config.settings import settings
from requirement_agent.skills.analyze_skill import AnalyzeSkill


class UnconfiguredProvider:
    """未配置 LLM 的 provider，强制走启发式分支，使结论只由闸门决定。"""

    def is_configured(self) -> bool:
        return False


def _extracted() -> ExtractedRequirement:
    return ExtractedRequirement(
        requirement_title="登录增强",
        summary="支持短信验证码登录",
        business_domain="auth",
        tags=["登录"],
        requirements=["短信验证码登录"],
        raw_text="支持短信验证码登录。",
    )


def _history(*cosines: float | None) -> list[dict[str, object]]:
    """造一批带真实余弦的检索候选（`vector_similarity` 是判定唯一认的字段）。"""
    return [
        {
            "requirement_key": f"REQ-{index:06d}",
            "requirement_name": f"历史需求{index}",
            "similarity": cosine,  # 融合分，判定不该读它
            "vector_similarity": cosine,
        }
        for index, cosine in enumerate(cosines, start=1)
    ]


def _analyze(history: list[dict[str, object]], mode: str):
    return AnalyzeAgent(skill=AnalyzeSkill(provider=UnconfiguredProvider())).analyze(
        _extracted(), history, analysis_mode=mode
    )


# ── 闸门取值 ──────────────────────────────────────────────────────────────


def test_defaults_come_from_the_calibration_not_from_hardcoded_numbers() -> None:
    """默认闸门来自 settings（出自校准报告），而不是代码里拍的一组数。"""
    gates = gates_for(None)
    assert gates.duplicate_contrast == settings.similarity_contrast_duplicate
    assert gates.related_contrast == settings.similarity_contrast_related
    assert gates_for("strict") == gates
    assert gates_for("激进") == gates, "未知模式必须回退 strict"


def test_relevance_floor_is_derived_from_the_noise_ceiling() -> None:
    """三个 relevance 闸门都由噪声上界换算 —— 实测 relevance 分不开级别，
    给它三个数只是假装。级别区分全部交给 contrast。"""
    gates = gates_for(None)
    assert gates.duplicate_relevance == gates.related_relevance == gates.candidate_relevance
    expected = (settings.similarity_noise_ceiling - settings.similarity_baseline) / (
        1 - settings.similarity_baseline
    )
    assert gates.candidate_relevance == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["balanced", "broad"])
def test_modes_widen_the_display_gate_only(mode: str) -> None:
    """宽模式只放宽「展示」这一道门。"""
    base, wider = gates_for("strict"), gates_for(mode)
    assert wider.candidate_relevance < base.candidate_relevance


@pytest.mark.parametrize("mode", ["strict", "balanced", "broad", "激进"])
def test_modes_never_touch_the_judgement_gates(mode: str) -> None:
    """**核心不变量：模式改变不了判定。**

    两把锁的四道判定闸门（重复/关联 各自的 relevance 与 contrast）在哪个模式下
    都必须一模一样。若哪天有人用模式去调它们，这条会红。
    """
    base, current = gates_for("strict"), gates_for(mode)
    assert current.duplicate_relevance == base.duplicate_relevance
    assert current.duplicate_contrast == base.duplicate_contrast
    assert current.related_relevance == base.related_relevance
    assert current.related_contrast == base.related_contrast


# ── 模式的实际效果 ────────────────────────────────────────────────────────


def test_broad_shows_a_candidate_that_strict_filters_out() -> None:
    """0.78 在 strict 下低于展示闸门（被丢掉），在 broad 下能露出来。

    这一条说明模式**确实有用** —— 它控制「宁可多给人看几条」。
    """
    history = _history(0.78)
    assert _analyze(history, "strict").candidates == []
    broad = _analyze(history, "broad")
    assert [c.level for c in broad.candidates] == ["candidate"]
    assert broad.independent is True, "露出来不等于判它重复"


def test_broad_does_not_judge_more_duplicates_than_strict() -> None:
    """**把「模式不改变判定」钉成行为断言，而不只是比闸门数值。**

    构造一条「relevance 落在两档展示闸门之间、但 contrast 很高」的候选：
    若实现按系数缩放了 duplicate_relevance，broad 下它就会变成重复 —— 那就是
    用户调宽模式却多挨了几个重复判定。正确行为是两档结论一致。
    """
    # 0.80 的 relevance ≈ 0.267，落在 broad 展示闸门(0.124) 与 strict(0.311) 之间；
    # 其余候选都在 0.66，落差 0.14 ≥ duplicate_contrast，让 contrast 这把锁是开的。
    history = _history(0.80, 0.66, 0.66, 0.66)
    strict = _analyze(history, "strict")
    broad = _analyze(history, "broad")
    assert strict.duplicate is False
    assert broad.duplicate is False, "调宽展示模式不该多判重复"
    assert all(c.level != "duplicate" for c in broad.candidates)


def test_both_modes_agree_on_a_clear_duplicate() -> None:
    """真重复在两档下都要判重复 —— 模式不该把该判的也丢掉。"""
    history = _history(0.95, 0.70, 0.70, 0.70)
    for mode in ("strict", "broad"):
        result = _analyze(history, mode)
        assert result.duplicate is True, mode
        assert result.independent is False


def test_candidate_without_cosine_is_unverifiable_and_never_drives_a_verdict() -> None:
    """纯关键词命中的候选没有余弦 —— 不能拿融合分顶替，只能标「判不了」。

    这是防「拿一个量纲不同的数比相似度阈值」的护栏。
    """
    history = _history(None, None)
    result = _analyze(history, "strict")
    assert {c.level for c in result.candidates} == {"unverifiable"}
    assert result.duplicate is False and result.related is False
    assert {c.similarity_source for c in result.candidates} == {"keyword_only"}


def test_cosine_is_filled_by_the_backend_not_by_a_model() -> None:
    """候选上的 `similarity` 现在是**余弦**，不是模型自报的相似度。"""
    result = _analyze(_history(0.9123, 0.70, 0.70, 0.70), "strict")
    top = result.candidates[0]
    assert top.similarity == pytest.approx(0.9123)
    assert top.similarity_source == "vector"


# ── 展示标签 ──────────────────────────────────────────────────────────────


def test_score_label_maps_levels_not_similarities() -> None:
    assert score_label("duplicate") == "高"
    assert score_label("related") == "中"
    assert score_label("candidate") == "低"
    assert score_label("unverifiable") == "—"


def test_unknown_level_does_not_render_as_low_similarity() -> None:
    """不认识的级别要显示「—」。显示成「低」会让它看起来像
    「评估过了，结论是不像」—— 那是另一种撒谎。"""
    assert score_label("") == "—"
    assert score_label("wat") == "—"
