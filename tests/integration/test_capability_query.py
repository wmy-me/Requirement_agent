"""按能力/条件反查需求主线（方案批次 4）。

覆盖方案 §6 的前两类搜索：
1. 按能力搜 —— 命中所有具备该能力的需求主线
2. 按能力 + 条件搜 —— 只命中当前版本同时带该条件的主线

打真实库，用完自清理。
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.api.app import app
from requirement_agent.infrastructure.db.session import SessionLocal

client = TestClient(app)


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


class _QueryFixture:
    """一条带能力关联与版本快照的需求主线，用完删干净。"""

    def __init__(self, *, constraint_snapshot: list | None = None) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.master_id = _new_id()
        self.key = f"REQ-CQ-{self.tag}"
        self.feature_id = _new_id()
        self.action = f"导出{self.tag}"
        self.capability_id = _new_id()
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :key, :name, 'x', 1, 'active', 1)"
                ),
                {"id": self.master_id, "key": self.key, "name": f"查询测试-{self.tag}"},
            )
            session.execute(
                text(
                    "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                    "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                    "provenance, content_hash) "
                    "VALUES (:id, :rid, 'F-001', '支持导出 Excel', 'active', 1, NULL, :rk, 1, "
                    "CAST('[]' AS JSONB), 'h')"
                ),
                {"id": self.feature_id, "rid": self.master_id, "rk": self.key},
            )
            session.execute(
                text(
                    "INSERT INTO capability (id, action, object, display_name, status, created_by) "
                    "VALUES (:id, :a, 'Excel', :d, 'active', 'test')"
                ),
                {"id": self.capability_id, "a": self.action, "d": f"{self.action} Excel"},
            )
            session.execute(
                text(
                    "INSERT INTO feature_capability (feature_id, capability_id, raw_text, review_status) "
                    "VALUES (:f, :c, '支持导出 Excel', 'proposed')"
                ),
                {"f": self.feature_id, "c": self.capability_id},
            )
            session.execute(
                text(
                    "INSERT INTO requirement_version (id, requirement_id, version_no, version_title, "
                    "change_type, requirement_snapshot, change_summary, created_by, reviewed_by, "
                    "status, capability_snapshot, constraint_snapshot) "
                    "VALUES (:id, :rid, 1, 't', 'new', 's', 'c', 'a', 'a', 'current', "
                    "CAST('[]' AS JSONB), CAST(:cs AS JSONB))"
                ),
                {
                    "id": _new_id(),
                    "rid": self.master_id,
                    "cs": __import__("json").dumps(constraint_snapshot or []),
                },
            )
            session.commit()

    def set_link_status(self, status: str) -> None:
        with SessionLocal() as session:
            session.execute(
                text(
                    "UPDATE feature_capability SET review_status = :s "
                    "WHERE feature_id = :f AND capability_id = :c"
                ),
                {"s": status, "f": self.feature_id, "c": self.capability_id},
            )
            session.commit()

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text(
                    "DELETE FROM feature_capability WHERE capability_id = :c"),
                {"c": self.capability_id},
            )
            session.execute(
                text("DELETE FROM requirement_version WHERE requirement_id = :id"),
                {"id": self.master_id},
            )
            session.execute(
                text("DELETE FROM requirement_feature WHERE requirement_id = :id"),
                {"id": self.master_id},
            )
            session.execute(
                text("DELETE FROM requirement_master WHERE id = :id"), {"id": self.master_id}
            )
            session.execute(
                text("DELETE FROM capability WHERE id = :id"), {"id": self.capability_id}
            )
            session.commit()


# ── 搜索 1：按能力 ──────────────────────────────────────────────────────


def test_search_streams_by_capability() -> None:
    fixture = _QueryFixture()
    try:
        response = client.get(f"/api/v1/capabilities/{fixture.capability_id}/streams")

        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["requirement_key"] for item in items] == [fixture.key]
        assert items[0]["display_name"] == f"{fixture.action} Excel"
        assert items[0]["review_status"] == "proposed"  # 如实告知：还没人工确认
        assert items[0]["current_version"] == 1
    finally:
        fixture.cleanup()


def test_search_streams_filters_by_review_status() -> None:
    """`confirmed` 才是可信档；proposed 时按 confirmed 过滤应该搜不到。"""
    fixture = _QueryFixture()
    try:
        url = f"/api/v1/capabilities/{fixture.capability_id}/streams"

        assert client.get(url, params={"review_status": "confirmed"}).json()["items"] == []
        assert len(client.get(url, params={"review_status": "proposed"}).json()["items"]) == 1

        fixture.set_link_status("confirmed")

        assert len(client.get(url, params={"review_status": "confirmed"}).json()["items"]) == 1
        assert client.get(url, params={"review_status": "proposed"}).json()["items"] == []
    finally:
        fixture.cleanup()


def test_unknown_capability_returns_404() -> None:
    assert client.get("/api/v1/capabilities/999999999/streams").status_code == 404


def test_invalid_review_status_returns_422() -> None:
    fixture = _QueryFixture()
    try:
        response = client.get(
            f"/api/v1/capabilities/{fixture.capability_id}/streams",
            params={"review_status": "whatever"},
        )
        assert response.status_code == 422
    finally:
        fixture.cleanup()


# ── 搜索 2：能力 + 条件 ─────────────────────────────────────────────────


def test_search_streams_by_capability_and_constraint() -> None:
    fixture = _QueryFixture(
        constraint_snapshot=[{"raw": "按部门筛选", "constraint_key": None, "matched": False}]
    )
    try:
        url = f"/api/v1/capabilities/{fixture.capability_id}/streams"

        assert len(client.get(url, params={"constraint": "按部门筛选"}).json()["items"]) == 1
        assert client.get(url, params={"constraint": "按门店筛选"}).json()["items"] == []
    finally:
        fixture.cleanup()


def test_constraint_filter_matches_formal_key_too() -> None:
    """命中词表的正式键，与未命中的原文，都应该被搜到。"""
    fixture = _QueryFixture(
        constraint_snapshot=[
            {"raw": "按部门维度筛选", "constraint_key": "按部门筛选", "matched": True}
        ]
    )
    try:
        url = f"/api/v1/capabilities/{fixture.capability_id}/streams"

        assert len(client.get(url, params={"constraint": "按部门筛选"}).json()["items"]) == 1
        assert len(client.get(url, params={"constraint": "按部门维度筛选"}).json()["items"]) == 1
    finally:
        fixture.cleanup()


def test_constraint_filter_excludes_streams_without_that_constraint() -> None:
    fixture = _QueryFixture(constraint_snapshot=[])
    try:
        response = client.get(
            f"/api/v1/capabilities/{fixture.capability_id}/streams",
            params={"constraint": "按部门筛选"},
        )
        assert response.json()["items"] == []
    finally:
        fixture.cleanup()


# ── 需求侧：某主线的能力与条件 ──────────────────────────────────────────


def test_requirement_capabilities_endpoint() -> None:
    fixture = _QueryFixture(
        constraint_snapshot=[{"raw": "按门店", "constraint_key": None, "matched": False}]
    )
    try:
        response = client.get(f"/api/v1/requirements/{fixture.key}/capabilities")

        assert response.status_code == 200
        body = response.json()
        assert body["requirement_key"] == fixture.key
        assert body["current_version"] == 1
        assert len(body["capabilities"]) == 1
        assert body["capabilities"][0]["review_status"] == "proposed"
        assert body["capabilities"][0]["feature_key"] == "F-001"
        assert body["constraints"] == [{"raw": "按门店", "constraint_key": None, "matched": False}]
    finally:
        fixture.cleanup()


def test_requirements_capabilities_unknown_key_returns_404() -> None:
    assert client.get("/api/v1/requirements/REQ-NOPE-9999/capabilities").status_code == 404


def test_soft_deleted_feature_disappears_from_capabilities() -> None:
    """功能被软删后，它带来的能力不再出现在当前视图 —— 复用 feature 的生命周期。"""
    fixture = _QueryFixture()
    try:
        with SessionLocal() as session:
            session.execute(
                text("UPDATE requirement_feature SET status='deleted' WHERE id=:id"),
                {"id": fixture.feature_id},
            )
            session.commit()

        body = client.get(f"/api/v1/requirements/{fixture.key}/capabilities").json()
        assert body["capabilities"] == []
    finally:
        fixture.cleanup()
