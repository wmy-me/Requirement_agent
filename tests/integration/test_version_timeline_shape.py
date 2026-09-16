"""版本时间轴依赖的数据契约（F 批）。

时间轴本身是前端代码、没有自动化测试，但它踩在几个**后端字段**上：`status`、`sources`、
`parent_version_no`、`diff` 的 `from/to`。这些字段一旦没了或改名，前端会静默退化成
「看不出当前版」「来源链消失」——浏览器里才发现。所以在这里把它们钉住。

打真实库，用既有的 REQ-000015（3 个版本、每版都有来源）。
"""

from __future__ import annotations

import os

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

import pytest
from fastapi.testclient import TestClient

from requirement_agent.api.app import app

client = TestClient(app)

SAMPLE_KEY = "REQ-000015"

# `requirement_version.status` 的取值域（`migrations` 的 CHECK 约束同源）
VERSION_STATUSES = {"draft", "pending_review", "current", "superseded"}


def _versions() -> list[dict]:
    response = client.get(f"/api/v1/requirements/{SAMPLE_KEY}/versions")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _trace_versions() -> list[dict]:
    response = client.get(f"/api/v1/requirements/{SAMPLE_KEY}/trace")
    assert response.status_code == 200, response.text
    return response.json()["versions"]


@pytest.fixture(scope="module")
def versions():
    rows = _versions()
    if not rows:
        pytest.skip("样本需求没有版本数据")
    return rows


def test_versions_expose_status(versions) -> None:
    """**契约 §8.5 要求前端用 `status === 'current'` 判断当前版** —— 而这两个端点
    此前都没返回 `status`，前端只能靠「version_no 最大」猜，等于把那条约定写在空气里。
    时间轴要标出「当前版本」，没这个字段就只能猜。
    """
    for row in versions:
        assert "status" in row, "版本项缺少 status，时间轴无法标出当前版本"
        assert row["status"] in VERSION_STATUSES, row["status"]


def test_exactly_one_current_version(versions) -> None:
    """同主线只有一个 current（由部分唯一索引保证），时间轴据此判断「我现在在哪」。"""
    assert [row["status"] for row in versions].count("current") == 1


def test_versions_expose_timeline_fields(versions) -> None:
    """时间轴节点要用的字段：版本号、类型（配色）、摘要、前驱、变更清单。"""
    row = versions[0]
    for field in ("version_no", "change_type", "change_summary", "parent_version_no", "feature_changes"):
        assert field in row, f"版本项缺少 `{field}`"


def test_trace_versions_carry_sources() -> None:
    """**时间轴上的「← 来源 #…」是这条链唯一的真实跨实体关系。**

    注意：不是「版本合并了另一个 REQ 的版本」——本系统的合并是「来源 → REQ」，
    待合并的东西还不是 REQ（详见 `docs/方案_对话状态机与Git式版本管理.md` §3.3 的落地说明）。
    真实的溯源是「版本 ← 来源」，也就是这里。
    """
    rows = _trace_versions()
    if not rows:
        pytest.skip("样本需求没有版本数据")

    with_sources = [row for row in rows if row.get("sources")]
    assert with_sources, "trace 的版本一个来源都没有 —— 时间轴会画不出溯源链"

    source = with_sources[0]["sources"][0]
    for field in ("source_id", "source_type", "requester_name"):
        assert field in source, f"来源缺少 `{field}`，时间轴画不出来"
    assert isinstance(source["source_id"], str), "来源 ID 应为字符串（雪花，见 api-contract §1.1）"


def test_trace_versions_also_expose_status() -> None:
    """两个端点都要给 `status` —— 前端用哪个都能判断当前版，免得按端点分支处理。"""
    for row in _trace_versions():
        assert row.get("status") in VERSION_STATUSES, row


def test_diff_accepts_explicit_version_range(versions) -> None:
    """时间轴上的「从 V1 到 V3」下拉依赖这个：后端一直支持，但前端此前从不传参。"""
    response = client.get(
        f"/api/v1/requirements/{SAMPLE_KEY}/diff",
        params={"from_version": 1, "to_version": 3},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from_version"] == 1
    assert body["to_version"] == 3


def test_diff_defaults_to_adjacent_versions(versions) -> None:
    """不传参时的既有行为不能变（前端首屏走的就是这条）。"""
    response = client.get(f"/api/v1/requirements/{SAMPLE_KEY}/diff")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["to_version"] == max(row["version_no"] for row in versions)
    assert body["from_version"] == body["to_version"] - 1
