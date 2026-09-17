#!/usr/bin/env python
"""相似度阈值校准：把「阈值该是多少」从拍数变成量数。

**为什么要有这个脚本。** 现有阈值（duplicate 0.80 / related 0.72 / candidate 0.60）
写在 `agents/analyze_agent.py` 的注释里，依据是「当时的 embedding 模型实测」——
但那段实测**没有任何可重跑的东西**，换模型、换语料、甚至只是想知道当初怎么定的，
都无从复现。2026-09-17 复核时发现实际比注释记的更糟：

    库里已存向量两两余弦（4 条需求，主题互不相关）：最高 0.7771
    库里唯一的 relation 行：similarity = 0.7494，模型判 related
    → 真相关的分数**低于**无关对的最高值。绝对阈值在这个模型上不可分。

所以把测量固化成脚本：**能重跑、会红、语料进仓库**。

## 它算什么

**① 余弦分布（pair 视角）** —— 四组语料各自的余弦，得出：

    baseline      = median(unrelated)      重参数化中心（用中位数，对离群稳健）
    noise_ceiling = P95(unrelated)         「无关的能到多高」——候选闸门
    dup_ref       = P05(synonym)           「真重复能低到多少」
    separability  = dup_ref − noise_ceiling     ★ 诊断量，可以为负

`separability ≤ 0` 就是「余弦单独不可分」。这时报告会红字写出
「不得只靠余弦判定重复」，而不是缩一缩阈值糊过去 —— 缩阈值只是把假的劈开点挪个位置。

**② 落差分布（query 视角）** —— 这个才是判别力所在。做法是把每一组 synonym 的 `a` 文本
当作「已有需求库」，再拿 `b` 文本当 query 打进去（真重复确实在库里），
同时拿 unrelated 的 `b` 文本打同一个库（库里没有它的重复）。各自算
`top1 − median(其余 top10)`：

    contrast_dup    = P10(落差 | 真重复有命中)
    contrast_floor  = P95(落差 | 无关)       ← 无关查询能达到的落差上界

实测（2026-09-17）这两组**完全不重叠**（0.147~0.30 vs 0.039~0.071），
而余弦绝对值在它们之间是乱的。

## 它不改任何代码

只打印建议值与可直接粘贴的 `.env` 行（`--print-env`）。阈值要有人看过、想清楚再进配置 ——
比 `backfill_capabilities.py` 的 `--apply` 更保守。理由：阈值错了不会报错，只会静默地
把假阳性放进来或把真重复漏出去，而这两种都要很久以后才被发现。

## 用法

    python -m scripts.calibrate_similarity --dry-run          # 只数条数，一次 API 都不调
    python -m scripts.calibrate_similarity --section contrast  # 只看落差（最省调用）
    python -m scripts.calibrate_similarity --write --print-env # 出报告 + 打印 .env 行
    python -m scripts.calibrate_similarity --compare docs/baseline/similarity_calibration_Doubao-embedding_20260101.json

向量按 `sha256(文本)+模型` 缓存在 `storage/calibration_cache/`，**同一份语料重跑不花钱**。
报告文件名带模型与日期、**不覆盖旧报告**，于是能看出阈值随模型漂移的历史。

## 退出码

    0  跑通，且落差可分（有可用阈值）
    1  环境问题：embedding 未配置 / 语料读不到 / 语料为空
    2  落差**也不可分** —— 两把锁全失效，必须走「灰区一律转人工」兜底
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from requirement_agent.infrastructure.embedding.embedding_service import EmbeddingService

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "similarity_calibration.json"
CACHE_DIR = Path("storage/calibration_cache")
REPORT_DIR = Path("docs/baseline")

# 与生产检索的召回条数解耦：这里要的是「够算落差的样本量」，不是复现线上行为。
# 取 10 是为了和 RetrievalService 的返回上限一致，让落差的量级与线上可比。
RECALL_LIMIT = 10

GROUPS = ("unrelated", "synonym", "same_capability_diff_object", "literal")


# ── 语料 ──────────────────────────────────────────────────────────────────


def load_corpus(path: Path) -> dict[str, Any]:
    """读语料。**校验分组名**，拼错的组名会被静默丢掉、进而算出一个错的基线。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    pairs = raw.get("pairs") or []
    unknown = sorted({str(p.get("group")) for p in pairs} - set(GROUPS))
    if unknown:
        raise ValueError(f"语料里有未知分组 {unknown}；合法值是 {list(GROUPS)}")
    for index, pair in enumerate(pairs):
        for side in ("a", "b"):
            if not str(pair.get(side) or "").strip():
                raise ValueError(f"第 {index} 对缺 {side} 文本")
    return raw


