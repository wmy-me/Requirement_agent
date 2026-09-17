"""能力裁决端点（方案批次 5 —— 人工确认后正式写入）。

本文件钉的是**职责边界的最后一段**：AI 只能写 `proposed` / `pending_confirmation`，
把它们翻成 `confirmed` / `active` **只有人能走的那条路**。

最关键的一条：**确认一条能力后，它才参与后续匹配**。
在此之前，AI 提议的新能力对后续需求而言等于不存在。
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.api.app import app
from requirement_agent.application.capability_match_service import CapabilityMatchService
from requirement_agent.infrastructure.db.session import SessionLocal

# B1 起 API 需要鉴权：`API_AUTH_TOKEN` 是兼容入口，等同于一个 admin token。
# 测具体的 401/403 行为请另建不带头的 TestClient（见 tests/integration/test_api_auth.py）。
client = TestClient(app, headers={"Authorization": "Bearer test-api-token"})


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


class _ReviewFixture:
    """一条 feature + 一条 pending_confirmation 能力 + 一条 proposed 关联。"""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.master_id = _new_id()
        self.feature_id = _new_id()
        self.capability_id = _new_id()
        self.key = f"REQ-CR-{self.tag}"
        self.action = f"导出{self.tag}"
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :key, :name, 'x', 1, 'active', 1)"
                ),
                {"id": self.master_id, "key": self.key, "name": f"裁决测试-{self.tag}"},
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
                    "VALUES (:id, :a, 'Excel', :d, 'pending_confirmation', 'analysis')"
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
            session.commit()

    def link_status(self) -> str:
        with SessionLocal() as session:
            return session.execute(
                text(
                    "SELECT review_status FROM feature_capability "
                    "WHERE feature_id=:f AND capability_id=:c"
                ),
                {"f": self.feature_id, "c": self.capability_id},
            ).scalar()

    def capability_status(self) -> str:
        with SessionLocal() as session:
            return session.execute(
                text("SELECT status FROM capability WHERE id=:id"), {"id": self.capability_id}
            ).scalar()

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM feature_capability WHERE capability_id=:c"), {"c": self.capability_id}
            )
            session.execute(
                text("DELETE FROM capability WHERE id=:id"), {"id": self.capability_id}
            )
            session.execute(
                text("DELETE FROM requirement_feature WHERE requirement_id=:id"), {"id": self.master_id}
            )
            session.execute(
                text("DELETE FROM requirement_master WHERE id=:id"), {"id": self.master_id}
            )
            session.commit()


# ── 关联裁决 ────────────────────────────────────────────────────────────


def test_confirm_feature_capability_link() -> None:
    fixture = _ReviewFixture()
    try:
        response = client.patch(
            "/api/v1/feature-capabilities",
            json={
                "feature_id": fixture.feature_id,
                "capability_id": fixture.capability_id,
                "status": "confirmed",
            },
        )

        assert response.status_code == 200
        assert response.json()["review_status"] == "confirmed"
        assert fixture.link_status() == "confirmed"
    finally:
        fixture.cleanup()


def test_dismiss_feature_capability_link() -> None:
    fixture = _ReviewFixture()
    try:
        response = client.patch(
            "/api/v1/feature-capabilities",
            json={
                "feature_id": fixture.feature_id,
                "capability_id": fixture.capability_id,
                "status": "dismissed",
            },
        )

        assert response.status_code == 200
        assert fixture.link_status() == "dismissed"
    finally:
        fixture.cleanup()


def test_cannot_revert_link_to_proposed() -> None:
    """撤回裁决要重新分析，不能靠这个端点 —— 与关系裁决端点同一约定。"""
    fixture = _ReviewFixture()
    try:
        response = client.patch(
            "/api/v1/feature-capabilities",
            json={
                "feature_id": fixture.feature_id,
                "capability_id": fixture.capability_id,
                "status": "proposed",
            },
        )
        assert response.status_code == 422
    finally:
        fixture.cleanup()


def test_unknown_link_returns_404() -> None:
    response = client.patch(
        "/api/v1/feature-capabilities",
        json={"feature_id": 999999999, "capability_id": 999999999, "status": "confirmed"},
    )
    assert response.status_code == 404


# ── 能力本身的裁决 ──────────────────────────────────────────────────────


def test_confirmed_capability_becomes_matchable() -> None:
    """**核心断言**：确认之后，该能力才参与后续匹配。

    在此之前，AI 提议的新能力对后续需求而言等于不存在 —— 这正是
    「AI 不得自动创建正式能力」这条边界的实际含义。
    """
    fixture = _ReviewFixture()
    try:
        match_payload = {"capabilities": [
            {"raw_text": "支持导出 Excel", "action": fixture.action, "object": "Excel"}
        ]}
        service = CapabilityMatchService()

        # 还没确认 → 匹配不上，只会又提一次案
        before = service.match(match_payload)
        assert before["capabilities"][0]["matched"] is False
        assert before["capabilities"][0]["status"] == "pending_confirmation"

        # 人工确认
        response = client.patch(
            f"/api/v1/capabilities/{fixture.capability_id}", json={"status": "active"}
        )
        assert response.status_code == 200
        assert fixture.capability_status() == "active"

        # 现在能匹配上了
        after = service.match(match_payload)
        assert after["capabilities"][0]["matched"] is True
        # 匹配结果里的雪花 ID 是**字符串**（T1 起统一），与 fixture 手里的 int 比要转一下
        assert after["capabilities"][0]["capability_id"] == str(fixture.capability_id)
    finally:
        fixture.cleanup()


def test_deprecated_capability_drops_out_of_matching() -> None:
    fixture = _ReviewFixture()
    try:
        client.patch(f"/api/v1/capabilities/{fixture.capability_id}", json={"status": "active"})
        client.patch(f"/api/v1/capabilities/{fixture.capability_id}", json={"status": "deprecated"})

        result = CapabilityMatchService().match({"capabilities": [
            {"raw_text": "支持导出 Excel", "action": fixture.action, "object": "Excel"}
        ]})

        assert result["capabilities"][0]["matched"] is False
    finally:
        fixture.cleanup()


def test_unknown_capability_patch_returns_404() -> None:
    assert client.patch("/api/v1/capabilities/999999999", json={"status": "active"}).status_code == 404


def test_invalid_capability_status_returns_422() -> None:
    fixture = _ReviewFixture()
    try:
        response = client.patch(
            f"/api/v1/capabilities/{fixture.capability_id}", json={"status": "whatever"}
        )
        assert response.status_code == 422
    finally:
        fixture.cleanup()


# ── 条件别名 ────────────────────────────────────────────────────────────


def test_add_constraint_alias_makes_match_work() -> None:
    """登记别名之后，「按部门维度筛选」就能命中正式键「按部门筛选」。"""
    tag = uuid.uuid4().hex[:8]
    canonical = f"按部门筛选{tag}"
    alias = f"按部门维度筛选{tag}"
    constraint_id = _new_id()
    try:
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO constraint_vocab (id, constraint_key, display_name, status, created_by) "
                    "VALUES (:id, :k, :k, 'active', 'test')"
                ),
                {"id": constraint_id, "k": canonical},
            )
            session.commit()

        result = CapabilityMatchService().match(
            {"capabilities": [{"raw_text": "x", "action": "导出", "object": "y",
                               "constraints": [alias]}]}
        )
        assert result["constraints"]["unmatched"] == [{"raw": alias}]  # 还没登记

        response = client.post(
            "/api/v1/constraints/aliases", json={"alias": alias, "constraint_id": constraint_id}
        )
        assert response.status_code == 200

        result = CapabilityMatchService().match(
            {"capabilities": [{"raw_text": "x", "action": "导出", "object": "y",
                               "constraints": [alias]}]}
        )
        assert result["constraints"]["unmatched"] == []
        assert result["constraints"]["matched"][0]["alias_hit"] is True
    finally:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM constraint_alias WHERE constraint_id=:id"), {"id": constraint_id}
            )
            session.execute(
                text("DELETE FROM constraint_vocab WHERE id=:id"), {"id": constraint_id}
            )
            session.commit()


def test_alias_for_unknown_constraint_returns_404() -> None:
    response = client.post(
        "/api/v1/constraints/aliases", json={"alias": "某别名", "constraint_id": 999999999}
    )
    assert response.status_code == 404
