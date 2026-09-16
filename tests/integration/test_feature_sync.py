"""功能同步落库（E 批）：`sync_features` 不再按 ordinal 配对。

这是**旧缺陷的回归测试**，而且是打在真实实现上的那一层 —— `tests/unit/test_review_service.py`
用的是 `FakeFeatureRepo`，永远走不到 `sync_features` 的真正实现，所以那个缺陷此前
既没有单测也没有集成测试覆盖。

三条必须钉住的性质：
1. 中间插入/删除一行，**不得改写其它行**的 `content` / `content_hash` / `provenance`；
2. 模块两列在 INSERT 与 UPDATE 里都要真的写进去；
3. `preview_sync` 与随后 `sync_features` 的结论**逐条一致**（「预览不撒谎」的硬证据）。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.infrastructure.db.repositories.review import RequirementFeatureRepository
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
            "VALUES (:id, :key, '同步测试', 'x', 1, 'active', 1) "
            "ON CONFLICT (requirement_key) DO UPDATE SET requirement_name = EXCLUDED.requirement_name "
            "RETURNING id"
        ),
        {"id": id_, "key": key},
    ).scalar()


def _snapshot(session: Session, requirement_id: int) -> dict[str, dict]:
    """按内容取当前 active feature 的完整快照，用来逐字比对是否被改写。"""
    rows = session.execute(
        text(
            "SELECT content, content_hash, provenance, module_key, module_name, ordinal "
            "FROM requirement_feature WHERE requirement_id = :id AND status = 'active'"
        ),
        {"id": requirement_id},
    ).mappings().all()
    return {row["content"]: dict(row) for row in rows}


def _active_count(session: Session, requirement_id: int) -> int:
    return session.execute(
        text(
            "SELECT count(*) FROM requirement_feature "
            "WHERE requirement_id = :id AND status = 'active'"
        ),
        {"id": requirement_id},
    ).scalar()


# ── 旧缺陷的回归 ────────────────────────────────────────────────────────


def test_insert_in_middle_does_not_rewrite_neighbouring_rows() -> None:
    """中间插一行：只有 1 条 add，其它行的内容与哈希逐字不变。

    旧实现（ordinal 配对）下，B/C/D 会被判成 modify 并真实覆写 —— 这条测试就是钉死它。
    """
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-INSERT", 100000000000000101)
        repo.create_features(
            rid, ["A", "B", "C", "D"], source_id=None,
            requirement_key="REQ-SYNC-INSERT", version_no=1, session=session,
        )
        before = _snapshot(session, rid)

        changes = repo.sync_features(
            rid, ["A", "X", "B", "C", "D"], source_id=None,
            requirement_key="REQ-SYNC-INSERT", version_no=2, session=session,
        )

        assert [c["op"] for c in changes] == ["add"]
        assert changes[0]["content"] == "X"

        after = _snapshot(session, rid)
        assert set(after) == {"A", "B", "C", "D", "X"}
        for content in ("A", "B", "C", "D"):
            assert after[content]["content_hash"] == before[content]["content_hash"]
            assert after[content]["provenance"] == before[content]["provenance"]
            assert after[content]["ordinal"] == before[content]["ordinal"]
    finally:
        trans.rollback()
        conn.close()


def test_delete_in_middle_does_not_rewrite_neighbouring_rows() -> None:
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-DELETE", 100000000000000102)
        repo.create_features(
            rid, ["A", "B", "C", "D"], source_id=None,
            requirement_key="REQ-SYNC-DELETE", version_no=1, session=session,
        )
        before = _snapshot(session, rid)

        changes = repo.sync_features(
            rid, ["A", "C", "D"], source_id=None,
            requirement_key="REQ-SYNC-DELETE", version_no=2, prune=True, session=session,
        )

        assert [c["op"] for c in changes] == ["delete"]
        assert changes[0]["content"] == "B"

        after = _snapshot(session, rid)
        for content in ("A", "C", "D"):
            assert after[content]["content_hash"] == before[content]["content_hash"]
            assert after[content]["provenance"] == before[content]["provenance"]
    finally:
        trans.rollback()
        conn.close()


# ── 模块列真的落库 ──────────────────────────────────────────────────────


def test_module_columns_written_on_add() -> None:
    """新增行（INSERT 路径）必须带模块标签 —— 旧 INSERT 漏了这两列。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-MODADD", 100000000000000103)

        repo.sync_features(
            rid,
            [{"content": "短信验证码登录", "module_key": "鉴权", "module_name": "鉴权"}],
            source_id=None, requirement_key="REQ-SYNC-MODADD", version_no=1, session=session,
        )

        row = session.execute(
            text(
                "SELECT module_key, module_name FROM requirement_feature "
                "WHERE requirement_id = :id AND content = :c"
            ),
            {"id": rid, "c": "短信验证码登录"},
        ).mappings().one()
        assert row["module_key"] == "鉴权"
        assert row["module_name"] == "鉴权"
    finally:
        trans.rollback()
        conn.close()


