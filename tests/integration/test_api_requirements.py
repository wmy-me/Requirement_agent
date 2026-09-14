import json
import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

import pytest
from fastapi.testclient import TestClient

from requirement_agent.api.app import app


client = TestClient(app)


API_HEADERS = {"Authorization": "Bearer test-api-token"}


def _purge_sources(requester_id: str) -> None:
    """清掉本测试造出来的来源行。

    本文件打的是**真实库**。此前不清理，每跑一次测试待办列表就多一条「接口冒烟-」，
    实际累积到过 15 条。只删仍是 pending_review 的行，绝不碰已进入审核流程的数据。
    """
    from sqlalchemy import text

    from requirement_agent.infrastructure.db.session import SessionLocal

    with SessionLocal() as session:
        session.execute(
            text(
                "DELETE FROM requirement_source "
                "WHERE requester_id = :requester_id AND processing_status = 'pending_review'"
            ),
            {"requester_id": requester_id},
        )
        session.commit()


def test_submit_requirement_api(monkeypatch) -> None:
    from requirement_agent.config.settings import settings

    monkeypatch.setattr(settings.api_auth_token, "_secret_value", "test-api-token")
    # 唯一 key，避免命中历史幂等记录（持久 DB 可能导致旧源已 committed）
    unique = uuid.uuid4().hex
    requester = f"u-{unique}"
    try:
        response = client.post(
            "/api/v1/requirements/submit",
            headers=API_HEADERS,
            json={
                "source_type": "web",
                "requester_id": requester,
                "requester_name": "alice",
                "original_text": f"接口冒烟-{unique}：希望报表按部门筛选导出",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "pending_review"
        assert payload["source_type"] == "web"
    finally:
        # 按 requester 清，即使断言失败也清得干净
        _purge_sources(requester)


def test_export_requirements_csv_api() -> None:
    """导出接口冒烟（只读，不写库，因此不加唯一化数据）。"""
    response = client.get("/api/v1/requirements/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    raw = response.content
    assert raw[:3] == b"\xef\xbb\xbf", "缺少 UTF-8 BOM，Excel 打开中文会乱码"
    header = raw.decode("utf-8-sig").splitlines()[0]
    assert header.split(",")[0] == "需求编号"
    assert "来源渠道" in header


def test_pending_review_ids_are_strings_not_numbers() -> None:
    """待办的 source_id 必须以**字符串**下发，否则审核必然 409。

    雪花 id 超过 JS 的 Number.MAX_SAFE_INTEGER（2^53）。按 JSON number 下发时，
    前端 JSON.parse 会把它悄悄改写——实测 224103804432285696 → ...700、
    225111676653928448 → ...450——回传时后端只看到「不存在的 id」→ 409。
    字符串在 JS 里原样透传，后端 pydantic 再把数字字符串转回 int。
    """
    items = client.get("/api/v1/reviews/pending?limit=30").json()["items"]
    if not items:
        pytest.skip("库里没有待审核来源，跳过")

    for item in items:
        source_id = item["source_id"]
        assert isinstance(source_id, str), f"source_id 必须是字符串，实际是 {type(source_id).__name__}"
        assert int(source_id) > 0
        # 模拟 JS 侧：字符串经过 JSON 往返必须一字不改
        assert json.loads(json.dumps({"source_id": source_id}))["source_id"] == source_id


def test_requirement_relations_endpoints() -> None:
    """关系端点冒烟。

    不断言具体关系内容——那需要先审核通过一条与既有 REQ 相关的需求（会写库）。
    关系边的**生成规则**由 tests/unit/test_requirement_relation.py 覆盖。
    """
    response = client.get("/api/v1/requirements/REQ-000001/relations")

    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)

    # 裁决：不存在的 id → 404；非法 status（proposed 是系统初始态，不允许改回）→ 422
    assert client.patch(
        "/api/v1/requirements/relations/999999999", json={"status": "confirmed"}
    ).status_code == 404
    assert client.patch(
        "/api/v1/requirements/relations/999999999", json={"status": "proposed"}
    ).status_code == 422


def test_ops_outbox_status_endpoint() -> None:
    """运维端点冒烟：状态计数键必须齐全（缺失的状态补 0，前端不必处理 undefined）。"""
    response = client.get("/api/v1/ops/outbox")

    assert response.status_code == 200
    body = response.json()
    assert set(body["counts"]) == {"pending", "processing", "completed", "dead_letter", "discarded"}
    assert all(isinstance(value, int) for value in body["counts"].values())
    assert isinstance(body["dead_letters"], list)


def test_ops_dead_letter_actions_reject_unknown_id() -> None:
    """retry / discard 只认死信：不存在的 id 返回 404。

    这里断言 404 而非 200 —— 守护的是「误点重试不能把运行中或已完成的事件重置」
    （重置会导致 embedding 重复写、文档重复切片）。
    """
    for action in ("retry", "discard"):
        response = client.post(f"/api/v1/ops/outbox/dead-letters/999999999/{action}")

        assert response.status_code == 404, action


def test_request_logging_sets_request_id_header() -> None:
    """请求头 X-Request-ID 必须回写，便于把前端报错与服务端日志对上。"""
    generated = client.get("/api/v1/ops/outbox")
    assert generated.headers.get("X-Request-ID")

    echoed = client.get("/api/v1/ops/outbox", headers={"X-Request-ID": "trace-abc"})
    assert echoed.headers["X-Request-ID"] == "trace-abc"


def test_list_requirements_filter_narrows_results() -> None:
    """列表筛选冒烟：断言的是一条不依赖具体数据的性质（筛选只会收窄，不会放宽）。"""
    items = client.get("/api/v1/requirements").json()["items"]
    if not items:
        pytest.skip("库里没有已提交需求，跳过筛选断言")

    status = items[0]["status"]
    filtered = client.get("/api/v1/requirements", params={"status": status}).json()["items"]

    assert 0 < len(filtered) <= len(items)
    assert all(item["status"] == status for item in filtered)


def test_list_requirements_unknown_filter_returns_empty() -> None:
    items = client.get(
        "/api/v1/requirements", params={"status": "__definitely_not_a_status__"}
    ).json()["items"]

    assert items == []
