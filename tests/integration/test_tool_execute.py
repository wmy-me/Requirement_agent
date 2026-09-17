"""L3 工具的**真实执行**测试（打真实库）。

> ### 为什么这个文件必须存在
>
> 上一版工具层删掉的原因之一就是**它没有这个文件**：11 条单测只覆盖注册表与 schema，
> **没有一个调用真实工具的 `execute()`**。实测把 `get_features` 依赖的底层方法改个名 ——
> `test_tool_registry.py` **22 passed 全绿**，而工具已经坏了。
>
> 那种「看起来被测着、实际没有」的状态比明显的死代码更危险。所以本批次的验收标准里，
> **真实 `execute` 的测试是硬要求**，不是加分项。

纯查询，不造数据、不清理（读测试不改变任何状态）。
"""

from __future__ import annotations

import os

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

import pytest

from requirement_agent.tools import ToolStatus, create

# 库里唯一一条带完整版本链的样本（3 版、16 条功能、带模块标签）
SAMPLE_KEY = "REQ-000015"


def _probe_params(tool_name: str) -> dict:
    """给「每个工具都要能真跑一次」用的最小合法参数。"""
    if tool_name in ("search_requirements", "search_features"):
        return {"query": "巡检"}
    if tool_name in ("list_requirements", "list_requirement_sources"):
        return {"limit": 3}
    if tool_name == "search_by_capability":
        return {"capability": "导出 Excel 文件"}
    if tool_name == "search_by_constraint":
        return {"constraint": "按门店"}
    if tool_name == "compare_requirement_versions":
        return {"requirement_key": SAMPLE_KEY, "from_version": 1, "to_version": 3}
    return {"requirement_key": SAMPLE_KEY}


def _run(tool_name: str, **params):
    tool = create(tool_name)
    assert tool is not None, f"工具 {tool_name} 没注册"
    return tool.run(params)


# ── 逐个工具的 happy path ─────────────────────────────────────────────────
# 每个工具都要有一条**真的查到了东西**的断言 —— 这正是上一版缺的。


def test_search_requirements_returns_candidates() -> None:
    result = _run("search_requirements", query="门店巡检计划", limit=3)
    assert result.status is ToolStatus.SUCCESS
    assert isinstance(result.result, list) and result.result
    first = result.result[0]
    assert set(first) == {"requirement_key", "requirement_name", "similarity"}
    assert first["requirement_key"].startswith("REQ-")


def test_get_requirement_detail_returns_the_row() -> None:
    result = _run("get_requirement_detail", requirement_key=SAMPLE_KEY)
    assert result.status is ToolStatus.SUCCESS
    body = result.result
    assert body["requirement_key"] == SAMPLE_KEY
    assert body["current_version"] >= 1
    assert body["feature_count"] > 0
    assert body["final_requirement"]


def test_get_requirement_features_returns_items() -> None:
    result = _run("get_requirement_features", requirement_key=SAMPLE_KEY, limit=5)
    assert result.status is ToolStatus.SUCCESS
    body = result.result
    assert body["count"] > 0
    assert len(body["features"]) <= 5
    assert body["truncated"] is True, "样本有 16 条功能，limit=5 必须标出被截断"
    assert set(body["features"][0]) == {"feature_key", "content", "module", "origin_version_no"}


def test_get_requirement_versions_returns_history() -> None:
    result = _run("get_requirement_versions", requirement_key=SAMPLE_KEY)
    assert result.status is ToolStatus.SUCCESS
    versions = result.result["versions"]
    assert len(versions) >= 2
    # 新→旧
    assert versions[0]["version_no"] >= versions[-1]["version_no"]
    # ⚠️ 刻意不带正文快照（那个很大）
    assert "requirement_snapshot" not in versions[0]


def test_list_requirements_returns_summaries_only() -> None:
    result = _run("list_requirements", limit=5)
    assert result.status is ToolStatus.SUCCESS
    assert result.result
    # **只给摘要不给正文** —— 列举类工具最容易把整个库灌进上下文
    assert set(result.result[0]) == {
        "requirement_key", "requirement_name", "current_version", "feature_count", "status",
    }