def corpus_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def texts_by_group(corpus: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in GROUPS}
    for pair in corpus["pairs"]:
        grouped[str(pair["group"])].append(pair)
    return grouped


# ── 向量与缓存 ────────────────────────────────────────────────────────────


def _cache_path(text: str, model: str) -> Path:
    digest = hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()[:32]
    return CACHE_DIR / f"{digest}.json"


def embed_with_cache(text: str, model: str, service: EmbeddingService) -> list[float]:
    """取向量，命中缓存则一次 API 都不调。

    缓存键含 model —— 换模型后旧向量必须失效，否则会拿两个模型的向量算余弦，
    得出一个谁也不认识的数（而且看起来完全正常）。
    """
    path = _cache_path(text, model)
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, list) and cached:
                return [float(value) for value in cached]
        except (OSError, ValueError):
            pass  # 缓存坏了就当没有；不因为缓存问题让校准跑不起来
    vector = service.embed(text)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vector), encoding="utf-8")
    return vector


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── 统计 ──────────────────────────────────────────────────────────────────


def quantile(values: list[float], q: float) -> float:
    """线性插值分位数。样本很少时（语料只有十几对）用它是**保守**的选择：
    不会像「取最近秩」那样把 P95 直接退化成最大值。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def relevance_of(cos: float, baseline: float) -> float:
    """与 `domain/similarity_scale.relevance` 同一口径。

    刻意**重算一遍**而不 import：脚本要能对着**历史报告里记的 baseline** 复算建议值，
    而不是被当前 settings 里的 baseline 影响。同一口径写两处是有代价的，
    所以这里加一条断言测试钉住两者一致（`tests/unit/test_similarity_scale.py`）。
    """
    span = 1.0 - baseline
    return (cos - baseline) / span if span > 0 else cos


# ── 主流程 ────────────────────────────────────────────────────────────────


def compute_pair_stats(
    grouped: dict[str, list[dict[str, Any]]], vectors: dict[str, list[float]]
) -> dict[str, Any]:
    """pair 视角：每组语料自身的余弦分布。"""
    stats: dict[str, Any] = {}
    for name, pairs in grouped.items():
        cosines = [cosine(vectors[p["a"]], vectors[p["b"]]) for p in pairs]
        if not cosines:
            continue
        stats[name] = {
            "count": len(cosines),
            "min": min(cosines),
            "p05": quantile(cosines, 0.05),
            "median": statistics.median(cosines),
            "p95": quantile(cosines, 0.95),
            "max": max(cosines),
            "cosines": [round(value, 6) for value in cosines],
            "pairs": [
                {"a": p["a"][:60], "b": p["b"][:60], "cosine": round(c, 6), "note": p.get("note", "")}
                for p, c in zip(pairs, cosines)
            ],
        }
    return stats


def duplicate_reference(
    grouped: dict[str, list[dict[str, Any]]], vectors: dict[str, list[float]]
) -> float:
    """真重复对的余弦 P05 —— 泄漏判据的参照线。

    **为什么用「真重复的下界」而不是「无关的上界」当泄漏判据。** 第一版用无关分布的
    P95，结果短文本探针集体误报：短词之间的余弦基线本来就高（实测无关对里 short 档
    中位 0.7958、long 档只有 0.7045，差 0.09），一个不分长度的上界线必然把短探针全扫进去。
    而那一版**真正的泄漏**长这样 ——

        v1 无关组探针「财务可按月度筛选并导出 Excel…」 → 命中 0.9592
        v1 无关组探针「新员工入职后分配培训课程…」     → 命中 0.9807
        v1 无关组探针「财务月度报表导出」              → 命中 1.0000（逐字相同）

    —— 全部落在真重复的区间里。所以判据应当是：**一个「无关」探针不该与库里的东西
    匹配得像真重复一样好**。这既不依赖长度，也不引入外部阈值，语义还直接对得上。
    """
    cosines = [cosine(vectors[p["a"]], vectors[p["b"]]) for p in (grouped.get("synonym") or [])]
    return quantile(cosines, 0.05) if cosines else 1.0


def length_drift(
    grouped: dict[str, list[dict[str, Any]]], vectors: dict[str, list[float]]
) -> dict[str, Any]:
    """无关对余弦按长度档拆开 —— **基线是否随文本长度漂移**。

    这是本项目一直想看而没看的一件事：如果短文本的余弦天然比长文本高 0.09，
    那么一个全局 `baseline` 对短需求就是系统性高估，`relevance` 会被抬高。
    本批次**只报告不按档定基线**（库里 4 条数据支撑不了分档），但必须留下证据。
    """
    tiers: dict[str, list[float]] = {}
    for pair in grouped.get("unrelated") or []:
        tier = str(pair.get("length") or "unknown")
        tiers.setdefault(tier, []).append(cosine(vectors[pair["a"]], vectors[pair["b"]]))
    stats = {
        tier: {"count": len(v), "median": statistics.median(v), "max": max(v)}
        for tier, v in tiers.items()
        if v
    }
    medians = [s["median"] for s in stats.values()]
    spread = (max(medians) - min(medians)) if len(medians) >= 2 else 0.0
    return {"by_tier": stats, "spread": spread, "significant": spread >= 0.05}


def compute_contrast_stats(
    grouped: dict[str, list[dict[str, Any]]], vectors: dict[str, list[float]]
) -> dict[str, Any]:
    """query 视角：拿 synonym 的 `a` 当「已有需求库」，别的文本当 query 打进去。

    ⚠️ 库里只有 12 条，而真实需求库会更大。库越大，无关 query 的 top1 越可能偶然偏高
    （从更多候选里挑最大），落差也会随之变小。所以这里测出的 contrast_floor 是
    **乐观下界** —— 真上生产后要按实际库容量复核。报告里会记下库容量。

    ## 泄漏检查（v2 加的）

    v1 的语料犯过一次错：`unrelated` 组与 `synonym` 组独立起草，**同几个主题被写了两遍**，
    于是「无关」探针在对照库里命中了真重复（top1 最高到 1.0000）—— 无关组的落差被撑起来，
    脚本据此**误报「落差也不可分」**。语料错了，结论就跟着错，而且看起来完全正常。

    所以这里对 `unrelated` 组逐条检查：**top1 超过无关对自身的余弦 P95** 就是在库里
    命中了不该有的东西。命中的行从 `unrelated_clean` 里剔除，并由 `unrelated` 保留原样
    以便核对。判据是自洽的 —— 拿无关分布自己的上界当参照，不引入外部阈值。
    """
    library_pairs = grouped.get("synonym") or []
    library_texts = [p["a"] for p in library_pairs]
    if len(library_texts) < 4:
        return {"error": f"对照库只有 {len(library_texts)} 条，不足以算落差（至少 4 条）"}
    library_vectors = [vectors[t] for t in library_texts]

    probes: dict[str, list[dict[str, Any]]] = {
        # 真重复：`b` 文本的重复项（`a`）确实在库里
        "synonym_hit": [
            {"text": p["b"], "note": p.get("note", ""), "expect_in_library": p["a"][:40]}
            for p in library_pairs
        ],
        # 无关：库里有语义无关的其它需求，但没有它的重复
        "unrelated": [
            {"text": p["b"], "note": p.get("note", "")}
            for p in (grouped.get("unrelated") or [])
        ],
        # 红区：同能力异对象，看它落在哪边
        "same_capability_diff_object": [
            {"text": p[side], "note": p.get("note", "")}
            for p in (grouped.get("same_capability_diff_object") or [])
            for side in ("a", "b")
        ],
    }

    leak_threshold = duplicate_reference(grouped, vectors)

    def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
        deltas = [r["contrast"] for r in rows]
        return {
            "count": len(rows),
            "p10": quantile(deltas, 0.10),
            "p25": quantile(deltas, 0.25),
            "median": statistics.median(deltas),
            "p95": quantile(deltas, 0.95),
            "max": max(deltas),
            "rows": rows,
        }

    out: dict[str, Any] = {"library_size": len(library_texts)}
    for name, items in probes.items():
        rows = []
        for item in items:
            probe_vec = vectors[item["text"]]
            scored = sorted(
                ((cosine(probe_vec, vec), text) for vec, text in zip(library_vectors, library_texts)),
                reverse=True,
            )[:RECALL_LIMIT]
            scores = [s for s, _ in scored]
            top1 = scores[0]
            rest = scores[1:]
            delta = top1 - statistics.median(rest) if len(rest) >= 3 else top1 - statistics.fmean(rest)
            rows.append(
                {
                    "text": item["text"][:60],
                    "top1": round(top1, 6),
                    "top1_text": scored[0][1][:60],
                    "contrast": round(delta, 6),
                    "n": len(scores),
                    # 只对 unrelated 组判泄漏：synonym_hit 的 top1 **本来就该**高（真重复在库里），
                    # 红区组的 top1 高是它的定义（长得很像），都不是错误。
                    "suspected_leak": name == "unrelated" and top1 > leak_threshold,
                    "note": item.get("note", ""),
                }
            )
        out[name] = summarise(rows)
        if name == "unrelated":
            clean = [r for r in rows if not r["suspected_leak"]]
            out["unrelated_clean"] = summarise(clean)
            out["leak_threshold"] = leak_threshold
            out["leaked"] = [r for r in rows if r["suspected_leak"]]
    return out


def suggest_thresholds(
    pair_stats: dict[str, Any],
    contrast_stats: dict[str, Any],
    drift: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推导建议阈值。**每条都有出处，没有一个是拍的。**

    | 阈值 | 取自 | 理由 |
    |---|---|---|
    | `candidate_relevance` | `relevance(P95 无关)` | 噪声上界之上才值得往上看 |
    | `related_relevance`   | 同 candidate | relevance **不做级别区分** —— 实测它分不开，级别由 contrast 区分 |
    | `duplicate_relevance` | `relevance(max 无关)` | 比语料里见过的**任何一个**无关对都近 |
    | `related_contrast`    | `P95(落差│无关)` | 「比无关查询能有的落差还突出」 |
    | `duplicate_contrast`  | `P10(落差│真重复)` | 「够得上真重复的落差下界」 |

    ⚠️ 两个 contrast 阈值必须满足 `related_contrast < duplicate_contrast`，
    否则 related 比 duplicate 还严、级别会倒挂。报告会显式检查这一条。
    """
    unrelated = pair_stats.get("unrelated", {})
    synonym = pair_stats.get("synonym", {})
    if not unrelated or not synonym:
        return {"error": "缺少 unrelated 或 synonym 组，无法推导阈值"}

    baseline = unrelated["median"]
    noise_ceiling = unrelated["p95"]
    unrelated_max = unrelated["max"]
    dup_ref = synonym["p05"]
    separability = dup_ref - noise_ceiling

    contrast_hit = contrast_stats.get("synonym_hit", {})
    # **用剔除泄漏后的无关组**：命中了真重复的「无关」探针会把无关落差撑起来，
    # 用它算出来的 related_contrast 会被推高、进而把真关联挡在门外。
    contrast_unrel = contrast_stats.get("unrelated_clean") or contrast_stats.get("unrelated", {})
    if "p10" not in contrast_hit or "p95" not in contrast_unrel:
        return {"error": "缺少落差统计，无法推导 contrast 阈值"}

    related_contrast = contrast_unrel["p95"]
    duplicate_contrast = contrast_hit["p10"]

    warnings: list[str] = []
    leaked = contrast_stats.get("leaked") or []
    if leaked:
        warnings.append(
            f"**疑似语料泄漏 {len(leaked)} 条**：这些「无关」探针在对照库里命中得像**真重复一样好**"
            f"（top1 ≥ P05 真重复 {contrast_stats.get('leak_threshold', 0):.4f}），"
            "说明对照库里存在与它同主题的文本。已从 unrelated_clean 中剔除，"
            "但**应改语料而不是靠剔除** —— 剔除只是让本次报告干净，下次换语料还会犯："
        )
        for row in leaked:
            warnings.append(
                f"    · top1 {row['top1']:.4f}  「{row['text'][:34]}…」→ 命中「{row['top1_text'][:34]}…」"
            )
    if separability <= 0:
        warnings.append(
            f"余弦单独不可分：P05(真重复) {dup_ref:.4f} ≤ P95(无关) {noise_ceiling:.4f}"
            f"（separability {separability:+.4f}）。不得只靠余弦下重复结论 —— "
            "这正是引入 contrast 闸门的原因。"
        )
    if related_contrast >= duplicate_contrast:
        warnings.append(
            f"落差也不可分：P95(落差│无关) {related_contrast:.4f} ≥ P10(落差│真重复) "
            f"{duplicate_contrast:.4f}。两把锁全失效 —— 只能走「灰区一律转人工」兜底。"
        )
    if contrast_stats.get("library_size", 0) < 8:
        warnings.append(
            f"对照库只有 {contrast_stats.get('library_size')} 条，落差统计不稳；"
            "真实库更大时无关 query 的 top1 会偶然偏高、落差变小，建议扩充语料后重跑。"
        )
    if drift and drift.get("significant"):
        detail = "、".join(
            f"{tier} 中位 {s['median']:.4f}（n={s['count']}）"
            for tier, s in sorted((drift.get("by_tier") or {}).items())
        )
        warnings.append(
            f"**基线随文本长度漂移 {drift['spread']:.4f}**：{detail}。"
            "短文本的余弦天然更高，单一 `baseline` 对短需求是系统性高估（`relevance` 被抬高）。"
            "⚠️ 本批次**不按长度分档定基线**（库里 4 条数据支撑不了分档），但这条要记住 —— "
            "它同时说明 **contrast 比 relevance 更值得依赖**：落差是同一批候选内部的差值，"
            "长度带来的整体抬升会互相抵消（实测各档落差分布几乎重合）。"
        )

    return {
        "baseline": baseline,
        "noise_ceiling": noise_ceiling,
        "unrelated_max": unrelated_max,
        "duplicate_ref": dup_ref,
        "separability": separability,
        "candidate_relevance": relevance_of(noise_ceiling, baseline),
        "related_relevance": relevance_of(noise_ceiling, baseline),
        "duplicate_relevance": relevance_of(unrelated_max, baseline),
        "related_contrast": related_contrast,
        "duplicate_contrast": duplicate_contrast,
        "warnings": warnings,
    }


