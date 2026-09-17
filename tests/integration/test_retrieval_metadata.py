"""检索证据落库（B4 批 4）。

判定改成两把锁之后，**要复校阈值就必须知道「当时到底召回了什么、各自多少分」** ——
`contrast` 是查询级统计量，而这些事后从库里查不回来（`analysis.candidates` 是被模型
裁剪过的证据面板，不是原始召回）。所以 `retrieve_node` 产出一份检索记录，随
`requirement_source.metadata["retrieval"]` 落库。

这个文件同时守着**「结构化过滤为什么只接 soft」**那条实测结论。
"""

from __future__ import annotations

import pytest

from requirement_agent.application.retrieval_service import RetrievalService
from requirement_agent.config.settings import settings
from requirement_agent.workflows.agents_nodes import retrieve_node

# 库里 REQ-000015（门店巡检）的正文，用来当一次真实检索的输入
INSPECTION_SUMMARY = "门店巡检管理系统：支持按区域生成巡检计划，巡检员手机打卡并上传现场照片。"
# 库里唯一那条真实关系的另一端
RELATED_KEY = "REQ-000002"


def _state(source_type: str = "web") -> dict:
    return {
        "extracted": {"summary": INSPECTION_SUMMARY, "requirement_title": "门店巡检"},
        "source_type": source_type,
        "source_text": INSPECTION_SUMMARY,
    }


# ── 检索记录的形状 ────────────────────────────────────────────────────────


def test_retrieve_node_records_what_happened() -> None:
    out = retrieve_node(_state())
    record = out["retrieval"]

    assert set(record) >= {
        "query", "recall_limit", "candidate_count", "contrast",
        "calibration", "filters", "candidates",
    }
    assert record["candidate_count"] == len(record["candidates"])
    assert record["recall_limit"] == settings.similarity_recall_limit

    # 校准块：换模型后阈值会静默失准，所以要逐次记下来，而不是只打一条日志
    assert record["calibration"]["baseline"] == settings.similarity_baseline
    assert record["calibration"]["noise_ceiling"] == settings.similarity_noise_ceiling
    assert record["calibration"]["model"] == settings.similarity_calibration_model
    assert isinstance(record["calibration"]["stale"], bool)


def test_recorded_candidates_are_not_trimmed() -> None:
    """**候选一律不裁剪。**

    裁掉低分候选就等于抹掉「当时到底召回了什么」—— 而「未召回 vs 模型否定」的
    区分正依赖它（批 5）。这里断言记录条数与实际召回条数一致，而不是「只留 top N」。
    """
    out = retrieve_node(_state())
    recalled = len(out["candidates"])
    assert out["retrieval"]["candidate_count"] == recalled
    assert len(out["retrieval"]["candidates"]) == recalled


def test_each_recorded_candidate_carries_the_raw_evidence() -> None:
    out = retrieve_node(_state())
    top = out["retrieval"]["candidates"][0]

    # 余弦与 relevance 都是**未截断**的原始量 —— 校准要的是它，钳过就没法复算
    assert top["vector_similarity"] is None or 0.0 <= top["vector_similarity"] <= 1.0
    assert top["relevance"] is None or isinstance(top["relevance"], float)
    assert "retrieval_score" in top and "match_type" in top


# ── 过滤模式 ──────────────────────────────────────────────────────────────


def test_soft_mode_never_drops_candidates_and_annotates_the_channel(monkeypatch) -> None:
    """**soft 的核心承诺：一条候选都不删。**

    它是唯一有意义的默认值 —— 硬过滤在这个库上的后果见本文件末尾的反例。
    """
    monkeypatch.setattr(settings, "similarity_filter_mode", "soft")
    out = retrieve_node(_state(source_type="web"))

    assert out["retrieval"]["filters"]["mode"] == "soft"
    assert out["retrieval"]["filters"]["applied"] == {}, "soft 不真过滤"
    assert out["retrieval"]["filters"]["annotated_against"] == {"channel": "web"}
    for row in out["retrieval"]["candidates"]:
        assert "channel_match" in row, "soft 只标注"
        assert isinstance(row["channel_match"], bool)


def test_off_mode_is_byte_identical_to_not_wiring_it(monkeypatch) -> None:
    """`off` 用于对照与回滚，所以它**不能**留下任何痕迹。

    尤其是 `requested` 必须是空的 —— 在 off 里写「请求了 channel=web」会让人
    以为检索按渠道过滤过，而它一条都没过滤。
    """
    monkeypatch.setattr(settings, "similarity_filter_mode", "off")
    out_off = retrieve_node(_state(source_type="web"))

    assert out_off["retrieval"]["filters"] == {
        "mode": "off", "requested": {}, "applied": {}, "annotated_against": {},
    }
    assert all("channel_match" not in row for row in out_off["retrieval"]["candidates"])


def test_hard_mode_actually_filters(monkeypatch) -> None:
    """hard 真过滤 —— 留着它是因为显式调用方（外部/助手）可能知道自己只要某个渠道，
    但分析路径默认不用它。"""
    monkeypatch.setattr(settings, "similarity_filter_mode", "hard")
    out = retrieve_node(_state(source_type="web"))

    assert out["retrieval"]["filters"]["applied"] == {"channel": "web"}


# ── 为什么只接 soft：一条真实数据里的反例 ─────────────────────────────────


def test_hard_filtering_by_business_domain_would_drop_the_only_real_relation() -> None:
    """**把「四个维度都不可硬过滤」钉成可执行的反例。**

    库里唯一那条真实关系是 `REQ-000015(门店巡检) --related--> REQ-000002(报表导出)`，
    而这两条的 `business_domain` 分别是 **workflow** 与 **report**。

    也就是说：给 REQ-000015 那次检索加上 `business_domain=workflow` 硬过滤，
    **会把它唯一真实的关联需求滤掉** —— 剩下一条候选都没有，analyze 于是判「独立」，
    一个真实关联被静默丢失。这正是项目最怕的那类失败。

    另外两个维度更直接：`department` / `sensitivity_level` 在库里 **12/12 全是 NULL**，
    硬过滤会返回 0 条候选；`source_type` 12/12 全是 'web'，今天过滤是空操作，
    等飞书流量进来则会藏掉跨渠道的重复。

    ⚠️ 这条测试依赖库里那条关系仍然存在。若它被删了，说明**样本没了**而不是
    「结论变了」—— 那时请换一条真实样本重钉，别把断言删掉了事。
    """
    rows = RetrievalService().search(
        INSPECTION_SUMMARY, limit=10, filters={"business_domain": "workflow"}
    )
    keys = {row["requirement_key"] for row in rows}

    assert RELATED_KEY not in keys, (
        f"前提变了：{RELATED_KEY} 的 business_domain 可能已不是 report。"
        "请核对库里那条关系，而不是放松这条断言。"
    )

    unfiltered = {
        row["requirement_key"]
        for row in RetrievalService().search(INSPECTION_SUMMARY, limit=10)
    }
    assert RELATED_KEY in unfiltered, "不加过滤时它本来是被召回的 —— 所以过滤确实弄丢了东西"
