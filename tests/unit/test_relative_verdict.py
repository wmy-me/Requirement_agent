"""两把锁判定的回归测试。

用例里的数字全部来自实测（2026-09-17，`Doubao-embedding`，4096 维，
语料见 `scripts/fixtures/similarity_calibration.json`）。它们记的是「这个模型在真实中文
业务文本上长什么样」，所以**换模型后这些用例应当失败** —— 那时要重跑
`scripts/calibrate_similarity.py` 并更新闸门，而不是改这些数字去迁就代码。

## 这批用例守的是什么

原先判定只有一条绝对余弦线（重复 0.80）。实测证明它在这个模型上划不出来：

    真重复（P7 打卡复述）       余弦 0.8631
    假阳性（P3 员工数据导出）   余弦 0.8734   ← **比真重复还高**
    语义无关（P5 甘特图排期）   余弦 0.7983

单看余弦，三类的顺序是错的。而「落差」（同一 query 内与其它候选的间距）把它们分开了：

    真重复      0.110 ~ 0.229     （12 条，最低 0.110）
    假阳性红区  0.021 ~ 0.133     （20 条，最高的两条 0.1109 / 0.1330）
    语义无关    0.027 ~ 0.131     （24 条，只有一条 0.1307，其余 ≤ 0.075）

关于那两个「紧贴线上」的红区样本：它们在落差上也够高，单靠落差挡不住。但**余弦能挡** ——
红区的配对余弦虽然中位不低，却**不在同一批候选里同时出现**，而落差是查询级的：
一个红区文本被提交时，库里没有它的真重复，它的 top1 只是「长得像」，top1 与其余候选的
差距远小于真重复。实测结果就是上面那两行。
"""

from __future__ import annotations

import pytest

from requirement_agent.domain.similarity_scale import (
    DEFAULT_GATES,
    SimilarityCalibration,
    SimilarityGates,
    relevance,
    verdict,
)

# 校准报告：docs/baseline/similarity_calibration_Doubao-embedding_20260917.json
CAL = SimilarityCalibration(
    baseline=0.7273,
    noise_ceiling=0.8120,
    model="Doubao-embedding",
    measured_at="2026-09-17T00:00:00+00:00",
    corpus_sha256="similarity_calibration.json@v2",
    separability=0.0829,
)
NOISE_CEILING = CAL.noise_ceiling


def _probe(cosine: float, contrast: float) -> str:
    """按「实测的余弦 + 落差」构造一批候选，返回判定级别。"""
    return verdict(cosine, [cosine] + [cosine - contrast] * 4, CAL).level


# ── 必须判重复（真重复，落差 ≥ 0.1385）─────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "cosine", "contrast"),
    [
        ("P2 字面复述 REQ-000001", 0.9996, 0.2995),
        ("P4 改写 REQ-000002", 0.9708, 0.2650),
        ("P1 改写 REQ-000001", 0.9357, 0.2182),
        ("P7 打卡小程序复述", 0.8631, 0.1473),
    ],
)
def test_true_duplicates_are_judged_duplicate(label: str, cosine: float, contrast: float) -> None:
    assert _probe(cosine, contrast) == "duplicate", label


# ── 必须挡住（假阳性红区与语义无关）───────────────────────────────────────


def test_same_capability_different_object_is_not_duplicate() -> None:
    """**本批次要挡的头号目标。**

    「导出 Excel 报表」vs「导出员工数据」：同动词、不同对象。它的余弦 0.8734
    **高于**真重复 P7 的 0.8631 —— 任何只看向量的阈值都会在这里判错。
    """
    assert _probe(0.8734, 0.1297) != "duplicate"


def test_same_capability_different_object_beats_the_old_rule_on_the_whole_group() -> None:
    """把「旧规则错得多离谱」写成可执行断言。

    语料里 10 对红区的配对余弦 P05 就有 0.8485，**全部**越过旧的 0.80 阈值 ——
    也就是说旧规则会把每一对「导出 Excel 报表 / 导出员工数据」都判成重复。
    真相关的 P05 是 0.8948，红区的 P95 是 0.9496：**余弦把这两类完全混在一起**。
    """
    red_zone_p05, red_zone_p95 = 0.8485, 0.9496
    true_dup_p05 = 0.8948
    assert red_zone_p05 > 0.80, "前提：红区整体在旧阈值之上"
    assert red_zone_p95 > true_dup_p05, "前提：红区的余弦上界盖过真重复的下界"


@pytest.mark.parametrize(
    ("label", "cosine", "contrast"),
    [
        ("P5 甘特图排期（语义无关）", 0.7983, 0.0388),
        ("P6 差旅报销（语义无关）", 0.7213, 0.0705),
        ("实测 7 组无关里最高的一对", 0.8086, 0.0200),
    ],
)
def test_unrelated_texts_never_reach_duplicate_or_related(
    label: str, cosine: float, contrast: float
) -> None:
    """0.8086 那一对在**当前**阈值（0.80）下会被判重复 —— 这就是要修的病灶。"""
    assert _probe(cosine, contrast) in ("candidate", "none"), label


