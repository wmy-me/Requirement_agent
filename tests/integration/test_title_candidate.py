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
