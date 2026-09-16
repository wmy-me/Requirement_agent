"""合并即确认：`RequirementRelationRepository.confirm_many`（E 批）。

与 `upsert_many` 的关键差异是「冲突时**升级**而不是跳过」——
人工合并这个动作本身就是对重复关系的背书，已存在的 `proposed` 边要升格为 `confirmed`。

全程在事务里跑、结束回滚，不留数据。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.infrastructure.db.repositories.relation import (
    RequirementRelationRepository,
)
from requirement_agent.infrastructure.db.session import engine


def _begin():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    return conn, trans, session


def _make_master(session: Session, key: str, id_: int) -> int:
    return session.execute(
        text(
            "INSERT INTO requirement_master (id, requirement_key, requirement_name, final_requirement, "
            "current_version, status, lock_version) "
            "VALUES (:id, :key, :name, 'x', 1, 'active', 1) "
            "ON CONFLICT (requirement_key) DO UPDATE SET requirement_name = EXCLUDED.requirement_name "
            "RETURNING id"
        ),
        {"id": id_, "key": key, "name": f"关系测试-{key}"},
    ).scalar()


def _relation(target_id: int, target_key: str) -> dict[str, object]:
    return {
        "target_requirement_id": target_id,
        "target_requirement_key": target_key,
        "relation_type": "duplicates_of",
        "reason": "人工合并时确认",
        "similarity": 0.9,
    }


def _set_status(session: Session, relation_id: int, status: str) -> None:
    """在**当前事务里**改关系状态。

    刻意不用 `repo.update_status`：它自建 session 并 commit（`relation.py:138,150`），
    因此看不到本测试未提交的数据，UPDATE 会命中 0 行且悄无声息 —— 这正是
    `confirm_many` 必须支持传入 session 的原因（审核链路里要与其他落库同事务）。
    """
    session.execute(
        text(
            "UPDATE requirement_relation SET status = :status, decided_by = 'someone' "
            "WHERE id = :id"
        ),
        {"status": status, "id": relation_id},
    )


def _relation_row(session: Session, subject_id: int, target_id: int) -> dict:
    return session.execute(
        text(
            "SELECT id, status, decided_by, created_by FROM requirement_relation "
            "WHERE subject_requirement_id = :s AND target_requirement_id = :t "
            "AND relation_type = 'duplicates_of'"
        ),
        {"s": subject_id, "t": target_id},
    ).mappings().one()


def test_confirm_many_inserts_a_confirmed_edge() -> None:
    conn, trans, session = _begin()
    try:
        repo = RequirementRelationRepository()
        a = _make_master(session, "REQ-REL-A1", 100000000000000201)
        b = _make_master(session, "REQ-REL-B1", 100000000000000202)

        confirmed = repo.confirm_many(
            subject_requirement_id=a,
            subject_requirement_key="REQ-REL-A1",
            relations=[_relation(b, "REQ-REL-B1")],
            decided_by="manager",
            session=session,
        )

        assert len(confirmed) == 1
        assert confirmed[0]["status"] == "confirmed"
        assert confirmed[0]["decided_by"] == "manager"
        assert confirmed[0]["created_by"] == "review"

        row = _relation_row(session, a, b)
        assert row["status"] == "confirmed"
        assert row["decided_by"] == "manager"
    finally:
        trans.rollback()
        conn.close()


def test_confirm_many_is_idempotent() -> None:
    """再确认一次不新增行，状态仍是 confirmed —— 同一对 REQ 的同类关系只留一条。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementRelationRepository()
        a = _make_master(session, "REQ-REL-A2", 100000000000000203)
        b = _make_master(session, "REQ-REL-B2", 100000000000000204)

        first = repo.confirm_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A2",
            relations=[_relation(b, "REQ-REL-B2")], decided_by="manager", session=session,
        )
        second = repo.confirm_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A2",
            relations=[_relation(b, "REQ-REL-B2")], decided_by="other", session=session,
        )

        assert first[0]["id"] == second[0]["id"]
        assert second[0]["status"] == "confirmed"
        assert _relation_row(session, a, b)["decided_by"] == "other"  # 后者覆盖

        total = session.execute(
            text(
                "SELECT count(*) FROM requirement_relation "
                "WHERE subject_requirement_id = :a AND target_requirement_id = :b"
            ),
            {"a": a, "b": b},
        ).scalar()
        assert total == 1
    finally:
        trans.rollback()
        conn.close()


def test_confirm_many_upgrades_a_proposed_edge() -> None:
    """分析写下的 proposed 边，被人合并时升格为 confirmed（而不是被 DO NOTHING 跳过）。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementRelationRepository()
        a = _make_master(session, "REQ-REL-A3", 100000000000000205)
        b = _make_master(session, "REQ-REL-B3", 100000000000000206)

        # 先由分析写入一条 proposed
        repo.upsert_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A3",
            relations=[{"target_requirement_id": b, "target_requirement_key": "REQ-REL-B3",
                        "relation_type": "duplicates_of", "reason": "分析给的",
                        "similarity": 0.85}],
            session=session,
        )
        assert _relation_row(session, a, b)["status"] == "proposed"

        repo.confirm_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A3",
            relations=[_relation(b, "REQ-REL-B3")], decided_by="manager", session=session,
        )

        row = _relation_row(session, a, b)
        assert row["status"] == "confirmed"
        assert row["decided_by"] == "manager"
    finally:
        trans.rollback()
        conn.close()


def test_confirm_many_revives_a_dismissed_edge() -> None:
    """人曾驳回过的边，在真正发生合并时应当被重新确认为 confirmed。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementRelationRepository()
        a = _make_master(session, "REQ-REL-A4", 100000000000000207)
        b = _make_master(session, "REQ-REL-B4", 100000000000000208)

        repo.upsert_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A4",
            relations=[{"target_requirement_id": b, "target_requirement_key": "REQ-REL-B4",
                        "relation_type": "duplicates_of", "reason": "分析给的",
                        "similarity": 0.85}],
            session=session,
        )
        _set_status(session, _relation_row(session, a, b)["id"], "dismissed")

        repo.confirm_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A4",
            relations=[_relation(b, "REQ-REL-B4")], decided_by="manager", session=session,
        )

        assert _relation_row(session, a, b)["status"] == "confirmed"
    finally:
        trans.rollback()
        conn.close()


def test_upsert_many_still_does_not_touch_existing_rows() -> None:
    """回归：`upsert_many` 的 DO NOTHING 语义不能被 confirm_many 的引入改掉。

    它保证「人已 dismissed 的边不会被后续分析翻回 proposed」。
    """
    conn, trans, session = _begin()
    try:
        repo = RequirementRelationRepository()
        a = _make_master(session, "REQ-REL-A5", 100000000000000209)
        b = _make_master(session, "REQ-REL-B5", 100000000000000210)
        relation = {"target_requirement_id": b, "target_requirement_key": "REQ-REL-B5",
                    "relation_type": "duplicates_of", "reason": "分析", "similarity": 0.85}

        assert repo.upsert_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A5",
            relations=[relation], session=session,
        ) == 1
        _set_status(session, _relation_row(session, a, b)["id"], "dismissed")

        # 重新分析又写出同一条边 —— 必须被忽略，不能把人驳回的结论翻回来
        assert repo.upsert_many(
            subject_requirement_id=a, subject_requirement_key="REQ-REL-A5",
            relations=[relation], session=session,
        ) == 0
        assert _relation_row(session, a, b)["status"] == "dismissed"
    finally:
        trans.rollback()
        conn.close()
