"""需求的多个候选标题（同一需求的不同视角入口）。

两条要点：

1. **标题不是内容，是视角** —— 派生自已有的业务对象/能力/条件，不调模型
2. **只有 confirmed 才对外可见** —— 沿用全局的「AI 提议 + 人工确认」约定，且
   重复派生**不覆盖**人工已有的裁决
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from requirement_agent.domain.requirement_titles import derive_title_candidates
from requirement_agent.infrastructure.db.repositories.title_candidate import (
    RequirementTitleCandidateRepository,
)
from requirement_agent.infrastructure.db.session import engine


def _begin():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    return conn, trans, session


def _make_master(session: Session) -> int:
    rid = uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000
    session.execute(
        text(
            "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
            "final_requirement, current_version, status, lock_version) "
            "VALUES (:id, :key, :name, 'x', 1, 'active', 1)"
        ),
        {"id": rid, "key": f"REQ-TC-{rid % 100000}", "name": "标题测试"},
    )
    return rid


# ── 派生（纯函数）──────────────────────────────────────────────────────


def test_derive_covers_three_angles() -> None:
    result = derive_title_candidates(
        business_object="员工数据",
        capabilities=[{"action": "导出", "object": "Excel", "capability_id": 1}],
        constraint_keys=["按部门筛选"],
    )

    by_angle = {item["angle"]: item["title"] for item in result}
    assert by_angle["business_object"] == "员工数据"
    assert by_angle["capability"] == "Excel导出能力"
    assert by_angle["constraint"] == "按部门筛选"


def test_derive_dedupes_and_caps_per_angle() -> None:
    """同一视角最多 3 条 —— 宁可少给，也不要让列表被近义标题淹掉。"""
    many = [{"action": "导出", "object": f"对象{i}"} for i in range(6)]
    result = derive_title_candidates(capabilities=many)

    assert len([item for item in result if item["angle"] == "capability"]) == 3
    assert len(result) == len({item["title"] for item in result})  # 无重复


def test_derive_skips_incomplete_capability() -> None:
    result = derive_title_candidates(
        capabilities=[{"action": "导出"}, {"object": "Excel"}, {"action": " ", "object": "x"}]
    )
    assert result == []


def test_derive_empty_input_returns_nothing() -> None:
    assert derive_title_candidates() == []


# ── 仓储 ───────────────────────────────────────────────────────────────


def test_upsert_creates_proposals_not_confirmed() -> None:
    conn, trans, session = _begin()
    try:
        rid = _make_master(session)
        repo = RequirementTitleCandidateRepository()

        inserted = repo.upsert_many(
            requirement_id=rid,
            titles=[{"title": "员工数据", "angle": "business_object"}],
            session=session,
        )

        assert inserted == 1
        rows = repo.list_for_requirement(rid, session=session)
        assert rows[0]["review_status"] == "proposed"
        assert repo.list_for_requirement(rid, review_status="confirmed", session=session) == []
    finally:
        trans.rollback()
        conn.close()


def test_upsert_does_not_override_human_decision() -> None:
    """重复派生不该把人工 confirmed 的标题翻回 proposed。"""
    conn, trans, session = _begin()
    try:
        rid = _make_master(session)
        repo = RequirementTitleCandidateRepository()
        repo.upsert_many(
            requirement_id=rid, titles=[{"title": "员工数据"}], session=session
        )
        title_id = repo.list_for_requirement(rid, session=session)[0]["id"]
        repo.update_status(title_id, "confirmed", decided_by="manager", session=session)

        assert repo.upsert_many(
            requirement_id=rid, titles=[{"title": "员工数据"}], session=session
        ) == 0
        assert repo.list_for_requirement(rid, review_status="confirmed", session=session)[0][
            "title"
        ] == "员工数据"
    finally:
        trans.rollback()
        conn.close()


def test_same_title_twice_on_one_requirement_is_rejected() -> None:
    """同一需求下标题唯一 —— 同一句话不必存两遍。"""
    conn, trans, session = _begin()
    try:
        rid = _make_master(session)
        insert = text(
            "INSERT INTO requirement_title_candidate (id, requirement_id, title, angle) "
            "VALUES (:id, :rid, '同名标题', 'free')"
        )
        session.execute(insert, {"id": 800000000000000001, "rid": rid})

        with pytest.raises(IntegrityError):
            session.execute(insert, {"id": 800000000000000002, "rid": rid})
    finally:
        trans.rollback()
        conn.close()


def test_list_confirmed_groups_by_requirement() -> None:
    """列表页要一次取多条需求的多入口，避免 N+1。"""
    conn, trans, session = _begin()
    try:
        rid = _make_master(session)
        repo = RequirementTitleCandidateRepository()
        repo.upsert_many(
            requirement_id=rid,
            titles=[{"title": "甲"}, {"title": "乙"}, {"title": "丙"}],
            session=session,
        )
        for item in repo.list_for_requirement(rid, session=session)[:2]:
            repo.update_status(item["id"], "confirmed", session=session)

        grouped = repo.list_confirmed_for_requirements([rid], session=session)

        assert len(grouped[rid]) == 2  # 只出 confirmed 的两条
    finally:
        trans.rollback()
        conn.close()


# ── HTTP 级：端点 ───────────────────────────────────────────────────────


import os

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient

from requirement_agent.api.app import app
from requirement_agent.infrastructure.db.session import SessionLocal

# B1 起 API 需要鉴权：`API_AUTH_TOKEN` 是兼容入口，等同于一个 admin token。
# 测具体的 401/403 行为请另建不带头的 TestClient（见 tests/integration/test_api_auth.py）。
client = TestClient(app, headers={"Authorization": "Bearer test-api-token"})


class _TitleFixture:
    """一条需求 + 一条能力 + 一条功能 + 一条 proposed 标题，用完删干净。"""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.master_id = uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000
        self.key = f"REQ-TIT-{self.tag}"
        self.capability_id = uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000
        self.feature_id = uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :k, '标题端点测试', 'x', 1, 'active', 1)"
                ),
                {"id": self.master_id, "k": self.key},
            )
            session.execute(
                text(
                    "INSERT INTO capability (id, action, object, display_name, status, created_by) "
                    "VALUES (:id, :a, 'Excel', :d, 'active', 'test')"
                ),
                {"id": self.capability_id, "a": f"导出{self.tag}", "d": f"导出{self.tag} Excel"},
            )
            session.execute(
                text(
                    "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                    "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                    "provenance, content_hash) "
                    "VALUES (:id, :rid, 'F-001', '支持导出 Excel', 'active', 1, NULL, :k, 1, "
                    "CAST('[]' AS JSONB), 'h')"
                ),
                {"id": self.feature_id, "rid": self.master_id, "k": self.key},
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
                    "INSERT INTO requirement_title_candidate (id, requirement_id, title, angle, "
                    "capability_id, source, review_status) "
                    "VALUES (:id, :rid, :t, 'capability', :c, 'analysis', 'proposed')"
                ),
                {
                    "id": uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000,
                    "rid": self.master_id,
                    "t": f"Excel导出能力{self.tag}",
                    "c": self.capability_id,
                },
            )
            session.commit()

    def title_id(self) -> int:
        with SessionLocal() as session:
            return session.execute(
                text("SELECT id FROM requirement_title_candidate WHERE requirement_id=:r"),
                {"r": self.master_id},
            ).scalar()

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM requirement_title_candidate WHERE requirement_id=:r"),
                {"r": self.master_id},
            )
            session.execute(
                text("DELETE FROM feature_capability WHERE capability_id=:c"), {"c": self.capability_id}
            )
            session.execute(
                text("DELETE FROM capability WHERE id=:c"), {"c": self.capability_id}
            )
            session.execute(
                text("DELETE FROM requirement_feature WHERE requirement_id=:r"), {"r": self.master_id}
            )
            session.execute(
                text("DELETE FROM requirement_master WHERE id=:r"), {"r": self.master_id}
            )
            session.commit()


def test_list_titles_carries_highlight() -> None:
    """**这是这批标题的意义**：内容不变，但从不同标题点进去高亮不同。"""
    fixture = _TitleFixture()
    try:
        response = client.get(f"/api/v1/requirements/{fixture.key}/titles")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["review_status"] == "proposed"
        highlight = items[0]["highlight"]
        assert highlight["kind"] == "capability"
        assert highlight["feature_keys"] == ["F-001"]  # 锚到该能力的那条功能
    finally:
        fixture.cleanup()


def test_confirm_title_makes_it_visible() -> None:
    fixture = _TitleFixture()
    try:
        response = client.patch(
            f"/api/v1/requirement-titles/{fixture.title_id()}", json={"status": "confirmed"}
        )
        assert response.status_code == 200
        assert response.json()["review_status"] == "confirmed"

        confirmed = client.get(
            f"/api/v1/requirements/{fixture.key}/titles", params={"review_status": "confirmed"}
        ).json()["items"]
        assert len(confirmed) == 1
    finally:
        fixture.cleanup()


def test_add_manual_title() -> None:
    fixture = _TitleFixture()
    try:
        response = client.post(
            f"/api/v1/requirements/{fixture.key}/titles", json={"title": "人工起的名"}
        )

        assert response.status_code == 200
        assert response.json()["inserted"] == 1
        assert response.json()["item"]["angle"] == "free"
        assert response.json()["item"]["review_status"] == "proposed"
    finally:
        fixture.cleanup()


def test_unknown_requirement_titles_returns_404() -> None:
    assert client.get("/api/v1/requirements/REQ-NOPE-9/titles").status_code == 404


def test_unknown_title_patch_returns_404() -> None:
    assert client.patch(
        "/api/v1/requirement-titles/999999999", json={"status": "confirmed"}
    ).status_code == 404


# ── 主线判定建议（分析侧）──────────────────────────────────────────────
#
# 「该新建主线还是追加到既有主线」由模型判、人工确认（方案 §11）。
# 这里只测**解析层**：模型输出不可信，必须防呆且不编默认值。


def test_suggestion_parses_valid_append() -> None:
    from requirement_agent.skills.analyze_skill import _coerce_suggestion

    result = _coerce_suggestion(
        {"action": "append_to", "target_requirement_key": "REQ-000015",
         "confidence": 0.91, "reason": "业务对象与能力都一致"}
    )

    assert result["action"] == "append_to"
    assert result["target_requirement_key"] == "REQ-000015"
    assert result["confidence"] == 0.91


def test_append_without_target_degrades_to_create_new() -> None:
    """**防呆**：`append_to` 却不给目标是无意义的（追加到哪条？）。

    降级为 `create_new` 并清掉目标，而不是带着一个空的追加目标往下传。
    """
    from requirement_agent.skills.analyze_skill import _coerce_suggestion

    result = _coerce_suggestion({"action": "append_to", "confidence": 0.9})

    assert result["action"] == "create_new"
    assert result["target_requirement_key"] is None


def test_dirty_confidence_is_clamped_not_crashed() -> None:
    from requirement_agent.skills.analyze_skill import _coerce_suggestion

    assert _coerce_suggestion({"action": "create_new", "confidence": "高"})["confidence"] == 0.0
    assert _coerce_suggestion({"action": "create_new", "confidence": 9})["confidence"] == 1.0
    assert _coerce_suggestion({"action": "create_new", "confidence": -3})["confidence"] == 0.0


def test_invalid_suggestion_returns_none_not_a_guess() -> None:
    """**不编默认值**：报告里写一条没依据的「建议追加」比不写更糟 ——
    审核人会当成模型判断过。给不出有效建议就返回 None。"""
    from requirement_agent.skills.analyze_skill import _coerce_suggestion

    for bad in (None, "字符串", {"action": "whatever"}, {"action": ""}, 123, []):
        assert _coerce_suggestion(bad) is None


def test_analysis_result_suggestion_defaults_to_none() -> None:
    """启发式回退路径不给建议 —— 规则算不出「该不该并线」这件事。"""
    from requirement_agent.agents.analyze_agent import AnalysisResult

    assert AnalysisResult().suggestion is None
