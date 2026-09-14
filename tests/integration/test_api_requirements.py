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