def test_module_columns_written_on_update() -> None:
    """改模块标签（UPDATE 路径）必须落库 —— 旧 UPDATE 也漏了这两列。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-MODUPD", 100000000000000104)
        repo.create_features(
            rid, [{"content": "登录", "module_key": "鉴权", "module_name": "鉴权"}],
            source_id=None, requirement_key="REQ-SYNC-MODUPD", version_no=1, session=session,
        )

        # 内容一字不改，只把模块从「鉴权」挪到「用户中心」
        repo.sync_features(
            rid, [{"content": "登录", "module_key": "用户中心", "module_name": "用户中心"}],
            source_id=None, requirement_key="REQ-SYNC-MODUPD", version_no=2, session=session,
        )

        row = session.execute(
            text(
                "SELECT module_key, module_name, content_hash FROM requirement_feature "
                "WHERE requirement_id = :id AND content = :c"
            ),
            {"id": rid, "c": "登录"},
        ).mappings().one()
        assert row["module_key"] == "用户中心"
        assert row["module_name"] == "用户中心"
    finally:
        trans.rollback()
        conn.close()


def test_flat_source_does_not_strip_existing_module_labels() -> None:
    """把扁平来源（不带模块）并进带模块的 REQ：既有标签一条都不能被抹掉。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-FLAT", 100000000000000105)
        repo.create_features(
            rid,
            [
                {"content": "登录", "module_key": "鉴权", "module_name": "鉴权"},
                {"content": "导出", "module_key": "报表", "module_name": "报表"},
            ],
            source_id=None, requirement_key="REQ-SYNC-FLAT", version_no=1, session=session,
        )

        repo.sync_features(
            rid, ["登录", "导出", "全新功能"], source_id=None,
            requirement_key="REQ-SYNC-FLAT", version_no=2, session=session,
        )

        rows = session.execute(
            text(
                "SELECT content, module_key FROM requirement_feature "
                "WHERE requirement_id = :id AND status = 'active'"
            ),
            {"id": rid},
        ).mappings().all()
        by_content = {r["content"]: r["module_key"] for r in rows}
        assert by_content["登录"] == "鉴权"
        assert by_content["导出"] == "报表"
        assert by_content["全新功能"] is None
    finally:
        trans.rollback()
        conn.close()


# ── 并集语义 ────────────────────────────────────────────────────────────


def test_union_sync_never_shrinks_the_target() -> None:
    """prune=False：来源比目标短时，目标一条都不能少（旧实现会按位置超尾删除）。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-UNION", 100000000000000106)
        repo.create_features(
            rid, [f"旧{i}" for i in range(1, 11)], source_id=None,
            requirement_key="REQ-SYNC-UNION", version_no=1, session=session,
        )
        before_count = _active_count(session, rid)

        changes = repo.sync_features(
            rid, ["新1", "新2", "新3"], source_id=None,
            requirement_key="REQ-SYNC-UNION", version_no=2, session=session,
        )

        assert all(c["op"] != "delete" for c in changes)
        assert _active_count(session, rid) == before_count
    finally:
        trans.rollback()
        conn.close()


# ── 预览不撒谎 ──────────────────────────────────────────────────────────


def test_preview_sync_does_not_write_and_matches_commit() -> None:
    """预览与落库必须逐条一致，且预览本身一行都不能写。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-PREVIEW", 100000000000000107)
        repo.create_features(
            rid,
            [{"content": "登录", "module_key": "鉴权", "module_name": "鉴权"}, "报表导出"],
            source_id=None, requirement_key="REQ-SYNC-PREVIEW", version_no=1, session=session,
        )
        before = _snapshot(session, rid)

        incoming = [
            {"content": "登录", "module_key": "鉴权", "module_name": "鉴权"},   # keep
            {"content": "报表导出", "module_key": "报表", "module_name": "报表"},  # 只改模块
            {"content": "新增功能", "module_key": "报表", "module_name": "报表"},  # add
        ]
        plan = repo.preview_sync(rid, incoming, session=session)

        # 预览不写库
        assert _snapshot(session, rid) == before

        changes = repo.sync_features(
            rid, incoming, source_id=None,
            requirement_key="REQ-SYNC-PREVIEW", version_no=2, session=session,
        )

        preview_ops = [
            (item.op, item.content) for item in plan if item.op in ("add", "modify", "delete")
        ]
        commit_ops = [
            (c["op"], c["content"] if c["op"] in ("add", "delete") else c["after"])
            for c in changes
        ]
        assert preview_ops == commit_ops
        assert preview_ops == [("add", "新增功能")]
    finally:
        trans.rollback()
        conn.close()


def test_preview_reports_deletions_under_prune() -> None:
    """prune=True 的预览必须把删除清单列出来 —— 这是删除行为的止血点。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-PRU", 100000000000000108)
        repo.create_features(
            rid, ["保留", "会被删"], source_id=None,
            requirement_key="REQ-SYNC-PRU", version_no=1, session=session,
        )

        plan = repo.preview_sync(rid, ["保留"], prune=True, session=session)

        deleted = [item for item in plan if item.op == "delete"]
        assert [item.content for item in deleted] == ["会被删"]
        assert _active_count(session, rid) == 2  # 预览没有真删
    finally:
        trans.rollback()
        conn.close()


def test_preview_rejects_nothing_when_target_has_no_features() -> None:
    """目标 REQ 还没有 feature 时，预览就是「全部新增」。"""
    conn, trans, session = _begin()
    try:
        repo = RequirementFeatureRepository()
        rid = _make_master(session, "REQ-SYNC-EMPTY", 100000000000000109)

        plan = repo.preview_sync(rid, ["甲", "乙"], session=session)

        assert [item.op for item in plan] == ["add", "add"]
        assert [item.ordinal for item in plan] == [1, 2]
    finally:
        trans.rollback()
        conn.close()
