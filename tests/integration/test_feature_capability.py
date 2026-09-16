"""功能 ↔ 能力关联与版本快照（方案批次 3）。

三条要点：

1. **关联一律 `proposed`** —— AI 提议的关联需要人工裁决，只有 confirmed 才代表
   能力在需求上正式成立。
2. **重复关联不覆盖人工裁决** —— 下一次分析不该把人已 dismissed 的关联翻回 proposed。
3. **同主线只有一个 current** —— 由数据库部分唯一索引保证，不靠应用自觉。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from requirement_agent.infrastructure.db.repositories.capability import (
    ACTIVE,
    CapabilityRepository,
    FeatureCapabilityRepository,
)
from requirement_agent.infrastructure.db.repositories.requirement import (
    RequirementVersionRepository,
)
from requirement_agent.infrastructure.db.session import engine
from requirement_agent.workflows.commit_nodes import (
    _build_capability_links,
    _capability_snapshot,
    _constraint_snapshot,
)


def _begin():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    return conn, trans, session


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


def _make_master(session: Session, key: str) -> int:
    rid = _new_id()
    session.execute(
        text(
            "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
            "final_requirement, current_version, status, lock_version) "
            "VALUES (:id, :key, :name, 'x', 0, 'active', 0)"
        ),
        {"id": rid, "key": key, "name": f"关联测试-{key}"},
    )
    return rid


def _make_feature(session: Session, requirement_id: int, feature_key: str, content: str) -> int:
    fid = _new_id()
    session.execute(
        text(
            "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, status, "
            "ordinal, origin_source_id, origin_requirement_key, origin_version_no, provenance, content_hash) "
            "VALUES (:id, :rid, :fk, :content, 'active', 1, NULL, 'REQ-X', 1, CAST('[]' AS JSONB), 'h')"
        ),
        {"id": fid, "rid": requirement_id, "fk": feature_key, "content": content},
    )
    return fid


def _make_capability(session: Session, action: str, object_: str) -> int:
    return CapabilityRepository().create(
        action=action, object_=object_, status=ACTIVE, session=session
    )["id"]


# ── 关联写入 ────────────────────────────────────────────────────────────


def test_link_many_writes_proposed_not_confirmed() -> None:
    """**核心**：关联默认 proposed —— 能力在需求上是否成立需要人工确认。"""
    conn, trans, session = _begin()
    try:
        rid = _make_master(session, "REQ-FC-1")
        fid = _make_feature(session, rid, "F-001", "支持导出 Excel")
        cid = _make_capability(session, "导出", "Excel关联1")

        repo = FeatureCapabilityRepository()
        inserted = repo.link_many(
            links=[{"feature_id": fid, "capability_id": cid, "raw_text": "支持导出 Excel"}],
            session=session,
        )

        assert inserted == 1
        row = session.execute(
            text("SELECT review_status FROM feature_capability WHERE feature_id=:f AND capability_id=:c"),
            {"f": fid, "c": cid},
        ).scalar()
        assert row == "proposed"
        assert row != "confirmed"
    finally:
        trans.rollback()
        conn.close()


def test_link_many_does_not_override_human_decision() -> None:
    """人工驳回过的关联，不该被下一次分析翻回 proposed。"""
    conn, trans, session = _begin()
    try:
        rid = _make_master(session, "REQ-FC-2")
        fid = _make_feature(session, rid, "F-001", "支持导出 Excel")
        cid = _make_capability(session, "导出", "Excel关联2")
        repo = FeatureCapabilityRepository()
        link = {"feature_id": fid, "capability_id": cid, "raw_text": "支持导出 Excel"}

        repo.link_many(links=[link], session=session)
        repo.update_status(
            feature_id=fid, capability_id=cid, status="dismissed",
            decided_by="manager", session=session,
        )

        assert repo.link_many(links=[link], session=session) == 0  # DO NOTHING
        row = session.execute(
            text("SELECT review_status FROM feature_capability WHERE feature_id=:f AND capability_id=:c"),
            {"f": fid, "c": cid},
        ).scalar()
        assert row == "dismissed"
    finally:
        trans.rollback()
        conn.close()


def test_list_for_requirement_hides_links_on_soft_deleted_features() -> None:
    """功能被软删后，它的能力关联不再出现在**当前**视图里（但行仍保留供历史查询）。"""
    conn, trans, session = _begin()
    try:
        rid = _make_master(session, "REQ-FC-3")
        fid = _make_feature(session, rid, "F-001", "支持导出 PDF")
        cid = _make_capability(session, "导出", "PDF关联")
        repo = FeatureCapabilityRepository()
        repo.link_many(
            links=[{"feature_id": fid, "capability_id": cid, "raw_text": "支持导出 PDF"}],
            session=session,
        )
        assert len(repo.list_for_requirement(rid, session=session)) == 1

        # 需求更新，不再支持 PDF → feature 被软删（复用 feature 自己的生命周期）
        session.execute(
            text("UPDATE requirement_feature SET status='deleted' WHERE id=:id"), {"id": fid}
        )

        assert repo.list_for_requirement(rid, session=session) == []
        # 关联行本身还在，历史版本查询仍能读到
        assert session.execute(
            text("SELECT count(*) FROM feature_capability WHERE feature_id=:f"), {"f": fid}
        ).scalar() == 1
    finally:
        trans.rollback()
        conn.close()


# ── 版本状态 ────────────────────────────────────────────────────────────


def test_mark_current_supersedes_previous_version() -> None:
    conn, trans, session = _begin()
    try:
        rid = _make_master(session, "REQ-FC-4")
        repo = RequirementVersionRepository()
        for version_no in (1, 2, 3):
            # 顺序即真实用法：先降级旧的 current，再插入新的（save 默认插 current）
            repo.supersede_current(
                requirement_id=rid, keep_version_no=version_no, session=session
            )
            session.execute(
                text(
                    "INSERT INTO requirement_version (id, requirement_id, version_no, version_title, "
                    "change_type, requirement_snapshot, change_summary, created_by, reviewed_by, status) "
                    "VALUES (:id, :rid, :v, 't', 'new', 's', 'c', 'a', 'a', 'current')"
                ),
                {"id": _new_id(), "rid": rid, "v": version_no},
            )

        rows = session.execute(
            text("SELECT version_no, status FROM requirement_version WHERE requirement_id=:r ORDER BY version_no"),
            {"r": rid},
        ).all()
        assert rows == [(1, "superseded"), (2, "superseded"), (3, "current")]
    finally:
        trans.rollback()
        conn.close()


def test_database_rejects_two_current_versions() -> None:
    """唯一性由**数据库**保证，不是靠应用自觉。

    这条正是 `supersede_current` 必须**先于** `save` 调用的原因：
    不先降级，第二条版本插进去就撞约束。
    """
    conn, trans, session = _begin()
    try:
        rid = _make_master(session, "REQ-FC-5")
        insert = text(
            "INSERT INTO requirement_version (id, requirement_id, version_no, version_title, "
            "change_type, requirement_snapshot, change_summary, created_by, reviewed_by, status) "
            "VALUES (:id, :rid, :v, 't', 'new', 's', 'c', 'a', 'a', 'current')"
        )
        session.execute(insert, {"id": _new_id(), "rid": rid, "v": 1})

        with pytest.raises(IntegrityError):
            session.execute(insert, {"id": _new_id(), "rid": rid, "v": 2})
    finally:
        trans.rollback()
        conn.close()


# ── 纯函数：关联与快照的构造 ────────────────────────────────────────────


def _features(*specs) -> list[dict]:
    return [
        {"id": idx, "feature_key": f"F-{idx:03d}", "content": content}
        for idx, content in enumerate(specs, start=1)
    ]


def _match_payload(*caps, matched=None, unmatched=None) -> dict:
    return {
        "capabilities": [
            {"raw_text": raw, "action": action, "object": obj, "capability_id": cid,
             "display_name": f"{action} {obj}"}
            for raw, action, obj, cid in caps
        ],
        "constraints": {"matched": matched or [], "unmatched": unmatched or []},
    }


def test_build_links_matches_by_normalized_text() -> None:
    """靠 raw_text 与 feature 正文的归一匹配建立对应（空白差异不影响）。"""
    features = _features("支持导出 Excel", "支持按部门筛选")
    payload = _match_payload(
        ("支持导出  Excel", "导出", "Excel", 1),   # 多一个空格
        ("支持按部门筛选", "筛选", "部门", 2),
    )

    links = _build_capability_links(features, payload)

    assert [(link["feature_id"], link["capability_id"]) for link in links] == [(1, 1), (2, 2)]


def test_build_links_skips_unmatched_features() -> None:
    """对不上就不挂 —— 宁可少挂，不要挂错。"""
    features = _features("支持导出 Excel", "模型改写过的表述")
    payload = _match_payload(("支持导出 Excel", "导出", "Excel", 1))

    assert len(_build_capability_links(features, payload)) == 1


def test_build_links_uses_feature_content_as_raw_text() -> None:
    """raw_text 存 feature 正文（落库后的原文），不是模型改写的版本。"""
    features = _features("支持导出 Excel")
    payload = _match_payload(("支持导出Excel", "导出", "Excel", 1))

    links = _build_capability_links(features, payload)

    assert links[0]["raw_text"] == "支持导出 Excel"


def test_capability_snapshot_aggregates_features_per_capability() -> None:
    """一个能力由多条功能支撑时，快照里聚成一条并列出 feature_keys。"""
    features = _features("支持导出报表", "支持批量导出")
    payload = _match_payload(
        ("支持导出报表", "导出", "Excel", 7), ("支持批量导出", "导出", "Excel", 7)
    )
    links = _build_capability_links(features, payload)

    snapshot = _capability_snapshot(features, payload, links)

    assert len(snapshot) == 1
    assert snapshot[0]["capability_id"] == 7
    assert snapshot[0]["feature_keys"] == ["F-001", "F-002"]
    assert snapshot[0]["review_status"] == "proposed"


def test_constraint_snapshot_keeps_matched_and_unmatched() -> None:
    """未命中的条件也要进快照 —— 人工审核要看到「模型提过这个」，才有依据决定。"""
    payload = _match_payload(
        matched=[{"raw": "按部门维度筛选", "constraint_key": "按部门筛选", "alias_hit": True}],
        unmatched=[{"raw": "按区域层级导出"}],
    )

    snapshot = _constraint_snapshot(payload)

    assert snapshot[0] == {
        "raw": "按部门维度筛选", "constraint_key": "按部门筛选", "matched": True, "alias_hit": True
    }
    assert snapshot[1] == {"raw": "按区域层级导出", "constraint_key": None, "matched": False}


def test_snapshot_handles_empty_payload() -> None:
    features = _features("任意功能")
    assert _build_capability_links(features, {}) == []
    assert _capability_snapshot(features, {}, []) == []
    assert _constraint_snapshot({}) == []
