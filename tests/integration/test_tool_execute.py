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


def _a_pending_source_id() -> str:
    """取一条真实待审来源的编号（预览/重匹配类工具需要它）。"""
    from requirement_agent.infrastructure.db.repositories import RequirementSourceRepository

    rows = RequirementSourceRepository().list_by_status("pending_review", limit=1)
    return str(rows[0]["source_id"]) if rows else "1"


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
    if tool_name == "preview_merge_impact":
        return {"source_id": _a_pending_source_id(), "target_requirement_key": SAMPLE_KEY}
    if tool_name == "match_capabilities":
        return {"source_id": _a_pending_source_id()}
    return {"requirement_key": SAMPLE_KEY}


def _run(tool_name: str, **params):
    tool = create(tool_name)
    assert tool is not None, f"工具 {tool_name} 没注册"
    return tool.run(params)


# ── 逐个工具的 happy path ─────────────────────────────────────────────────
# 每个工具都要有一条**真的查到了东西**的断言 —— 这正是上一版缺的。


def test_search_requirements_returns_candidates() -> None:
    """检索工具的返回形状。

    ⚠️ **这条依赖真实 embedding 网关**，所以它有两种合法的结局：
    网关可用 → 向量召回有结果；网关抖动 → `search_by_vector` 记一条
    `vector_recall_fallback` 并退化关键词，而查询词「门店巡检计划」在库里的文本
    （REQ-000015 是「门店巡检管理**系统**」）里**没有**这个字面，于是**一条都召不回**。

    第二种情况以前会让这条测试变红，看起来像代码坏了 —— 其实是降级路径正常工作。
    所以这里**显式分开处理**：降级时要断言「是干净的空结果，不是错误」。
    """
    result = _run("search_requirements", query="门店巡检计划", limit=3)

    assert result.status in (ToolStatus.SUCCESS, ToolStatus.EMPTY), (
        f"检索永不返回 ERROR —— 没命中是合法答案，实际 {result.status}"
    )
    if result.status is ToolStatus.EMPTY:
        assert result.result in ([], None), "降级路径的空结果必须是干净的"
        return

    assert isinstance(result.result, list) and result.result
    first = result.result[0]
    # **只给余弦，不给排序分。** 排序分（`retrieval_score`/`score`）是管道内部的东西，
    # 递给模型只会让它拿一个「叫 similarity 却不是相似度」的数下结论。
    # `source_types` 是来路，`soft` 模式的渠道标注直接复用它（B4 批 4）。
    assert set(first) == {
        "requirement_key",
        "requirement_name",
        "vector_similarity",
        "source_types",
    }
    assert first["requirement_key"].startswith("REQ-")
    # 不断言非空：纯关键词命中的候选没有余弦，那是合法的 `None`
    assert first["vector_similarity"] is None or 0.0 <= first["vector_similarity"] <= 1.0


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

    连「zzz完全不存在的业务场景zzz」这种乱码查询也能拿到候选，而且余弦在 **0.7 上下**。

    ⚠️ **那个 0.7 不是「还行」，它就是噪声基线。** B4 批 1 校准出无关中文业务文本的
    余弦中位是 **0.7273**（`SIMILARITY_BASELINE`）—— 也就是说「随便什么中文业务文本
    两两相比」本来就有 0.7 左右，这个模型的余弦空间是**压缩**的。

    这正是第一版绝对阈值（duplicate ≥ 0.80）失败的原因：0.80 只比噪声中心高 0.07，
    实测有 2/7 的无关文本对直接越线。判定因此改成了 relevance + contrast 两把锁。

    ⚠️ **所以模型不能把「有结果」当成「有相似需求」。** 这条测试把这个性质记下来，
    免得有人以为「查得到 = 有重复」。
    """
    result = _run("search_requirements", query="zzz完全不存在的业务场景zzz")
    # 这不是在断言「应该为空」—— 恰恰相反，是在记录「它不会为空」
    assert result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY}
    if result.status is ToolStatus.SUCCESS:
        assert all("vector_similarity" in row for row in result.result)
        cosines = [r["vector_similarity"] for r in result.result if r["vector_similarity"] is not None]
        if cosines:
            assert max(cosines) > 0.6, "乱码查询也会拿到贴近噪声基线的候选 —— 这就是召回优先"


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
    "match_capabilities", "preview_merge_impact",
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


# ══════════════════════════════════════════════════════════════════════════
# B2 第二批：两个「预演」工具
# ══════════════════════════════════════════════════════════════════════════


def test_match_capabilities_returns_real_match() -> None:
    """拿真实来源的抽取候选，对当前词表重匹配。"""
    result = _run("match_capabilities", source_id=_a_pending_source_id())
    assert result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY}
    if result.status is ToolStatus.SUCCESS:
        assert result.result["capabilities"]
        assert "business_object" in result.result


def test_match_capabilities_unknown_source_is_empty() -> None:
    result = _run("match_capabilities", source_id="999999999")
    assert result.status is ToolStatus.EMPTY


def test_match_capabilities_writes_nothing() -> None:
    """**这条是这个工具存在的意义所在。**

    `CapabilityMatchService.match` 的默认参数是 `persist=True` —— 它会**真的往库里
    写能力提案**（`capability` 行 + `feature_capability` 关联）。做成只读工具必须显式
    传 `persist=False`，否则就是「在查询的名义下写库」。

    这里直接数一下调用前后的行数。
    """
    from sqlalchemy import text

    from requirement_agent.infrastructure.db.session import SessionLocal

    def counts() -> tuple[int, int]:
        with SessionLocal() as session:
            caps = session.execute(text("SELECT count(*) FROM capability")).scalar_one()
            links = session.execute(text("SELECT count(*) FROM feature_capability")).scalar_one()
        return int(caps), int(links)

    before = counts()
    _run("match_capabilities", source_id=_a_pending_source_id())
    assert counts() == before, "预演工具写了库 —— persist=False 没传"


def test_preview_merge_impact_union_shows_no_deletion() -> None:
    result = _run("preview_merge_impact", source_id=_a_pending_source_id(),
                  target_requirement_key=SAMPLE_KEY, merge_mode="union")
    assert result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY}
    if result.status is ToolStatus.SUCCESS:
        assert result.result["summary"]["delete"] == 0


def test_preview_merge_impact_replace_warns_about_deletion() -> None:
    """**`replace` 的删除清单只能在这里看到** —— 必须带 warnings。"""
    result = _run("preview_merge_impact", source_id=_a_pending_source_id(),
                  target_requirement_key=SAMPLE_KEY, merge_mode="replace")
    if result.status is not ToolStatus.SUCCESS:
        pytest.skip("样本来源已不在待审，跳过")
    summary = result.result["summary"]
    if summary["delete"]:
        assert any("失去" in w for w in result.result["warnings"]), "replace 删除必须给出告警"


def test_preview_merge_impact_unknown_source_is_empty() -> None:
    result = _run("preview_merge_impact", source_id="999999999",
                  target_requirement_key=SAMPLE_KEY)
    assert result.status is ToolStatus.EMPTY


def test_preview_merge_impact_unknown_target_is_empty() -> None:
    result = _run("preview_merge_impact", source_id=_a_pending_source_id(),
                  target_requirement_key="REQ-999999")
    assert result.status is ToolStatus.EMPTY


def test_preview_merge_impact_writes_nothing() -> None:
    """**「预览不撒谎」的另一半：预览也不落库。** 项目里有测试钉着「预览与落库逐条一致」，
    这里补的是「预览本身一行不写」。"""
    from sqlalchemy import text

    from requirement_agent.infrastructure.db.session import SessionLocal

    def counts() -> tuple[int, int, int]:
        with SessionLocal() as session:
            v = session.execute(text("SELECT count(*) FROM requirement_version")).scalar_one()
            f = session.execute(text("SELECT count(*) FROM requirement_feature")).scalar_one()
            m = session.execute(text("SELECT count(*) FROM requirement_master")).scalar_one()
        return int(v), int(f), int(m)

    before = counts()
    _run("preview_merge_impact", source_id=_a_pending_source_id(),
         target_requirement_key=SAMPLE_KEY, merge_mode="replace")
    assert counts() == before, "预览写了库"


def test_preview_merge_impact_rejects_bad_mode() -> None:
    """`merge_mode` 只接受 union / replace —— 别的值在 schema 层就被拒。"""
    result = _run("preview_merge_impact", source_id=_a_pending_source_id(),
                  target_requirement_key=SAMPLE_KEY, merge_mode="explode")
    assert result.status is ToolStatus.ERROR
    assert "merge_mode" in (result.message or "")