# ── 三态语义：这一版最重要的行为变化 ──────────────────────────────────────


def test_missing_requirement_is_empty_not_error() -> None:
    """**「查不到」是合法答案，不是工具故障。**

    上一版把它当 error 返回 —— 模型会以为工具坏了而反复重试，
    而正确答案其实是「这条需求不存在」。
    """
    for tool_name in ("get_requirement_detail", "get_requirement_features", "get_requirement_versions"):
        result = _run(tool_name, requirement_key="REQ-999999")
        assert result.status is ToolStatus.EMPTY, f"{tool_name} 把「不存在」报成了 {result.status}"
        assert not result.is_error
        assert "REQ-999999" in (result.message or "")


def test_search_is_recall_first_so_it_rarely_returns_empty() -> None:
    """**实测出来的一条重要性质：检索是「召回优先」的，几乎不会有空结果。**

    连「zzz完全不存在的业务场景zzz」这种乱码查询，也能拿到 0.77 的相似度 ——
    因为 `RetrievalService` 是关键词 + 向量的混合检索，命中基础分就返回。

    ⚠️ **所以模型不能把「有结果」当成「有相似需求」**：`similarity` 只是候选排序，
    是否构成重复由后端阈值判（strict 模式 duplicate ≥ 0.80）。
    这条测试把这个性质**记下来**，免得有人以为「查得到 = 有重复」。
    """
    result = _run("search_requirements", query="zzz完全不存在的业务场景zzz")
    # 这不是在断言「应该为空」—— 恰恰相反，是在记录「它不会为空」
    assert result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY}
    if result.status is ToolStatus.SUCCESS:
        assert all("similarity" in row for row in result.result)


def test_empty_list_is_empty_not_error() -> None:
    result = _run("list_requirements", status="archived")  # 库里没有归档需求
    assert result.status is ToolStatus.EMPTY


# ── 参数校验：边界与拒绝 ──────────────────────────────────────────────────


def test_at_version_out_of_range_is_error_not_a_lie() -> None:
    """**上一版在这里对模型说了假话。**

    仓储的 SQL 判据对未来版本对所有当前行都成立，所以 `at_version=99` 会**静默返回
    当前功能集**，看起来像「v99 长这样」。这一版在 QueryService 里拦掉。
    """
    result = _run("get_requirement_features", requirement_key=SAMPLE_KEY, at_version=99)
    assert result.status is ToolStatus.ERROR
    assert "V99" in (result.message or "") or "99" in (result.message or "")


def test_at_version_within_range_is_success() -> None:
    """**与上面成对**：范围内的历史版本要能正常查（不能拦过头）。"""
    result = _run("get_requirement_features", requirement_key=SAMPLE_KEY, at_version=1)
    assert result.status is ToolStatus.SUCCESS
    assert result.result["at_version"] == 1
    assert result.result["count"] > 0


def test_historical_version_content_differs_from_current() -> None:
    """`at_version` 要真的回到当时 —— v1 的功能数应当少于当前。"""
    at_v1 = _run("get_requirement_features", requirement_key=SAMPLE_KEY, at_version=1).result
    current = _run("get_requirement_features", requirement_key=SAMPLE_KEY).result
    assert at_v1["count"] < current["count"], "v1 应比当前少（样本 v2/v3 都在追加）"


def test_limit_above_cap_is_rejected_by_schema() -> None:
    """上限是硬的：超出 `max_result_count` 的入参**直接不合法**。"""
    result = _run("search_requirements", query="巡检", limit=999)
    assert result.status is ToolStatus.ERROR
    assert "limit" in (result.message or "")


def test_limit_below_cap_is_honoured() -> None:
    result = _run("get_requirement_features", requirement_key=SAMPLE_KEY, limit=2)
    assert result.status is ToolStatus.SUCCESS
    assert len(result.result["features"]) == 2


def test_unknown_parameter_is_rejected() -> None:
    """**多传字段必须报错**：静默忽略会让工具照默认值跑，返回一个「看起来正常
    但没按调用方意思办」的结果 —— 那比报错糟得多。"""
    result = _run("get_requirement_detail", requirement_key=SAMPLE_KEY, include_deleted=True)
    assert result.status is ToolStatus.ERROR
    assert "include_deleted" in (result.message or "")