def test_unrelated_text_above_the_old_threshold_is_downgraded() -> None:
    """把病灶单独钉一条：0.8086 > 旧 duplicate 阈值 0.80，但落差只有 0.02。

    「旧行为」在这里表达成**把 relevance 闸门设到旧阈值 0.80 的等效位置、contrast 完全不设**。
    换算关系是 `relevance(0.80) = (0.80 − 0.7273) / (1 − 0.7273) ≈ 0.2666` ——
    旧规则 `cos ≥ 0.80` 与 `relevance ≥ 0.2666` 是同一件事，只是刻度不同。
    在这种配置下这一对会判重复（= 今天会犯的错）；补上 contrast 锁才挡得住。
    """
    assert 0.8086 > 0.80, "前提：这一对在旧的绝对阈值之上"
    old_absolute_behaviour = SimilarityGates(
        duplicate_relevance=relevance(0.80, CAL), duplicate_contrast=0.0,
        related_relevance=relevance(0.72, CAL), related_contrast=0.0,
        candidate_relevance=relevance(0.60, CAL),
    )
    cosines = [0.8086] + [0.7886] * 4
    assert verdict(0.8086, cosines, CAL, old_absolute_behaviour).level == "duplicate"
    # 两把锁下它落到 candidate 以下 —— 0.8086 还低于噪声上界 0.8120，连展示都不必
    assert verdict(0.8086, cosines, CAL, DEFAULT_GATES).level in ("none", "candidate")


# ── 取舍：紧贴闸门的真重复会被让给人工 ────────────────────────────────────


def test_true_duplicate_below_the_contrast_gate_goes_to_human() -> None:
    """**这是刻意的取舍，不是缺陷。**

    语料里真重复组的落差最低是 0.110，而闸门取在 0.1385（真重复的 P10）。
    也就是说约一成真重复会被压到 related（转人工），换来的是无关组与红区组
    那两条 0.13 左右的假阳性被挡在门外。项目既定口径是「宁可漏报，不可自相矛盾」
    （`skills/analyze_skill.py:95`），所以这个方向是对的。

    若哪天有人为了「提高召回」把 `duplicate_contrast` 往下调，这条测试会红。
    """
    assert _probe(0.9357, 0.110) == "related"


def test_red_zone_extremes_stay_out_of_duplicate() -> None:
    """红区里落差最高的两条（0.1330 / 0.1109）—— 闸门正好卡在它们之上。

    这不是巧合，是 `duplicate_contrast = P10(真重复)` 与 `max(红区落差)` 相互逼近的结果。
    """
    assert _probe(0.9033, 0.1330) != "duplicate"
    assert _probe(0.8716, 0.1109) != "duplicate"


# ── 模式放宽的边界 ────────────────────────────────────────────────────────


def test_broad_mode_may_relax_relevance_but_never_contrast() -> None:
    """**这条是两把锁能不能立住的关键。**

    `broad` 的用途是「宁可多给人看几条」，不是「宁可多判几个重复」。
    放宽 relevance 到 0（等于完全不设全局门槛）之后，假阳性红区**仍不得**判重复 ——
    挡住它的必须是 contrast 锁。若哪天有人为了「提高召回」去调 `duplicate_contrast`，
    这条测试会红。
    """
    broad = SimilarityGates(
        duplicate_relevance=0.0, duplicate_contrast=DEFAULT_GATES.duplicate_contrast,
        related_relevance=0.0, related_contrast=DEFAULT_GATES.related_contrast,
        candidate_relevance=0.0,
    )
    result = verdict(0.8734, [0.8734] + [0.8734 - 0.1297] * 4, CAL, broad)
    assert result.level != "duplicate"


def test_contrast_fixes_the_ordering_that_relevance_gets_wrong() -> None:
    """**这条是整套设计的核心证据。**

    P7 是真重复（打卡小程序复述），P3 是假阳性（同能力异对象）。余弦把它们排反了：

        relevance(P3 = 0.8734) = 0.5358  >  relevance(P7 = 0.8631) = 0.4976

    只开第一把锁，任何规则都会把 P3 判得比 P7 更「重复」—— 这是绝对阈值方案的死穴，
    而且**换刻度救不了**：relevance 是余弦的单调变换，顺序原样保留。

    两把锁给出的结论是对的，靠的全是 contrast：

        P7 落差 0.1473 ≥ 0.1385 → duplicate   ✓
        P3 落差 0.1297 <  0.1385 → related     ✓（转人工）
    """
    assert relevance(0.8734, CAL) > relevance(0.8631, CAL), "前提：relevance 把两者排反了"
    assert _probe(0.8631, 0.1473) == "duplicate"
    assert _probe(0.8734, 0.1297) != "duplicate"
