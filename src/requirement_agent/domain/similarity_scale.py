"""相似度判定标尺（纯函数：不接 session、不写库、不读配置）。

**为什么要有这个模块。** 原先判定只有一条线：余弦 ≥ 0.80 判重复、≥ 0.72 判关联。
这两个数是在**另一个模型**上标定出来的，写在 `analyze_agent.py` 的注释里。实测（2026-09-17，
`Doubao-embedding` 4096 维）表明这条线**根本划不出来**：

    库里已存向量两两余弦（4 条需求，主题互不相关）：最高 0.7771
    库里唯一的 relation 行：similarity = 0.7494，模型判 related（理由充分、判得对）
    → **真相关比无关的还低**

单调变换救不了这个顺序 —— 它们本来就在同一个区间里。所以判定改成**两把锁**：

1. **relevance（全局距离）** —— 这一对文本本身够不够近
2. **contrast（本次查询内的突出度）** —— 这个候选相对它同批的其它候选，是不是鹤立鸡群

第二把锁才是关键。实测同一 query 内 `top1 − median(其余)`：

    真重复      0.147 ~ 0.30
    假阳性红区  0.1297        （「导出 Excel」vs「导出员工数据」这类同能力异对象）
    语义无关    0.039 ~ 0.071

**无关组与真重复组完全不重叠**，而余弦绝对值在它们之间是乱的（无关的 0.7983 可以高过
真重复的 0.8631 之外的任何一对）。这是本项目目前唯一一个真正干净的判别信号。

---

三条容易搞错、已在此写死的约定：

**① `relevance()` 不截断。** 它可能返回负数（余弦低于基线时）。截断到 0 会把噪声地板
以下的整段压成同一个值，排序退化成任意。排序用未截断值，**只有展示时才钳到 [0,1]**
（见 `RelativeVerdict.relevance_display`）。

**② `relevance()` 单独用等于什么都没做。** 它是余弦的严格单调重参数化：
`cos ≥ T ⟺ relevance ≥ (T−b)/(1−b)`，任何决策规则平移过去完全等价，排序一字不变。
换个刻度就宣布「改成相对判定了」是自欺。它真正买到的是三样：

  - 换 embedding 模型后只需重测 `baseline` **一个数**，而不是重拟三个阈值；
  - 噪声地板从注释里的一个数变成有出处、可追溯的配置；
  - 展示上 0 有了含义（`0.7038` 渲染成「70% 相似」是撒谎，它其实是噪声中心）。

**判别力来自 `contrast()`，不是它。** 这句话必须留着 —— 否则下一个人会以为换个刻度就修好了。

**③ 分析模式（broad / balanced / strict）只能放宽 relevance 闸门，绝不能放宽 contrast 闸门。**
`broad` 的用途是「宁可多给人看几条」，不是「宁可多判几个重复」。放宽 contrast 会把红区
（同能力异对象）重新放进来，而那正是这套两把锁要挡的东西。已钉测试。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "DEFAULT_GATES",
    "ContrastResult",
    "RelativeVerdict",
    "SimilarityCalibration",
    "SimilarityGates",
    "VerdictLevel",
    "contrast",
    "relevance",
    "verdict",
]

VerdictLevel = Literal["duplicate", "related", "candidate", "none", "unverifiable"]


@dataclass(frozen=True, slots=True)
class SimilarityCalibration:
    """一次校准测量出的参照点。**只对 `model` 这一个模型有效。**

    换 embedding 模型后重跑 `scripts/calibrate_similarity.py`，不要把旧的数字带过去 ——
    绝对阈值 0.80 在模型 A 上是「无关分布上方一点」，在模型 B 上可能是「无关分布正中间」，
    而从代码上完全看不出来。`scripts/calibrate_similarity.py` 会把 model 一起写进报告。
    """

    baseline: float
    """无关文本对的余弦**中位数**。重参数化的中心，用中位数是为了对离群稳健。"""

    noise_ceiling: float
    """无关文本对余弦的 **P95**。候选闸门 —— 超过它才值得往上看一眼。"""

    model: str
    """测量时使用的 `EMBEDDING_MODEL`。基线只对该模型有效。"""

    measured_at: str
    """测量时间（ISO 8601）。"""

    corpus_sha256: str
    """语料文件的 sha256。语料改了但没重测，据此可以检出。"""

    separability: float
    """`P05(真重复) − noise_ceiling` —— **诊断量，可以为负**。

    ≤ 0 意味着「该模型下余弦单独不可分」：真重复的下界还没够到无关的上界。
    这时**不得**只靠余弦下重复结论，必须走灰区转人工。当前实测就是这个情况。
    """


@dataclass(frozen=True, slots=True)
class ContrastResult:
    """一批候选内部的落差（同一 query 的候选集，不是跨 query）。"""

    value: float
    """`top1 − 其余的中心`。"""

    method: Literal["median", "mean"]
    """n ≥ 4 用中位数（对「恰好有两条都相关」稳健，不像 top1−top2 那样被单个接近的
    竞争者打崩）；n 在 2~3 用均值。"""

    confidence: Literal["high", "low"]
    """样本量决定的置信度。`low` 表示这个落差是拿 1~2 个数算出来的，别当真。"""

    n: int
    """参与统计的候选条数（含 top1）。"""


@dataclass(frozen=True, slots=True)
class SimilarityGates:
    """判定闸门。**两把锁各自有门槛**，级别越高要求越严。

    默认值来自 `scripts/calibrate_similarity.py` 的实跑报告
    （`docs/baseline/similarity_calibration_Doubao-embedding_20260917.json`），
    换算过程记在文件的 `suggestion` 段里。批 2 会把这几个数搬进 `settings`；
    放在这里是为了让本模块可独立单测 —— **领域层不该读配置**。

    ⚠️ **换 embedding 模型后这几个数全部失效**，必须重跑校准。
    校准报告里带 `SIMILARITY_CALIBRATION_MODEL`，批 2 会拿它与当前模型比对并告警。
    """

    duplicate_relevance: float = 0.42
    """= `relevance(max 无关对)` —— 比语料里见过的**任何一个**无关对都近。"""

    duplicate_contrast: float = 0.1385
    """= `P10(落差 │ 真重复)` —— 即「90% 的真重复能达到这个落差」。

    取 P10 而不是更低，是因为无关组与红区组的落差上界（0.1307 / 0.1330）紧贴在这里：
    往下放一点点，假阳性就会从这两组里漏进来。代价是约 10% 的真重复会被压到 related
    （转人工）—— 这正是「宁可漏报，不可自相矛盾」要的取舍。
    """

    related_relevance: float = 0.31
    """= `relevance(P95 无关)` —— 与 `candidate` 同值。

    刻意与 candidate 相同：实测 relevance **分不开级别**（真相关的余弦可以低于无关的），
    级别区分全部交给 contrast。这里不假装它能在 related 与 candidate 之间划出区别。
    """

    related_contrast: float = 0.0747
    """= `P95(落差 │ 无关)` —— 「比无关查询能有的落差还突出」。"""

    candidate_relevance: float = 0.31
    """`candidate` **只看 relevance**：它的用途是「值得展示给人看」，
    不是自动下判，不需要突出度佐证。"""


DEFAULT_GATES = SimilarityGates()


@dataclass(frozen=True, slots=True)
class RelativeVerdict:
    """一个候选的判定结果，连同它是怎么被算出来的。"""

    level: VerdictLevel
    relevance: float
    """**未截断**，可能为负。判定与排序都用它。"""

    contrast: float | None
    """本次查询的落差。候选不足 2 条时为 `None`（无法判定突出度）。"""

    reason: str
    """中文短语，直接可展示给审核人。"""

    @property
    def relevance_display(self) -> float:
        """展示用的 relevance，钳到 [0, 1]。**只用于展示。**"""
        return max(0.0, min(1.0, self.relevance))


def relevance(cos: float, calibration: SimilarityCalibration) -> float:
    """把余弦换算成「高出无关基线的程度」。**不截断**（可为负）。

    含义是「从噪声中心走到完全相同的距离，走完了多少」。0 = 与无关文本的中心无异，
    1 = 完全相同。负值表示比无关文本的中心还远。

    ⚠️ 这是余弦的**严格单调重参数化**，单独用它不改变任何判定结果。理由见模块 docstring ②。
    """
    span = 1.0 - calibration.baseline
    if span <= 0:
        # baseline 被配成了 1.0 或更大 —— 换算没有意义，直接返回原始余弦而不是抛错：
        # 判定链路上的一个坏配置不该让整条分析链挂掉，但要留下可见的痕迹。
        return cos
    return (cos - calibration.baseline) / span


def contrast(scores: Sequence[float]) -> ContrastResult | None:
    """算一批候选的落差：`top1 − 其余的中心`。

    - `n ≥ 4` → 中位数，置信度 `high`
    - `n ∈ {2,3}` → 均值，置信度 `low`
    - `n ≤ 1` → **返回 `None`**。单条候选没有「其余」可言，落差无从谈起。
      调用方据此得到 `unverifiable`，而不是一个假装算出来的 0 —— 0 会被误读成
      「算过了，没有落差」。

    实测（2026-09-17）：真重复 0.147~0.30，语义无关 0.039~0.071，两组不重叠。
    """
    values = [float(value) for value in scores]
    if len(values) <= 1:
        return None
    ranked = sorted(values, reverse=True)
    top = ranked[0]
    rest = ranked[1:]
    if len(rest) >= 3:
        return ContrastResult(
            value=top - statistics.median(rest), method="median", confidence="high", n=len(values)
        )
    return ContrastResult(
        value=top - statistics.fmean(rest), method="mean", confidence="low", n=len(values)
    )


def verdict(
    cos: float | None,
    all_cos: Sequence[float],
    calibration: SimilarityCalibration,
    gates: SimilarityGates = DEFAULT_GATES,
) -> RelativeVerdict:
    """对一个候选下判定。**两把锁同开才算数。**

    `all_cos` 是**同一 query 的整批候选**余弦（含本候选）。落差是查询级属性，
    所以每个候选都拿同一个落差值，各配自己的 relevance —— 「这批里有鹤立鸡群的」
    （查询级）且「这一条本身够近」（对级），两个条件缺一不可。

    ⚠️ 在循环里逐个调用会重复计算落差（O(n² log n)）。n 是召回条数（默认 5~10），
    这个开销可以忽略；**若将来把召回上限调到几十上百，先在这里做一次预计算。**

    `cos is None` 表示这个候选**没有余弦**（只命中了关键词、没进向量召回）。
    这时不做相似度判定，返回 `unverifiable` —— 拿「命中几个 token 加权的和」去比
    相似度阈值是范畴错误，那个数的量纲随查询词数变化。
    """
    if cos is None:
        return RelativeVerdict(
            level="unverifiable",
            relevance=0.0,
            contrast=None,
            reason="该候选只命中关键词、没有向量分数，无法做相似度判定。",
        )

    rel = relevance(cos, calibration)
    contrast_result = contrast(all_cos)
    contrast_value = contrast_result.value if contrast_result is not None else None

    if contrast_value is None:
        # 只有一条候选：距离可以说，突出度不能说。
        return RelativeVerdict(
            level="candidate" if rel >= gates.candidate_relevance else "none",
            relevance=rel,
            contrast=None,
            reason="本次只有一条候选，无法判断它是否突出，需人工确认。",
        )

    if rel >= gates.duplicate_relevance and contrast_value >= gates.duplicate_contrast:
        return RelativeVerdict(
            level="duplicate", relevance=rel, contrast=contrast_value,
            reason="与历史需求高度接近，且明显突出于本次其它候选。",
        )
    if rel >= gates.related_relevance and contrast_value >= gates.related_contrast:
        return RelativeVerdict(
            level="related", relevance=rel, contrast=contrast_value,
            reason="与历史需求存在关联，且在本次候选中较为突出。",
        )
    if rel >= gates.candidate_relevance:
        # 距离够、突出度不够 —— **这正是灰区**（同能力异对象的假阳性也落在这里）。
        # 刻意不下重复结论：实测这两类在余弦尺度上不可分，硬劈出来的线一定是拍的。
        return RelativeVerdict(
            level="candidate", relevance=rel, contrast=contrast_value,
            reason="与历史需求有一定距离，但未突出于其它候选，**需人工确认**。",
        )
    return RelativeVerdict(
        level="none", relevance=rel, contrast=contrast_value,
        reason="与历史需求的距离落在噪声区间内。",
    )
