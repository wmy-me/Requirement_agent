"""能力 / 限定条件的受控词表（方案批次 1）。

两条最重要的断言：

1. **新建默认是 `pending_confirmation`，不参与匹配** —— 这是把方案 §11「受控词表新增
   必须人工确认」落在**代码里**，而不是靠调用方自觉。`find_exact` 默认只认 `active`。
2. **归并是精确匹配 `(action, object)`，不是相似度** —— 「导出 Excel」与「导出 PDF」
   是两个能力；「按部门筛选导出 Excel」的原话仍能归到「导出 Excel」。

仓储级用例在事务里跑、结束回滚；HTTP 级用例打真实库并自清理。
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.api.app import app
from requirement_agent.infrastructure.db.repositories.capability import (
    ACTIVE,
    DEPRECATED,
    PENDING_CONFIRMATION,
    CapabilityRepository,
    ConstraintVocabRepository,
)
from requirement_agent.infrastructure.db.session import SessionLocal, engine

client = TestClient(app)


def _begin():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    return conn, trans, session


def _tag() -> str:
    return uuid.uuid4().hex[:8]


# ── 仓储级：职责边界 ────────────────────────────────────────────────────


def test_create_defaults_to_pending_confirmation() -> None:
    """**核心断言**：新建能力默认落 pending_confirmation，不是 active。

    这样 Agent 提议一条新能力后，它不会立刻参与后续匹配 —— 必须有人确认。
    """
    conn, trans, session = _begin()
    try:
        repo = CapabilityRepository()
        created = repo.create(
            action="导出", object_="Excel临时", display_name="导出 Excel临时", session=session
        )

        assert created["status"] == PENDING_CONFIRMATION
        assert created["status"] != ACTIVE
    finally:
        trans.rollback()
        conn.close()


def test_find_exact_ignores_unconfirmed_proposals() -> None:
    """未经确认的提案不参与匹配 —— 别人提的、没人确认的条目不该被自动复用。"""
    conn, trans, session = _begin()
    try:
        repo = CapabilityRepository()
        repo.create(action="导出", object_="PDF临时", session=session)

        assert repo.find_exact("导出", "PDF临时", session=session) is None

        # 人工确认后才可匹配
        found = repo.find_exact("导出", "PDF临时", only_active=False, session=session)
        assert found is not None
        assert repo.update_status(found["id"], ACTIVE, session=session)["status"] == ACTIVE
        assert repo.find_exact("导出", "PDF临时", session=session) is not None
    finally:
        trans.rollback()
        conn.close()


def test_action_object_pair_is_unique() -> None:
    """同一 (动作, 对象) 只留一条：重复提议幂等返回既有行，不堆重复。"""
    conn, trans, session = _begin()
    try:
        repo = CapabilityRepository()
        first = repo.create(action="导出", object_="CSV临时", session=session)
        second = repo.create(action="导出", object_="CSV临时", session=session)

        assert first["id"] == second["id"]
        count = session.execute(
            text("SELECT count(*) FROM capability WHERE action='导出' AND object='CSV临时'")
        ).scalar()
        assert count == 1
    finally:
        trans.rollback()
        conn.close()


def test_distinct_objects_are_distinct_capabilities() -> None:
    """「导出 Excel」与「导出 PDF」是两条能力 —— 这正是选精确匹配而非相似度的原因。"""
    conn, trans, session = _begin()
    try:
        repo = CapabilityRepository()
        excel = repo.create(action="导出", object_="Excel甲", session=session)
        pdf = repo.create(action="导出", object_="PDF甲", session=session)

        assert excel["id"] != pdf["id"]
        assert repo.find_exact("导出", "Excel甲", only_active=False, session=session)["id"] == excel["id"]
        assert repo.find_exact("导出", "PDF甲", only_active=False, session=session)["id"] == pdf["id"]
    finally:
        trans.rollback()
        conn.close()


def test_capability_requires_action_and_object() -> None:
    conn, trans, session = _begin()
    try:
        repo = CapabilityRepository()
        for action, object_ in (("导出", ""), ("", "Excel"), ("  ", "  ")):
            try:
                repo.create(action=action, object_=object_, session=session)
            except ValueError:
                continue
            raise AssertionError(f"应拒绝空的动作或对象：{action!r}/{object_!r}")
    finally:
        trans.rollback()
        conn.close()


# ── 仓储级：条件与别名 ──────────────────────────────────────────────────


def test_constraint_alias_resolves_to_canonical_key() -> None:
    """「按部门维度筛选」作为别名，命中正式键「按部门筛选」。"""
    conn, trans, session = _begin()
    try:
        repo = ConstraintVocabRepository()
        canonical = repo.create(constraint_key="按部门筛选", status=ACTIVE, session=session)
        repo.add_alias(alias="按部门维度筛选", constraint_id=canonical["id"], session=session)

        by_key = repo.find_exact("按部门筛选", session=session)
        by_alias = repo.find_exact("按部门维度筛选", session=session)

        assert by_key is not None and by_alias is not None
        assert by_key["id"] == by_alias["id"]
        assert by_alias["aliases"] == ["按部门维度筛选"]
    finally:
        trans.rollback()
        conn.close()


def test_constraint_create_defaults_to_pending() -> None:
    conn, trans, session = _begin()
    try:
        repo = ConstraintVocabRepository()
        created = repo.create(constraint_key="按区域筛选临时", session=session)

        assert created["status"] == PENDING_CONFIRMATION
        assert repo.find_exact("按区域筛选临时", session=session) is None
    finally:
        trans.rollback()
        conn.close()


def test_alias_is_not_repointed_by_duplicate_registration() -> None:
    """别名唯一且**不可改挂** —— 一次误操作不该静默改变别名指向的条件。"""
    conn, trans, session = _begin()
    try:
        repo = ConstraintVocabRepository()
        first = repo.create(constraint_key="条件甲", status=ACTIVE, session=session)
        second = repo.create(constraint_key="条件乙", status=ACTIVE, session=session)
        repo.add_alias(alias="某别名", constraint_id=first["id"], session=session)

        again = repo.add_alias(alias="某别名", constraint_id=second["id"], session=session)

        assert again["constraint_id"] == first["id"]  # 仍指向第一个
        assert repo.find_exact("某别名", session=session)["id"] == first["id"]
    finally:
        trans.rollback()
        conn.close()


def test_deprecated_constraint_drops_out_of_matching() -> None:
    conn, trans, session = _begin()
    try:
        repo = ConstraintVocabRepository()
        created = repo.create(constraint_key="停用条件", status=ACTIVE, session=session)
        repo.update_status(created["id"], DEPRECATED, session=session)

        assert repo.find_exact("停用条件", session=session) is None
    finally:
        trans.rollback()
        conn.close()


# ── HTTP 级：只读端点 ───────────────────────────────────────────────────


class _VocabFixture:
    """造一条 active 能力 + 一条待确认提案，用完删干净。"""

    def __init__(self) -> None:
        self.tag = _tag()
        self.action = f"导出{self.tag}"
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO capability (id, action, object, display_name, status, created_by) "
                    "VALUES (:id, :a, :o, :d, 'active', 'test')"
                ),
                {
                    "id": uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000,
                    "a": self.action,
                    "o": "Excel",
                    "d": f"{self.action} Excel",
                },
            )
            session.execute(
                text(
                    "INSERT INTO capability (id, action, object, display_name, status, created_by) "
                    "VALUES (:id, :a, :o, :d, 'pending_confirmation', 'analysis')"
                ),
                {
                    "id": uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000,
                    "a": self.action,
                    "o": "待确认对象",
                    "d": f"{self.action} 待确认对象",
                },
            )
            session.commit()

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM capability WHERE action = :a"), {"a": self.action}
            )
            session.commit()


def test_list_capabilities_filters_by_status() -> None:
    fixture = _VocabFixture()
    try:
        all_items = client.get(
            "/api/v1/capabilities", params={"q": fixture.action}
        ).json()["items"]
        active_only = client.get(
            "/api/v1/capabilities", params={"q": fixture.action, "status": "active"}
        ).json()["items"]

        assert len(all_items) == 2
        assert len(active_only) == 1
        assert active_only[0]["status"] == "active"
        assert active_only[0]["display_name"] == f"{fixture.action} Excel"
    finally:
        fixture.cleanup()


def test_get_unknown_capability_returns_404() -> None:
    assert client.get("/api/v1/capabilities/999999999").status_code == 404


def test_get_unknown_constraint_returns_404() -> None:
    assert client.get("/api/v1/constraints/999999999").status_code == 404


def test_invalid_status_filter_returns_422() -> None:
    assert client.get("/api/v1/capabilities", params={"status": "whatever"}).status_code == 422


def test_capability_detail_round_trip() -> None:
    fixture = _VocabFixture()
    try:
        items = client.get(
            "/api/v1/capabilities", params={"q": fixture.action, "status": "active"}
        ).json()["items"]
        detail = client.get(f"/api/v1/capabilities/{items[0]['id']}")

        assert detail.status_code == 200
        assert detail.json()["display_name"] == f"{fixture.action} Excel"
    finally:
        fixture.cleanup()