def print_report(
    pair_stats: dict[str, Any], contrast_stats: dict[str, Any], suggestion: dict[str, Any]
) -> None:
    """打印报告。**三段各自独立** —— `--section pairs` / `--section contrast` 只跑一段，
    缺的那段留空即可，不要让它去访问不存在的键。"""
    if pair_stats:
        print("=" * 78)
        print("① 各语料组的余弦分布（pair 视角）")
        print("=" * 78)
        print(f"{'组':<32}{'n':>4}{'min':>9}{'P05':>9}{'中位':>9}{'P95':>9}{'max':>9}")
        for name in GROUPS:
            if name not in pair_stats:
                continue
            s = pair_stats[name]
            print(
                f"{name:<32}{s['count']:>4}{s['min']:>9.4f}{s['p05']:>9.4f}"
                f"{s['median']:>9.4f}{s['p95']:>9.4f}{s['max']:>9.4f}"
            )
        print()

    if contrast_stats:
        print("=" * 78)
        print("② 落差分布（query 视角，对照库 = synonym 的 a 侧）")
        print("=" * 78)
        if "error" in contrast_stats:
            print(f"✗ {contrast_stats['error']}")
        else:
            print(f"对照库容量：{contrast_stats.get('library_size', '?')} 条")
            print(f"{'类别':<32}{'n':>4}{'P10':>9}{'P25':>9}{'中位':>9}{'P95':>9}{'max':>9}")
            for name in ("synonym_hit", "unrelated", "unrelated_clean", "same_capability_diff_object"):
                if name not in contrast_stats or "p10" not in contrast_stats[name]:
                    continue
                s = contrast_stats[name]
                tag = f"{name}  ← 推导用" if name == "unrelated_clean" else name
                print(
                    f"{tag:<32}{s['count']:>4}{s['p10']:>9.4f}{s['p25']:>9.4f}"
                    f"{s['median']:>9.4f}{s['p95']:>9.4f}{s['max']:>9.4f}"
                )
            leaked = contrast_stats.get("leaked") or []
            if leaked:
                print(f"\n  ⚠️ 疑似泄漏 {len(leaked)} 条（未剔除前的 unrelated 含它们）：")
                for row in leaked:
                    print(f"     top1 {row['top1']:.4f}  「{row['text'][:30]}…」")
                    print(f"                      命中「{row['top1_text'][:34]}…」")
        print()

    if not suggestion:
        return

    print("=" * 78)
    print("③ 建议阈值")
    print("=" * 78)
    if "error" in suggestion:
        print(f"✗ {suggestion['error']}")
        return
    print(f"  SIMILARITY_BASELINE            = {suggestion['baseline']:.4f}"
          f"   （P50 无关）")
    print(f"  SIMILARITY_NOISE_CEILING       = {suggestion['noise_ceiling']:.4f}"
          f"   （P95 无关）")
    print(f"  SIMILARITY_CONTRAST_DUPLICATE  = {suggestion['duplicate_contrast']:.4f}"
          f"   （P10 落差│真重复）")
    print(f"  SIMILARITY_CONTRAST_RELATED    = {suggestion['related_contrast']:.4f}"
          f"   （P95 落差│无关）")
    print()
    print(f"  separability = {suggestion['separability']:+.4f}"
          f"   （P05 真重复 {suggestion['duplicate_ref']:.4f}"
          f" − P95 无关 {suggestion['noise_ceiling']:.4f}）")

    print()
    if suggestion["warnings"]:
        print("⚠️  警告")
        for warning in suggestion["warnings"]:
            print(f"  · {warning}")
    else:
        print("✓ 余弦与落差均可用；两把锁各自有效。")


