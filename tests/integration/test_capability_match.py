"""能力/条件候选与词表的比对（方案批次 2）。

本文件的首要目的是**钉住职责边界**（方案 §11.8），而不是测正常路径：

1. 未匹配的能力只写 `pending_confirmation`，**且该提案立刻不参与后续匹配** ——
   即模型抽错了也污染不了任何"已确认"的匹配结果。
2. 未匹配的条件**一条都不写词表** —— 实测长文本噪声率约 90%，写进去会淹没审核队列。

仓储级用例在事务里跑、结束回滚。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.application.capability_match_service import CapabilityMatchService
from requirement_agent.infrastructure.db.repositories.capability import (
    ACTIVE,
    PENDING_CONFIRMATION,
    CapabilityRepository,
    ConstraintVocabRepository,
)
from requirement_agent.infrastructure.db.session import engine


def _begin():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    return conn, trans, session


def _service() -> CapabilityMatchService:
    return CapabilityMatchService()


def _extracted(**overrides) -> dict:
    payload = {"business_object": "员工数据", "capabilities": []}
    payload.update(overrides)
    return payload


def _cap(action: str, object_: str, constraints: list[str] | None = None) -> dict:
    return {
        "raw_text": f"支持{action}{object_}",
        "action": action,
        "object": object_,
        "constraints": constraints or [],
    }


def _count(session: Session, table: str, where: str, **params) -> int:
    return session.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params).scalar()


# ── 能力：匹配与提案 ────────────────────────────────────────────────────


def test_matched_capability_is_reused_not_duplicated() -> None:
    """命中 active 词表 → 直接引用，**不新建任何行**。"""
    conn, trans, session = _begin()
    try:
        repo = CapabilityRepository()
        existing = repo.create(
            action="导出", object_="Excel命中", status=ACTIVE, session=session
        )

        result = _service().match(
            _extracted(capabilities=[_cap("导出", "Excel命中")]), session=session
        )

        hits = result["capabilities"]
        assert len(hits) == 1
        assert hits[0]["matched"] is True
        assert hits[0]["capability_id"] == existing["id"]
        assert _count(session, "capability", "object = 'Excel命中'") == 1  # 没有重复行
    finally:
        trans.rollback()
        conn.close()


def test_unmatched_capability_creates_only_a_proposal() -> None:
    """**核心边界**：未命中只写 pending_confirmation，绝不写 active。"""
    conn, trans, session = _begin()
    try:
        result = _service().match(
            _extracted(capabilities=[_cap("导出", "未见过宾语")]), session=session
        )

        hit = result["capabilities"][0]
        assert hit["matched"] is False
        assert hit["status"] == PENDING_CONFIRMATION
        assert hit["proposed"] is True

        row = session.execute(
            text("SELECT status FROM capability WHERE object = '未见过宾语'")
        ).scalar()
        assert row == PENDING_CONFIRMATION
        assert row != ACTIVE
    finally:
        trans.rollback()
        conn.close()


def test_proposal_does_not_participate_in_later_matching() -> None:
    """提案**立刻不能被匹配** —— 这是「提案 ≠ 正式」的直接后果。

    模型抽错一次，不会让它在下一条需求里被当成「已有能力」复用。
    """
    conn, trans, session = _begin()
    try:
        service = _service()
        first = service.match(_extracted(capabilities=[_cap("导出", "提案宾语")]), session=session)
        assert first["capabilities"][0]["status"] == PENDING_CONFIRMATION

        # 第二次遇到同一条：仍然匹配不上，而是走「复用提案行」的分支
        second = service.match(_extracted(capabilities=[_cap("导出", "提案宾语")]), session=session)
        assert second["capabilities"][0]["matched"] is False
        assert _count(session, "capability", "object = '提案宾语'") == 1  # 提案不重复堆

        # 人工确认之后才参与匹配
        repo = CapabilityRepository()
        found = repo.find_exact("导出", "提案宾语", only_active=False, session=session)
        repo.update_status(found["id"], ACTIVE, session=session)

        third = service.match(_extracted(capabilities=[_cap("导出", "提案宾语")]), session=session)
        assert third["capabilities"][0]["matched"] is True
    finally:
        trans.rollback()
        conn.close()


def test_same_candidate_repeated_in_one_extraction_is_processed_once() -> None:
    conn, trans, session = _begin()
    try:
        result = _service().match(
            _extracted(capabilities=[_cap("导出", "重复宾语"), _cap("导出", "重复宾语")]),
            session=session,
        )

        assert len(result["capabilities"]) == 1
        assert _count(session, "capability", "object = '重复宾语'") == 1
    finally:
        trans.rollback()
        conn.close()


def test_incomplete_candidates_are_skipped() -> None:
    """缺动作或宾语的行直接丢弃 —— 它们构不成能力身份，留着只会变垃圾提案。"""
    conn, trans, session = _begin()
    try:
        result = _service().match(
            _extracted(
                capabilities=[
                    {"action": "导出", "object": ""},
                    {"action": "", "object": "Excel"},
                    {"action": "  ", "object": "  "},
                    _cap("导出", "完整宾语"),
                ]
            ),
            session=session,
        )

        assert len(result["capabilities"]) == 1
        assert result["capabilities"][0]["object"] == "完整宾语"
    finally:
        trans.rollback()
        conn.close()


# ── 条件：只匹配，不入表 ────────────────────────────────────────────────


def test_unmatched_constraint_is_never_written_to_vocab() -> None:
    """**核心边界**：未命中的条件一条都不写词表。

    长文本上模型会把「最多12个汉字」「同时在线1000人」当成限定条件，
    自动入表会让审核队列被垃圾淹没。
    """
    conn, trans, session = _begin()
    try:
        result = _service().match(
            _extracted(
                capabilities=[
                    _cap("导出", "条件宾语", constraints=["按区域层级导出", "最多12个汉字"])
                ]
            ),
            session=session,
        )

        assert [item["raw"] for item in result["constraints"]["unmatched"]] == [
            "按区域层级导出",
            "最多12个汉字",
        ]
        assert result["constraints"]["matched"] == []
        assert _count(session, "constraint_vocab", "constraint_key = '按区域层级导出'") == 0
        assert _count(session, "constraint_vocab", "constraint_key = '最多12个汉字'") == 0
    finally:
        trans.rollback()
        conn.close()


def test_matched_constraint_by_canonical_key() -> None:
    conn, trans, session = _begin()
    try:
        repo = ConstraintVocabRepository()
        created = repo.create(constraint_key="按部门筛选", status=ACTIVE, session=session)

        result = _service().match(
            _extracted(capabilities=[_cap("导出", "X", constraints=["按部门筛选"])]),
            session=session,
        )

        hit = result["constraints"]["matched"][0]
        assert hit["constraint_id"] == created["id"]
        assert hit["alias_hit"] is False  # 命中的是正式键
        assert result["constraints"]["unmatched"] == []
    finally:
        trans.rollback()
        conn.close()


def test_matched_constraint_by_alias_marks_alias_hit() -> None:
    """别名命中要标出来 —— 审核页据此提示「归到了哪个正式条件」。"""
    conn, trans, session = _begin()
    try:
        repo = ConstraintVocabRepository()
        canonical = repo.create(constraint_key="按部门筛选", status=ACTIVE, session=session)
        repo.add_alias(alias="按部门维度筛选", constraint_id=canonical["id"], session=session)

        result = _service().match(
            _extracted(capabilities=[_cap("导出", "Y", constraints=["按部门维度筛选"])]),
            session=session,
        )

        hit = result["constraints"]["matched"][0]
        assert hit["alias_hit"] is True
        assert hit["constraint_key"] == "按部门筛选"
    finally:
        trans.rollback()
        conn.close()


def test_duplicate_constraints_across_capabilities_are_deduped() -> None:
    conn, trans, session = _begin()
    try:
        result = _service().match(
            _extracted(
                capabilities=[
                    _cap("导出", "A", constraints=["按门店"]),
                    _cap("统计", "B", constraints=["按门店"]),
                ]
            ),
            session=session,
        )

        assert result["summary"]["constraint_unmatched"] == 1
    finally:
        trans.rollback()
        conn.close()


# ── 健壮性 ──────────────────────────────────────────────────────────────


def test_dirty_payload_does_not_raise() -> None:
    conn, trans, session = _begin()
    try:
        service = _service()
        for bad in ({}, {"capabilities": None}, {"capabilities": "xxx"}, {"capabilities": [1, 2]}):
            result = service.match(bad, session=session)
            assert result["capabilities"] == []
    finally:
        trans.rollback()
        conn.close()


def test_summary_counts_are_consistent() -> None:
    conn, trans, session = _begin()
    try:
        repo = CapabilityRepository()
        repo.create(action="导出", object_="已确认宾语", status=ACTIVE, session=session)
        repo.create(action="统计", object_="提案宾语2", status=PENDING_CONFIRMATION, session=session)

        result = _service().match(
            _extracted(
                capabilities=[
                    _cap("导出", "已确认宾语"),
                    _cap("统计", "提案宾语2"),  # 词表里有但是 pending → 匹配不上
                    _cap("创建", "全新宾语"),
                ]
            ),
            session=session,
        )

        summary = result["summary"]
        assert summary["capability_total"] == 3
        assert summary["capability_matched"] == 1
        assert summary["capability_proposed"] == 2
    finally:
        trans.rollback()
        conn.close()


def test_business_object_is_passed_through() -> None:
    conn, trans, session = _begin()
    try:
        result = _service().match(_extracted(business_object="巡检计划"), session=session)
        assert result["business_object"] == "巡检计划"
    finally:
        trans.rollback()
        conn.close()


# ── 溯源与清理（防止测试往真实库堆垃圾）────────────────────────────────
#
# 批次 2 起，提交需求会把能力候选写成提案 —— 于是**任何走真实提交路径的测试都会
# 往 capability 表写行**。实测跑一次测试套件就留下一行「(导出, 报表)」，
# 而既有清理只删 requirement_source。修法是让提案记住来源并级联删除，
# 这样既有的 `_purge_sources` 自动就完整了。


def _make_source(session: Session, suffix: str) -> int:
    source_id = 7_000_000_000_000_000 + abs(hash(suffix)) % 1_000_000_000
    session.execute(
        text(
            "INSERT INTO requirement_source (id, idempotency_key, source_type, original_text, "
            "original_payload, metadata, processing_status, submitted_at) "
            "VALUES (:id, :ik, 'web', 'x', CAST('{}' AS JSONB), CAST('{}' AS JSONB), "
            "'pending_review', NOW())"
        ),
        {"id": source_id, "ik": f"match-{suffix}"},
    )
    return source_id


def test_proposal_records_its_origin_source() -> None:
    conn, trans, session = _begin()
    try:
        source_id = _make_source(session, "origin")
        _service().match(
            _extracted(capabilities=[_cap("导出", "带来源宾语")]),
            source_id=source_id,
            session=session,
        )

        row = session.execute(
            text("SELECT origin_source_id FROM capability WHERE object = '带来源宾语'")
        ).scalar()
        assert row == source_id
    finally:
        trans.rollback()
        conn.close()


def test_deleting_the_source_cascades_away_its_proposals() -> None:
    """**测试清理的正确性依赖这条**：删掉来源，它提出的能力自动消失。"""
    conn, trans, session = _begin()
    try:
        source_id = _make_source(session, "cascade")
        _service().match(
            _extracted(capabilities=[_cap("导出", "级联宾语")]),
            source_id=source_id,
            session=session,
        )
        assert _count(session, "capability", "object = '级联宾语'") == 1

        session.execute(
            text("DELETE FROM requirement_source WHERE id = :id"), {"id": source_id}
        )

        assert _count(session, "capability", "object = '级联宾语'") == 0
    finally:
        trans.rollback()
        conn.close()


def test_proposal_without_source_is_not_created_by_mistake() -> None:
    """不传 source_id 时 origin 为空 —— 提醒调用方「务必传」是有原因的。

    这条记录的是**当前行为**：没来源的提案不会被级联清理，所以提交路径必须传。
    """
    conn, trans, session = _begin()
    try:
        _service().match(_extracted(capabilities=[_cap("导出", "无来源宾语")]), session=session)

        row = session.execute(
            text("SELECT origin_source_id FROM capability WHERE object = '无来源宾语'")
        ).scalar()
        assert row is None
    finally:
        trans.rollback()
        conn.close()
