"""前端工作台依赖的 6 个端点（2026-09-17 新增）。

写这批之前做了「界面 → 接口」对账，发现 6 个端点不存在、对应界面做不出来：

    /stats/overview          整个总览模块（前端拉全量自算不可行）
    /sources                 来源中心的列表（此前只有按 id 溯源）
    /reviews/history         审核中心的「历史」（此前只有 pending）
    /ops/models              运维的模型调用页（**表早已就绪，只缺端点**）
    /ops/worker              Worker 状态（此前只有 outbox 计数）
    /agent/runs 去掉必填      智能分析的任务列表

它们全部是**读已存在的数据**，不新增表、不改既有端点。

这个文件除了钉形状，还钉两条**口径**（那才是容易错的地方）：
① 风险/冲突只统计**分析过的**来源，分母如实给出；
② 审核历史的默认状态**不含 `returned`**（它不是终态）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.infrastructure.db.session import SessionLocal


@pytest.fixture
def client() -> TestClient:
    from requirement_agent.api.app import app

    # conftest 已设 API_AUTH_TOKEN=test-api-token（B1 起受保护端点都要鉴权）
    return TestClient(app, headers={"Authorization": "Bearer test-api-token"})


# ── ① /stats/overview ─────────────────────────────────────────────────────


def test_overview_returns_both_scalars_and_groups(client: TestClient) -> None:
    """总览页要的数一次拿全：既有标量，也有分组。"""
    r = client.get("/api/v1/stats/overview")

    assert r.status_code == 200
    body = r.json()
    for key in (
        "pending_review", "high_risk", "conflict", "dead_letter",
        "requirements_total", "sources_total", "analysed_sources",
    ):
        assert isinstance(body[key], int), key
    for key in ("source_status_counts", "channel_counts", "domain_counts",
                "risk_matrix", "submission_trend"):
        assert isinstance(body[key], (dict, list)), key


def test_overview_exposes_the_denominator_for_risk_stats(client: TestClient) -> None:
    """**风险与冲突是从 `metadata` 读的，只覆盖已分析的来源。**

    所以必须同时给出 `analysed_sources` 作分母 —— 否则「高危 6 条」会被读成
    「全库有 6 条高危」，而实际只统计了分析过的那部分。
    """
    body = client.get("/api/v1/stats/overview").json()

    assert body["analysed_sources"] <= body["sources_total"], (
        "已分析的不可能多于来源总数"
    )


def test_overview_status_counts_sum_to_the_total(client: TestClient) -> None:
    """分组计数必须与总数自洽 —— 对不上说明有一条查询的口径写错了。"""
    body = client.get("/api/v1/stats/overview").json()

    assert sum(body["source_status_counts"].values()) == body["sources_total"]
    assert sum(body["channel_counts"].values()) == body["sources_total"]


def test_overview_trend_is_chronological(client: TestClient) -> None:
    """趋势按时间**正序**返回，前端可以直接画折线，不用自己再翻一遍。"""
    body = client.get("/api/v1/stats/overview").json()
    periods = [point["period"] for point in body["submission_trend"]]

    assert periods == sorted(periods)


# ── ② /sources ────────────────────────────────────────────────────────────


def test_sources_list_shape(client: TestClient) -> None:
    r = client.get("/api/v1/sources", params={"limit": 5})

    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "库里应当有来源"
    first = items[0]
    assert set(first) == {
        "source_id", "source_type", "requester_name", "processing_status",
        "error_message", "excerpt", "linked_requirement_key",
        "submitted_at", "updated_at",
    }
    # 雪花 id 一律字符串（契约 §1.1）
    assert isinstance(first["source_id"], str)


def test_sources_list_does_not_carry_the_full_text(client: TestClient) -> None:
    """列表带的是**摘要**（前 200 字），不是全文。

    全文可能上千字，而列表每次几十条 —— 整段带走会让 payload 膨胀几个数量级。
    详情走 `/reviews/{source_id}/detail`。
    """
    items = client.get("/api/v1/sources", params={"limit": 20}).json()["items"]

    for item in items:
        assert len(item["excerpt"]) <= 200, item["source_id"]
        assert "metadata" not in item, "列表不该带 metadata（可能几十 KB）"


def test_sources_list_filters_by_status(client: TestClient) -> None:
    r = client.get("/api/v1/sources", params={"status": ["committed"], "limit": 50})

    assert r.status_code == 200
    assert all(x["processing_status"] == "committed" for x in r.json()["items"])


def test_sources_list_resolves_the_linked_requirement(client: TestClient) -> None:
    """`linked_requirement_key` 表示**这条来源最终变成了哪条需求**。

    它由 `requirement_version_source` 左连接取到；没入库的来源是 `null`
    —— 那不是错误，是「还没变成需求」。
    """
    items = client.get("/api/v1/sources", params={"limit": 50}).json()["items"]
    committed = [x for x in items if x["processing_status"] == "committed"]

    assert committed, "库里应当有已入库的来源"
    assert all(x["linked_requirement_key"] for x in committed), (
        "已 committed 的来源必须能追到需求 —— 追不到说明版本链的关联断了"
    )


# ── ③ /reviews/history ────────────────────────────────────────────────────


def test_history_defaults_to_terminal_statuses_only(client: TestClient) -> None:
    """默认口径是三种**终态**，**不含 `returned`**。

    退回修改不是终态 —— 来源会重新进入分析流程。把它列进「历史」会让人以为
    那件事已经结束了。
    """
    items = client.get("/api/v1/reviews/history", params={"limit": 50}).json()["items"]

    assert items, "库里应当有已审来源"
    assert {x["processing_status"] for x in items} <= {"approved", "rejected", "committed"}
    assert "returned" not in {x["processing_status"] for x in items}


def test_history_does_not_include_pending(client: TestClient) -> None:
    """待审的在 `/reviews/pending`，不该同时出现在历史里。"""
    items = client.get("/api/v1/reviews/history", params={"limit": 200}).json()["items"]

    assert all(x["processing_status"] != "pending_review" for x in items)


# ── ④ /ops/models ─────────────────────────────────────────────────────────


def test_models_endpoint_returns_records_and_routing_diagnostics(
    client: TestClient,
) -> None:
    """模型调用记录 + **路由配置诊断**。

    `routing.unrecognized` 回答「我明明配了怎么没生效」—— 配置是手写 JSON，
    写错键名是常事，而这个诊断让排查不必靠读代码。
    """
    r = client.get("/api/v1/ops/models", params={"limit": 5})

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["items"], list)
    assert set(body["routing"]) == {"unrecognized", "resolved"}
    for task in ("extract", "analyze", "risk", "narrative", "embedding", "vision"):
        assert task in body["routing"]["resolved"]


def test_models_routing_resolved_matches_the_registry(client: TestClient) -> None:
    """`resolved` 报的是**当前实际会用的**模型，不是配置里的草稿。"""
    from requirement_agent.infrastructure.llm.model_registry import ModelRegistry

    body = client.get("/api/v1/ops/models").json()
    registry = ModelRegistry()

    assert body["routing"]["resolved"]["analyze"]["model"] == registry.get_chat_model("analyze").model


# ── ⑤ /ops/worker ─────────────────────────────────────────────────────────


def test_worker_endpoint_reports_stale_processing(client: TestClient) -> None:
    """**`stale_processing` 是这里最要紧的一个数。**

    只看各状态计数看不出来「消费者崩了」：它认领的行会永远停在 `processing`，
    队列看上去「有在干活」。超过阈值没收尾的才是卡住的。
    """
    r = client.get("/api/v1/ops/worker")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["stale_processing"], int)
    assert body["stale_timeout_seconds"] > 0
    assert set(body["counts"]) >= {"pending", "processing", "completed", "dead_letter"}


def test_worker_consumer_may_be_null_and_that_is_not_a_lie(client: TestClient) -> None:
    """测试进程里没有内嵌消费循环 —— 该字段就该是 `null`，**不伪造一份全零统计**。

    「没跑」和「跑了但没干活」是两回事。`/ops/worker` 的 docstring 也说明了：
    判断系统有没有在消费要看队列是否推进，不能只看这个字段（生产形态是独立 Worker）。
    """
    body = client.get("/api/v1/ops/worker").json()

    assert body["consumer"] is None or isinstance(body["consumer"], dict)


# ── ⑥ /agent/runs 的两种用法 ──────────────────────────────────────────────


def test_runs_can_be_listed_without_a_source_id(client: TestClient) -> None:
    """不带 `source_id` → 「最近都跑过什么」（智能分析的任务列表）。

    这里**原先会 422**（强制要求 source_id）。那个限制在只有「按来源反查」时
    成立，但一旦有了任务列表页，它就从「防误用」变成了「做不到」。
    """
    r = client.get("/api/v1/agent/runs", params={"limit": 5})

    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)


def test_runs_still_filters_by_source_when_given(client: TestClient) -> None:
    """带 `source_id` 时行为不变 —— 老调用方不受影响。"""
    with SessionLocal() as session:
        source_id = session.execute(
            text("SELECT id FROM requirement_source ORDER BY id LIMIT 1")
        ).scalar()

    r = client.get("/api/v1/agent/runs", params={"source_id": int(source_id), "limit": 5})

    assert r.status_code == 200
    for run in r.json()["items"]:
        assert run["source_id"] == int(source_id)
