"""文档版本链 —— 分片 diff 与主线结构（方案批次 1 + 2）。

两条要点：

1. **分片不重写第二份 diff** —— 复用 `feature_diff.plan_sync` 的内核，
   所以「中间插一片不级联」这条性质与功能条目**天然一致**
2. **「同名同格式才更新」** 落在 `document_stream` 的唯一索引上
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from requirement_agent.domain.chunk_diff import plan_chunk_sync
from requirement_agent.infrastructure.db.session import engine


def _begin():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    return conn, trans, session


def _chunks(*texts: str) -> list[dict]:
    return [
        {"id": index * 10, "chunk_index": index, "chunk_text": content}
        for index, content in enumerate(texts)
    ]


def _ops(plan) -> list[str]:
    return [item.op for item in plan]


# ── 分片 diff（纯函数）──────────────────────────────────────────────────


def test_insert_in_middle_does_not_cascade() -> None:
    """中间插一片：只有 1 条 add，其余全 keep，且**各保持原序号**。

    这与功能条目是同一个问题、同一份内核 —— 所以性质天然一致。
    旧实现（按位置）会把后面每一片都判成 modify 并重算向量。
    """
    plan = plan_chunk_sync(_chunks("甲", "乙", "丙", "丁", "戊"), ["甲", "新", "乙", "丙", "丁", "戊"])

    assert _ops(plan).count("add") == 1
    assert _ops(plan).count("keep") == 5
    assert _ops(plan).count("modify") == 0
    # keep 的仍在自己原来的序号上，新增追加到末尾
    assert {item.content: item.ordinal for item in plan}["乙"] == 1
    assert {item.content: item.ordinal for item in plan}["新"] == 5


def test_append_at_tail() -> None:
    plan = plan_chunk_sync(_chunks("甲", "乙"), ["甲", "乙", "丙"])

    assert _ops(plan) == ["keep", "keep", "add"]
    assert [item.ordinal for item in plan] == [0, 1, 2]


def test_prune_defaults_to_true_for_documents() -> None:
    """文档与功能条目的唯一实质差异：**文档以新内容为准**，旧分片该删就删。"""
    plan = plan_chunk_sync(_chunks("甲", "乙", "丙"), ["甲", "丙"])

    assert _ops(plan).count("delete") == 1
    assert [item.content for item in plan if item.op == "delete"] == ["乙"]


def test_plan_can_opt_out_of_prune() -> None:
    """显式 prune=False 时旧分片保留 —— 给「只追加不删除」的场景留出口。"""
    plan = plan_chunk_sync(_chunks("甲", "乙", "丙"), ["甲", "丙"], prune=False)

    assert _ops(plan).count("delete") == 0
    assert _ops(plan).count("keep") == 3


def test_old_chunk_index_is_carried_in_feature_key() -> None:
    """调用方靠 `feature_key` 知道「这条是原来哪一片」—— 否则没法把它对应回库里的行。"""
    plan = plan_chunk_sync(_chunks("甲", "乙", "丙"), ["甲", "丙"])

    by_content = {item.content: item for item in plan}
    assert by_content["甲"].feature_key == "0"
    assert by_content["丙"].feature_key == "2"
    assert by_content["乙"].feature_key == "1"  # 被删的那片也带着旧序号


def test_ordinal_has_no_offset() -> None:
    """**不偏移** —— 偏移一位会让新增分片跳号（实测踩过）。

    分片序号是 0-based（`chunk_index` 从 0 起），内核的「追加到末尾」按
    `max(ordinal)+1` 算，所以直接沿用即可。
    """
    plan = plan_chunk_sync(_chunks("甲"), ["甲", "乙"])

    assert {item.content: item.ordinal for item in plan}["乙"] == 1


def test_identical_content_yields_all_keep() -> None:
    plan = plan_chunk_sync(_chunks("甲", "乙"), ["甲", "乙"])

    assert _ops(plan) == ["keep", "keep"]


def test_reworded_chunk_is_paired_not_delete_plus_add() -> None:
    """改写一片 → 配成 modify，而不是「删一片 + 加一片」。

    靠内核的第三遍（同组内按序配对）——这正是复用内核白捡的性质。
    """
    plan = plan_chunk_sync(_chunks("甲", "乙改前", "丙"), ["甲", "乙改后", "丙"])

    assert _ops(plan) == ["keep", "modify", "keep"]
    assert [item.before for item in plan if item.op == "modify"] == ["乙改前"]


# ── 主线结构 ───────────────────────────────────────────────────────────


def test_same_name_and_format_is_one_stream() -> None:
    """**「同名同格式才更新」这条规则就落在这个唯一索引上。**"""
    conn, trans, session = _begin()
    try:
        insert = text(
            "INSERT INTO document_stream (id, file_name, content_type) "
            "VALUES (:id, '报告.pdf', 'application/pdf')"
        )
        session.execute(insert, {"id": 900000000000000001})

        with pytest.raises(IntegrityError):
            session.execute(insert, {"id": 900000000000000002})
    finally:
        trans.rollback()
        conn.close()


def test_different_format_is_a_separate_stream() -> None:
    """格式不同就是两条主线 —— 符合方案里的规则。"""
    conn, trans, session = _begin()
    try:
        for index, content_type in enumerate(("application/pdf", "text/plain"), start=1):
            session.execute(
                text(
                    "INSERT INTO document_stream (id, file_name, content_type) "
                    "VALUES (:id, '报告', :ct)"
                ),
                {"id": 900000000000000010 + index, "ct": content_type},
            )
        assert session.execute(
            text("SELECT count(*) FROM document_stream WHERE file_name = '报告'")
        ).scalar() == 2
    finally:
        trans.rollback()
        conn.close()


def test_only_one_current_version_per_stream() -> None:
    """与 requirement_version 同一招：数据库层保证，不靠应用自觉。"""
    conn, trans, session = _begin()
    try:
        session.execute(
            text(
                "INSERT INTO document_stream (id, file_name, content_type) "
                "VALUES (900000000000000020, '唯一性.pdf', 'application/pdf')"
            )
        )
        insert_asset = text(
            "INSERT INTO document_asset (id, file_name, content_type, storage_uri, checksum, "
            "stream_id, version_no, status) "
            "VALUES (:id, '唯一性.pdf', 'application/pdf', 's3://x', 'ck', "
            "900000000000000020, :v, 'current')"
        )
        session.execute(insert_asset, {"id": 900000000000000021, "v": 1})

        with pytest.raises(IntegrityError):
            session.execute(insert_asset, {"id": 900000000000000022, "v": 2})
    finally:
        trans.rollback()
        conn.close()


def test_backfill_gave_every_existing_asset_a_stream() -> None:
    """回填后不应有落在主线之外的资产（否则它们永远进不了版本链）。"""
    with engine.connect() as conn:
        orphan = conn.execute(
            text("SELECT count(*) FROM document_asset WHERE stream_id IS NULL")
        ).scalar()
        chunks_without_hash = conn.execute(
            text("SELECT count(*) FROM document_chunk WHERE content_hash IS NULL")
        ).scalar()

    assert orphan == 0
    assert chunks_without_hash == 0
