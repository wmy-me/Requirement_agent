"""预合并预览端点（E 批）：`GET /api/v1/reviews/{source_id}/merge-preview`。

本文件打**真实库**（与 `test_api_requirements.py` 同套路）：造一套「目标 REQ + 待审来源」，
断言完在 `finally` 里删干净，不留垃圾。

要钉住的三件事：
1. 预览**不写库** —— 调用前后 `requirement_feature` / `requirement_version` 一行都不能变；
2. 分组、summary 与预览里的 overrides 自洽；
3. `merge_mode` 的差异被如实表达：replace 会列出删除清单并给出 warnings，union 不会。
"""

from __future__ import annotations

import hashlib
import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.api.app import app
from requirement_agent.infrastructure.db.session import SessionLocal

client = TestClient(app)


def _new_id() -> int:
    """避开真实雪花 id 区间，保证与库中既有数据不撞号。"""
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


def _sha(content: str) -> str:
    """与落库口径一致的 content_hash。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class _MergeFixture:
    """一套「目标 REQ（3 条功能，分 2 个模块）+ 待审来源」，用完即删。"""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.master_id = _new_id()
        self.key = f"REQ-PREV-{self.tag}"
        self.source_id = _new_id()
        self.requester = f"preview-{self.tag}"
        self._seed()

    def _seed(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :key, :name, :text, 1, 'active', 1)"
                ),
                {"id": self.master_id, "key": self.key, "name": f"预览目标-{self.tag}", "text": "x"},
            )
            for ordinal, (content, module) in enumerate(
                [("登录", "鉴权"), ("锁定策略", "鉴权"), ("导出", "报表")], start=1
            ):
                session.execute(
                    text(
                        "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                        "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                        "provenance, content_hash, module_key, module_name) "
                        "VALUES (:id, :rid, :fk, :content, 'active', :ordinal, NULL, :rk, 1, "
                        "CAST('[]' AS JSONB), :hash, :mk, :mk)"
                    ),
                    {
                        "id": _new_id(),
                        "rid": self.master_id,
                        "fk": f"F-{ordinal:03d}",
                        "content": content,
                        "ordinal": ordinal,
                        "rk": self.key,
                        "hash": _sha(content),
                        "mk": module,
                    },
                )
            session.execute(
                text(
                    "INSERT INTO requirement_source (id, idempotency_key, source_type, requester_id, "
                    "requester_name, original_text, original_payload, metadata, processing_status, "
                    "submitted_at) "
                    "VALUES (:id, :ik, 'web', :requester, '预览走查', :text, CAST('{}' AS JSONB), "
                    "CAST(:meta AS JSONB), 'pending_review', NOW())"
                ),
                {
                    "id": self.source_id,
                    "ik": f"preview-{self.tag}",
                    "requester": self.requester,
                    "text": "登录、锁定策略、导出，外加一条全新的看板",
                    "meta": '{"extracted": {"modules": [{"module": "鉴权", "items": ["登录", "锁定策略"]}, '
                    '{"module": "报表", "items": ["导出", "看板"]}]}}',
                },
            )
            session.commit()

    def feature_rows(self) -> list[tuple]:
        with SessionLocal() as session:
            return session.execute(
                text(
                    "SELECT content, content_hash, module_key, ordinal FROM requirement_feature "
                    "WHERE requirement_id = :id ORDER BY ordinal"
                ),
                {"id": self.master_id},
            ).all()

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM requirement_feature WHERE requirement_id = :id"),
                {"id": self.master_id},
            )
            session.execute(
                text("DELETE FROM requirement_master WHERE id = :id"), {"id": self.master_id}
            )
            session.execute(
                text("DELETE FROM requirement_source WHERE id = :id"), {"id": self.source_id}
            )
            session.commit()


def _preview(fixture: _MergeFixture, **params):
    query = {"target_requirement_key": fixture.key, **params}
    return client.get(f"/api/v1/reviews/{fixture.source_id}/merge-preview", params=query)


# ── 正常路径 ────────────────────────────────────────────────────────────


def test_preview_groups_by_module_and_summarises() -> None:
    fixture = _MergeFixture()
    try:
        response = _preview(fixture)
        assert response.status_code == 200
        body = response.json()

        assert body["target"]["requirement_key"] == fixture.key
        assert body["next_version"] == 2
        assert body["merge_mode"] == "union"

        # 目标 3 条 + 来源新增 1 条（看板）= 4
        assert body["summary"]["active_after"] == 4
        assert body["summary"]["add"] == 1
        assert body["summary"]["delete"] == 0  # union 不删
        assert body["summary"]["keep"] == 3

        # 按「生效后的模块」分组：新增的看板落在「报表」组
        by_module = {group["module_key"]: group for group in body["groups"]}
        assert set(by_module) == {"鉴权", "报表"}
        assert [item["content"] for item in by_module["报表"]["added"]] == ["看板"]
        assert by_module["鉴权"]["kept"] == 2
        assert by_module["报表"]["kept"] == 1
    finally:
        fixture.cleanup()


def test_preview_does_not_write_anything() -> None:
    """预览是纯读：功能行必须逐字不变，也不产生新版本。"""
    fixture = _MergeFixture()
    try:
        before = fixture.feature_rows()
        with SessionLocal() as session:
            versions_before = session.execute(
                text("SELECT count(*) FROM requirement_version WHERE requirement_id = :id"),
                {"id": fixture.master_id},
            ).scalar()

        assert _preview(fixture).status_code == 200

        assert fixture.feature_rows() == before
        with SessionLocal() as session:
            versions_after = session.execute(
                text("SELECT count(*) FROM requirement_version WHERE requirement_id = :id"),
                {"id": fixture.master_id},
            ).scalar()
        assert versions_after == versions_before
    finally:
        fixture.cleanup()


def test_union_overrides_draft_contains_no_deletions() -> None:
    """union 下回传的 overrides 草稿不该含 delete，否则会把并集又变回替换。"""
    fixture = _MergeFixture()
    try:
        body = _preview(fixture).json()

        ops = [item["op"] for item in body["overrides"]]
        assert "delete" not in ops
        assert ops == ["add"]
    finally:
        fixture.cleanup()


# ── replace 模式 ────────────────────────────────────────────────────────


def test_replace_mode_lists_deletions_and_warns() -> None:
    """replace：来源没提到的功能会被删除，必须在预览里显式喊出来。"""
    fixture = _MergeFixture()
    try:
        body = _preview(fixture, merge_mode="replace").json()

        assert body["merge_mode"] == "replace"
        deleted = [
            item["content"] for group in body["groups"] for item in group["deleted"]
        ]
        assert deleted == []  # 目标 3 条都被来源命中，只是模块标签会变
        assert body["warnings"] == []  # 没有删除、也有新增，不该有任何告警
    finally:
        fixture.cleanup()


def test_replace_mode_reports_real_deletions() -> None:
    """目标里有来源完全没有的功能时，replace 必须把它列进删除清单并告警。"""
    fixture = _MergeFixture()
    try:
        # 往目标里塞一条来源绝不会有的功能
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                    "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                    "provenance, content_hash, module_key, module_name) "
                    "VALUES (:id, :rid, 'F-099', '来源永远不会提到的功能', 'active', 9, NULL, :rk, 1, "
                    "CAST('[]' AS JSONB), :hash, '遗留', '遗留')"
                ),
                {
                    "id": _new_id(),
                    "rid": fixture.master_id,
                    "rk": fixture.key,
                    "hash": _sha("来源永远不会提到的功能"),
                },
            )
            session.commit()

        body = _preview(fixture, merge_mode="replace").json()

        deleted = [item["content"] for group in body["groups"] for item in group["deleted"]]
        assert deleted == ["来源永远不会提到的功能"]
        assert any("失去 1 条" in warning for warning in body["warnings"])

        # union 下同一条功能不会被删
        union_body = _preview(fixture).json()
        assert union_body["summary"]["delete"] == 0
    finally:
        fixture.cleanup()


# ── 错误映射 ────────────────────────────────────────────────────────────


def test_unknown_source_returns_404() -> None:
    fixture = _MergeFixture()
    try:
        response = client.get(
            f"/api/v1/reviews/{_new_id()}/merge-preview",
            params={"target_requirement_key": fixture.key},
        )
        assert response.status_code == 404
    finally:
        fixture.cleanup()


def test_unknown_target_returns_404() -> None:
    fixture = _MergeFixture()
    try:
        response = _preview(fixture, target_requirement_key=f"REQ-NOPE-{fixture.tag}")
        assert response.status_code == 404
    finally:
        fixture.cleanup()


def test_source_not_pending_returns_409() -> None:
    """已经处理过的来源不该还能预览合并。"""
    fixture = _MergeFixture()
    try:
        with SessionLocal() as session:
            session.execute(
                text("UPDATE requirement_source SET processing_status = 'committed' WHERE id = :id"),
                {"id": fixture.source_id},
            )
            session.commit()

        assert _preview(fixture).status_code == 409
    finally:
        fixture.cleanup()


def test_invalid_merge_mode_returns_422() -> None:
    fixture = _MergeFixture()
    try:
        assert _preview(fixture, merge_mode="whatever").status_code == 422
    finally:
        fixture.cleanup()