def print_env_block(suggestion: dict[str, Any], model: str, source: str) -> None:
    print()
    print("# ── 相似度校准（由 scripts/calibrate_similarity.py 生成，勿手改）──")
    print(f"# 模型 {model}；语料 & 报告见 {source}")
    print(f"SIMILARITY_BASELINE={suggestion['baseline']:.4f}")
    print(f"SIMILARITY_NOISE_CEILING={suggestion['noise_ceiling']:.4f}")
    print(f"SIMILARITY_CONTRAST_DUPLICATE={suggestion['duplicate_contrast']:.4f}")
    print(f"SIMILARITY_CONTRAST_RELATED={suggestion['related_contrast']:.4f}")
    print(f"SIMILARITY_CALIBRATION_MODEL={model}")
    print(f"SIMILARITY_CALIBRATION_SOURCE={source}")


def compare_with_previous(path: Path, suggestion: dict[str, Any]) -> None:
    """与旧报告对比，打印漂移。换模型后最容易漏看的就是「阈值该动没动」。"""
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"\n[compare] 读不了 {path}：{exc}")
        return
    old = previous.get("suggestion") or {}
    print()
    print("=" * 78)
    print(f"④ 与 {path.name} 对比")
    print("=" * 78)
    print(f"  旧模型：{previous.get('model', '?')}    新模型：{suggestion.get('model', '?')}")
    for key, label in (
        ("baseline", "基线"),
        ("noise_ceiling", "噪声上界"),
        ("duplicate_contrast", "落差·重复"),
        ("related_contrast", "落差·关联"),
        ("separability", "separability"),
    ):
        if key not in old or key not in suggestion:
            continue
        delta = suggestion[key] - old[key]
        flag = "  ← 漂移显著" if key != "separability" and abs(delta) >= 0.03 else ""
        print(f"  {label:<14}{old[key]:>9.4f} → {suggestion[key]:>9.4f}   {delta:+.4f}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="相似度阈值校准（语料驱动的实测，不改代码）")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH, help="语料路径")
    parser.add_argument("--write", action="store_true", help=f"写报告到 {REPORT_DIR}/")
    parser.add_argument("--print-env", action="store_true", help="打印可粘贴的 .env 行")
    parser.add_argument("--compare", type=Path, help="与旧报告对比漂移")
    parser.add_argument("--dry-run", action="store_true", help="只数条数与预估调用次数，不调 API")
    parser.add_argument(
        "--section", choices=("all", "pairs", "contrast", "suggest"), default="all",
        help="只跑一部分（contrast 最省调用）",
    )
    args = parser.parse_args()

    try:
        corpus = load_corpus(args.fixture)
    except (OSError, ValueError) as exc:
        print(f"[错误] 语料读不了：{exc}")
        return 1
    grouped = texts_by_group(corpus)

    unique_texts = sorted({p[side] for p in corpus["pairs"] for side in ("a", "b")})
    print(f"语料：{args.fixture}")
    print(f"共 {len(corpus['pairs'])} 对 / {len(unique_texts)} 条唯一文本")
    for name in GROUPS:
        print(f"  {name:<32}{len(grouped[name]):>3} 对")

    service = EmbeddingService()
    if not service.is_configured():
        print("\n[skip] embedding 网关未配置（EMBEDDING_BASE_URL / EMBEDDING_API_KEY），未做校准。")
        return 1

    from requirement_agent.config.settings import settings

    model = settings.embedding_model

    if args.dry_run:
        hits = sum(1 for text in unique_texts if _cache_path(text, model).exists())
        print(f"\n[dry-run] 模型 {model}：唯一文本 {len(unique_texts)} 条，"
              f"缓存已命中 {hits} 条，预计调用 embedding **{len(unique_texts) - hits}** 次")
        print(f"[dry-run] 缓存目录：{CACHE_DIR.resolve()}")
        return 0

    print(f"\n开始取向量（模型 {model}，缓存于 {CACHE_DIR}/）…")
    vectors: dict[str, list[float]] = {}
    for index, text in enumerate(unique_texts, start=1):
        try:
            vectors[text] = embed_with_cache(text, model, service)
        except Exception as exc:  # noqa: BLE001
            print(f"[错误] 第 {index} 条取向量失败：{type(exc).__name__}: {exc}")
            return 1
    print(f"向量就绪（{len(vectors)} 条，维度 {len(next(iter(vectors.values())))}）\n")

    pair_stats = compute_pair_stats(grouped, vectors) if args.section in ("all", "pairs") else {}
    contrast_stats = (
        compute_contrast_stats(grouped, vectors) if args.section in ("all", "contrast") else {}
    )

    if args.section == "pairs":
        pair_stats = compute_pair_stats(grouped, vectors)
        print_report(pair_stats, {}, {})
        return 0
    if args.section == "contrast":
        contrast_stats = compute_contrast_stats(grouped, vectors)
        print_report({}, contrast_stats, {})
        return 0

    # all / suggest 都需要两份统计
    if not pair_stats:
        pair_stats = compute_pair_stats(grouped, vectors)
    if not contrast_stats:
        contrast_stats = compute_contrast_stats(grouped, vectors)

    drift = length_drift(grouped, vectors)
    suggestion = suggest_thresholds(pair_stats, contrast_stats, drift)
    suggestion["model"] = model
    suggestion["length_drift"] = drift
    print_report(pair_stats, contrast_stats, suggestion)

    report_name = f"similarity_calibration_{model}_{datetime.now(timezone.utc):%Y%m%d}.json"
    report_path = REPORT_DIR / report_name
    if args.write and "error" not in suggestion:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "model": model,
                    "dimension": len(next(iter(vectors.values()))),
                    "measured_at": datetime.now(timezone.utc).isoformat(),
                    "corpus": str(args.fixture),
                    "corpus_sha256": corpus_sha256(args.fixture),
                    "pair_stats": pair_stats,
                    "contrast_stats": contrast_stats,
                    "suggestion": suggestion,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n报告已写入 {report_path}")

    if args.print_env and "error" not in suggestion:
        print_env_block(suggestion, model, str(report_path if args.write else args.fixture))
    if args.compare:
        compare_with_previous(args.compare, suggestion)

    if "error" in suggestion:
        return 1
    warnings = suggestion.get("warnings") or []
    if any("落差也不可分" in w for w in warnings):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
