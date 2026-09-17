"""相似度标尺的纯函数测试。

**这里不连库、不调 API、不读配置** —— 标尺是纯函数，它的正确性应当只由入参决定。
真实 embedding 的行为由 `scripts/calibrate_similarity.py` 负责测（那是一次性的、
要花钱的、不挂在 CI 上的），两者分工不同：这里守的是**算术**，那里守的是**模型**。
"""

from __future__ import annotations

import pytest

from requirement_agent.domain.similarity_scale import (
    DEFAULT_GATES,
    SimilarityCalibration,
    SimilarityGates,
    contrast,
    relevance,
    verdict,
)

# 实测口径（2026-09-17，Doubao-embedding）：无关文本对余弦中位 0.7193、最高 0.8086。
CAL = SimilarityCalibration(
    baseline=0.72,
    noise_ceiling=0.79,
    model="Doubao-embedding",
    measured_at="2026-09-17T00:00:00+00:00",
    corpus_sha256="test",
    separability=-0.03,
)


# ── relevance ─────────────────────────────────────────────────────────────


def test_relevance_is_monotone_in_cosine() -> None:
    """单调是它最基本的性质 —— 否则「排序用未截断值」这个约定就没有意义。"""
    values = [0.60, 0.70, 0.72, 0.75, 0.80, 0.90, 1.0]
    relevances = [relevance(value, CAL) for value in values]
    assert relevances == sorted(relevances)


def test_relevance_anchors_baseline_at_zero_and_identity_at_one() -> None:
    assert relevance(CAL.baseline, CAL) == pytest.approx(0.0)
    assert relevance(1.0, CAL) == pytest.approx(1.0)


def test_relevance_is_not_truncated_at_zero() -> None:
    """**低于基线必须给出负数。**

    截断到 0 会把噪声地板以下的整段候选压成同一个值，排序在那里退化成任意 ——
    而「哪条更近一点」在灰区里恰恰是审核人唯一能用的线索。
    """
    below = relevance(0.60, CAL)
    assert below < 0, f"低于基线应返回负数，实际 {below}"
    assert below < relevance(0.70, CAL) < 0


def test_relevance_does_not_explode_when_baseline_is_degenerate() -> None:
    """baseline 被配成 1.0 时不做除零，退回原始余弦 —— 一个坏配置不该让分析链挂掉。"""
    degenerate = SimilarityCalibration(
        baseline=1.0, noise_ceiling=1.0, model="x", measured_at="", corpus_sha256="", separability=0.0
    )
    assert relevance(0.9, degenerate) == pytest.approx(0.9)


def test_relevance_agrees_with_calibration_script() -> None:
    """校准脚本里**重算了一份** relevance（要能对着历史报告的 baseline 复算，
    不能被当前 settings 影响）。同一口径写两处有代价，所以在这里钉住两者一致。
    """
    from scripts.calibrate_similarity import relevance_of

    for cos in (0.60, 0.72, 0.8086, 0.95, 1.0):
        assert relevance(cos, CAL) == pytest.approx(relevance_of(cos, CAL.baseline))


# ── contrast ──────────────────────────────────────────────────────────────


def test_contrast_returns_none_for_single_candidate() -> None:
    """**单条候选没有「其余」可言。**

    这里必须返回 None 而不是 0 —— 0 会被下游误读成「算过了，没有落差」，
    于是单候选场景会被当成「有落差但不突出」，与事实相反。
    """
    assert contrast([0.8]) is None
    assert contrast([]) is None


def test_contrast_uses_median_and_high_confidence_from_four_up() -> None:
    result = contrast([0.90, 0.70, 0.72, 0.74])
    assert result is not None
    assert result.method == "median"
    assert result.confidence == "high"
    assert result.n == 4
    assert result.value == pytest.approx(0.90 - 0.72)


def test_contrast_uses_mean_and_low_confidence_below_four() -> None:
    """n 在 2~3 时只能拿 1~2 个数算，标 low 是提醒调用方别当真。"""
    for scores in ([0.90, 0.80], [0.90, 0.80, 0.70]):
        result = contrast(scores)
        assert result is not None
        assert result.method == "mean"
        assert result.confidence == "low"
    assert contrast([0.90, 0.80]).value == pytest.approx(0.10)


def test_contrast_is_robust_to_two_close_competitors() -> None:
    """这正是选「中位数」而不是「top1 − top2」的原因：两条都相关时，
    top1−top2 会被单个接近的竞争者打崩（实测 P8 两组只差 0.0042），
    而 top1−median(其余) 仍然稳定。
    """
    result = contrast([0.90, 0.899, 0.70, 0.68, 0.66])
    assert result is not None
    assert result.value > 0.19  # 0.90 − 0.70，未被那个 0.899 拉崩


# ── verdict：两把锁 ────────────────────────────────────────────────────────


def _cosines(top1: float, delta: float) -> list[float]:
    """造一批候选，使其落差恰好等于 `delta`（其余 4 条都取 top1 − delta）。"""
    return [top1] + [top1 - delta] * 4


def test_verdict_without_cosine_is_unverifiable() -> None:
    """只命中关键词、没进向量召回的候选，没有余弦可比 ——
    拿「命中几个 token 加权的和」去比相似度阈值是范畴错误。"""
    result = verdict(None, [0.9, 0.8, 0.7], CAL)
    assert result.level == "unverifiable"
    assert result.contrast is None


def test_verdict_marks_single_candidate_without_contrast() -> None:
    """只有一条候选时，距离可以说，突出度不能说。"""
    result = verdict(0.95, [0.95], CAL)
    assert result.contrast is None
    assert result.level == "candidate"
    assert "人工" in result.reason


def test_verdict_levels_do_not_collapse_under_default_gates() -> None:
    """默认闸门的级别必须有序：越高越严。倒挂会让相关比重复还难判。"""
    gates = DEFAULT_GATES
    assert gates.duplicate_relevance >= gates.related_relevance >= gates.candidate_relevance
    assert gates.duplicate_contrast > gates.related_contrast