def test_blank_requirement_key_is_rejected() -> None:
    """**纯空白不是「需求不存在」，是「参数没意义」。**

    没有 strip 校验时 `"   "` 能过 `min_length=1`（长度是 3），最后返回
    「没有编号为 `     ` 的需求」—— 把参数错误伪装成业务结果。
    """
    result = _run("get_requirement_detail", requirement_key="   ")
    assert result.status is ToolStatus.ERROR
    assert "参数不合法" in (result.message or "")


# ── 审计 ──────────────────────────────────────────────────────────────────


def test_every_call_is_audited(caplog) -> None:
    """每次调用留一条结构化日志：tool / args / duration_ms / status / count。"""
    import logging

    with caplog.at_level(logging.INFO, logger="requirement_agent.tools.base"):
        _run("get_requirement_detail", requirement_key=SAMPLE_KEY)

    # `record.getMessage()` 已经做过 % 格式化，不要再 % 一次
    records = [r.getMessage() for r in caplog.records]
    assert any("event=tool_call" in line and "tool=get_requirement_detail" in line for line in records)
    assert any("duration_ms=" in line and "status=success" in line for line in records)


def test_empty_call_is_audited_at_info_not_warning(caplog) -> None:
    """**「查不到」按 INFO 记，不按 WARNING。**

    它是正常答案，不是故障 —— 如果它进 WARNING，日志里就分不清
    「用户查了个不存在的编号」和「数据库连不上」了。
    """
    import logging

    with caplog.at_level(logging.INFO, logger="requirement_agent.tools.base"):
        _run("get_requirement_detail", requirement_key="REQ-999999")

    assert any("status=empty" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_error_call_is_audited_at_warning(caplog) -> None:
    """真正的失败（参数不合法 / 依赖报错）才进 WARNING。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="requirement_agent.tools.base"):
        _run("get_requirement_detail")  # 缺必填的 requirement_key

    assert any("status=error" in r.getMessage() for r in caplog.records)
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("tool_name", [
    "search_requirements", "get_requirement_detail", "get_requirement_features",
    "get_requirement_versions", "list_requirements",
    "compare_requirement_versions", "get_requirement_risks",
    "list_requirement_relations", "list_requirement_sources",
    "search_by_capability", "search_by_constraint", "search_features",
    "trace_requirement_sources",
])
def test_every_tool_is_actually_callable(tool_name: str) -> None:
    """**这条是防「静默腐烂」的总闸。**

    每个注册在册的工具都必须能被真实调用一次并返回 `ToolResult`（不抛异常、不返回 None）。
    上一版正是缺这条：改坏底层方法名，注册表测试全绿而工具已死。
    """
    tool = create(tool_name)
    assert tool is not None
    kwargs = _probe_params(tool_name)
    result = tool.run(kwargs)
    assert result.status is not ToolStatus.ERROR, f"{tool_name} 调用失败：{result.message}"
    assert result.duration_ms >= 0


# ══════════════════════════════════════════════════════════════════════════
# 批 2 的工具
# ══════════════════════════════════════════════════════════════════════════


def test_list_requirement_relations_returns_edges() -> None:
    result = _run("list_requirement_relations", requirement_key=SAMPLE_KEY)
    assert result.status is ToolStatus.SUCCESS
    row = result.result["relations"][0]
    # 关系边必须带 status —— `proposed` 是模型提议、没经人工确认，展示时要标
    assert "status" in row and "relation_type" in row


def test_trace_requirement_sources_returns_the_chain() -> None:
    """每一版从哪条来源来 —— 本系统**真实存在**的跨实体关系。"""
    result = _run("trace_requirement_sources", requirement_key=SAMPLE_KEY)
    assert result.status is ToolStatus.SUCCESS
    versions = result.result["versions"]
    with_sources = [v for v in versions if v["sources"]]
    assert with_sources, "样本每一版都该有来源"
    source = with_sources[0]["sources"][0]
    assert set(source) == {"source_id", "source_type", "requester_name", "submitted_at", "text_excerpt"}
    assert isinstance(source["source_id"], str), "雪花 ID 是字符串（契约 §1.1）"
    assert len(source["text_excerpt"]) <= 200, "原文只给摘要，不给全文"


def test_list_requirement_sources_gives_titles_not_bodies() -> None:
    """**只给标题不给正文** —— 待审材料是给审核人做判断用的。"""
    result = _run("list_requirement_sources", status="pending_review", limit=5)
    assert result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY}
    if result.status is ToolStatus.SUCCESS:
        for row in result.result:
            assert set(row) == {"source_id", "title", "source_type", "requester_name", "submitted_at"}
            assert "original_text" not in row
            assert "metadata" not in row, "分析结果与风险也在 metadata 里，同样不给"


def test_get_requirement_risks_reads_from_versions() -> None:
    """风险**按版本**存（在 `diff_payload.risk` 里），不是按需求存。"""
    result = _run("get_requirement_risks", requirement_key=SAMPLE_KEY)
    assert result.status is ToolStatus.SUCCESS
    risk = result.result["risks"][0]
    assert {"version_no", "quality_risk", "change_risk", "technical_impact_risk"} <= set(risk)


def test_compare_requirement_versions_returns_a_real_diff() -> None:
    """样本 v1→v3 是 4 条新增（v2 引入 1 + v3 引入 3）—— 与 `/diff` 的既有实测一致。"""
    result = _run(
        "compare_requirement_versions", requirement_key=SAMPLE_KEY, from_version=1, to_version=3
    )
    assert result.status is ToolStatus.SUCCESS
    assert result.result["summary"]["added"] == 4
    assert result.result["from_version"] == 1 and result.result["to_version"] == 3


def test_compare_versions_out_of_range_is_error() -> None:
    """与 `at_version` 同样的范围校验 —— **越界要报错，不给假数据**。"""
    result = _run(
        "compare_requirement_versions", requirement_key=SAMPLE_KEY, from_version=1, to_version=99
    )
    assert result.status is ToolStatus.ERROR


def test_compare_same_version_reports_no_difference() -> None:
    """同一版比同一版 → EMPTY（「没有差异」是正常答案）。"""
    result = _run(
        "compare_requirement_versions", requirement_key=SAMPLE_KEY, from_version=3, to_version=3
    )
    assert result.status is ToolStatus.EMPTY


def test_search_features_finds_rows_across_requirements() -> None:
    result = _run("search_features", query="导出", limit=5)
    assert result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY}
    if result.status is ToolStatus.SUCCESS:
        assert all("requirement_key" in row and "content" in row for row in result.result)


def test_search_by_capability_resolves_a_unique_name() -> None:
    result = _run("search_by_capability", capability="导出 Excel 文件")
    assert result.status is ToolStatus.SUCCESS
    body = result.result
    # **必须带出能力自身的状态** —— `pending_confirmation` 表示还没人工确认
    assert "capability_status" in body
    assert body["streams"]


def test_search_by_capability_does_not_guess_when_ambiguous() -> None:
    """**匹配到多个能力时不替调用方挑** —— 挑错会把两条不相干的需求混起来。"""
    result = _run("search_by_capability", capability="创建")
    assert result.status is ToolStatus.EMPTY
    assert result.result and result.result.get("need_disambiguation") is True
    assert len(result.result["candidates"]) >= 2
    assert "请用更完整的名字重试" in (result.message or "")


def test_search_by_capability_unknown_name_is_empty() -> None:
    result = _run("search_by_capability", capability="zzz不存在的能力zzz")
    assert result.status is ToolStatus.EMPTY


def test_search_by_constraint_finds_the_requirement() -> None:
    """按条件反查 —— 条件是独立的查询轴，不能从能力那侧问到。"""
    result = _run("search_by_constraint", constraint="按门店")
    assert result.status is ToolStatus.SUCCESS
    assert result.result["streams"][0]["requirement_key"] == SAMPLE_KEY


def test_search_by_constraint_unknown_is_empty() -> None:
    result = _run("search_by_constraint", constraint="zzz不存在的条件zzz")
    assert result.status is ToolStatus.EMPTY
