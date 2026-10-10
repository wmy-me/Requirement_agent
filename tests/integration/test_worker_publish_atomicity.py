"""Worker 发布边界：来源置待审 + run 终态必须是**同一个 token 事务**。

改造前是「先 `update_status` 再 `finish`」两次独立事务，中间有一个真实的窗口：
被接管（租约过期重认领 / 人工 pause）的执行者可能已经写完了 `pending_review`，
却再也写不进 run 终态 —— 库里留下「来源说分析完了、run 说还在跑」的错位，
而且这条陈旧结论还会被审核页当成正常结果消费。

本文件的重点是**控制竞态时机**：接管恰好落在发布边界（分析已算完、发布还没发生），
断言既没有陈旧结论、也没有终态被提交。用真库真行，不 mock 发布路径本身。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.domain.requirement import RequirementSource
from requirement_agent.infrastructure.db.repositories import AgentRunRepository
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.llm.invocation import current_run_id


def _source(marker: str) -> RequirementSource:
    return RequirementSource(
        source_type="web",
        original_text=f"{marker}：按区域生成巡检计划并拍照上传。",
        requester_name="publish 边界测试",
        idempotency_key=f"publish-{uuid.uuid4().hex}",
    )


def _capability(marker: str) -> dict[str, str]:
    """每次跑用不同的 (action, object)：避免撞上别的测试留下的能力行。"""
    return {"raw_text": marker, "action": marker, "object": "Excel"}


def _result(marker: str) -> dict[str, object]:
    """形状对齐 `run_analysis` 的真实返回（字段名就是消费者读的那些）。"""
    return {
        "extracted": {
            "requirement_title": marker, "summary": "按区域生成巡检计划并拍照上传",
            "business_domain": "workflow", "capabilities": [_capability(marker)],
        },
        "analysis": {"duplicate": False, "related": False, "conflict": False, "independent": True},
        "risk": {"quality_risk": "low", "change_risk": "low", "technical_impact_risk": "low", "confidence": 0.9},
        "candidates": [],
        "retrieval": {"filters": {}},
        "intent": "requirement",
        "route_history": [{"action": "extract"}],
        "route_reason": "先抽取",
        "error_state": None,
        "decision": "pending_review",
        "run_events": [{"event_type": "node_started", "node": "extract", "payload": {}}],
        "tool_calls": [
            {"tool": "extract_requirement", "params": {"source_id": 1}, "status": "success",
             "duration_ms": 1.0, "count": 1, "message": None, "sample": None}
        ],
    }


@pytest.fixture
def cleanup():
    made: list[int] = []
    yield made
    if made:
        with SessionLocal() as session:
            # 能力提案带 ON DELETE CASCADE 挂在 origin_source_id 上，随来源一起清掉。
            session.execute(text("DELETE FROM requirement_source WHERE id = ANY(:ids)"), {"ids": made})
            session.commit()


def _source_row(source_id: int) -> dict[str, object]:
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT processing_status, metadata FROM requirement_source WHERE id = :i"),
            {"i": source_id},
        ).mappings().one()
    return dict(row)


def _capability_rows(source_id: int) -> int:
    with SessionLocal() as session:
        return int(
            session.execute(
                text("SELECT COUNT(*) FROM capability WHERE origin_source_id = :i"), {"i": source_id}
            ).scalar()
            or 0
        )


def _takeover(run_id: str) -> str:
    """模拟「租约过期后被另一个执行者原子认领」，返回新 token。"""
    with SessionLocal() as session:
        session.execute(
            text("UPDATE agent_run SET lease_expires_at = NOW() - interval '1 second' "
                 "WHERE run_id = CAST(:r AS UUID)"),
            {"r": run_id},
        )
        session.commit()
    claimed = AgentRunRepository().claim_run(run_id)
    assert claimed is not None and claimed["execution_token"], "接管必须成功，否则测试前提不成立"
    return str(claimed["execution_token"])


# ── ① 接管恰好落在发布边界 ────────────────────────────────────────────────


def test_takeover_at_publish_boundary_commits_neither_result_nor_terminal_state(monkeypatch, cleanup) -> None:
    """分析算完、还没发布时被接管：**来源与 run 都不许留下这次执行的痕迹**。

    这里刻意在 `run_analysis` 内部（也就是发布之前）制造接管，让「旧执行者手里的
    结论」与「数据库里的所有权」在发布那一刻已经不一致 —— 这正是改造前会写坏的窗口。
    """
    marker = f"race-{uuid.uuid4().hex[:8]}"
    takeover: dict[str, str] = {}

    def racing_analysis(**kwargs):
        run_id = str(current_run_id())
        takeover["token"] = _takeover(run_id)
        return _result(marker)

    monkeypatch.setattr(
        "requirement_agent.application.requirement_service.run_analysis", racing_analysis
    )

    service = RequirementService()
    with pytest.raises(RuntimeError, match="ownership lost"):
        service.submit_requirement(_source(marker))

    with SessionLocal() as session:
        source_id = session.execute(
            text("SELECT id FROM requirement_source WHERE idempotency_key LIKE 'publish-%' "
                 "ORDER BY id DESC LIMIT 1")
        ).scalar()
        run = session.execute(
            text("SELECT run_id, status, execution_token FROM agent_run WHERE source_id = :i"),
            {"i": source_id},
        ).mappings().one()
        events = session.execute(
            text("SELECT event_type FROM agent_run_event WHERE run_id = CAST(:r AS UUID)"),
            {"r": run["run_id"]},
        ).scalars().all()
    cleanup.append(int(source_id))

    row = _source_row(int(source_id))
    assert row["processing_status"] != "pending_review", "被接管的执行者不得发布陈旧结果"
    assert "analysis" not in row["metadata"] and "extracted" not in row["metadata"]
    assert "capability_match" not in row["metadata"], "同事务的能力提案不得半截落库"
    assert _capability_rows(int(source_id)) == 0, "陈旧提案必须随发布事务一起回滚"
    assert run["status"] == "running", "run 归新执行者所有，旧执行者不得收尾"
    assert str(run["execution_token"]) == takeover["token"]
    assert "run_completed" not in events, "旧执行者不得写终态事件"


# ── ② 同一事务：发布中途失败则来源也不改 ──────────────────────────────────


def test_publish_rolls_back_source_when_the_single_transaction_fails(monkeypatch, cleanup) -> None:
    """发布事务内部出错时，**已经执行过的来源 UPDATE 也要回滚**（同事务的证据）。

    如果发布还是两次事务，这条断言会失败：来源早就提交成 `pending_review` 了。
    """
    marker = f"rollback-{uuid.uuid4().hex[:8]}"
    original = AgentRunRepository._append_events_in_session

    def failing_append(self, session, run_id, events, *, commit=True):
        if any(str(event.get("event_type")) == "run_completed" for event in events):
            raise RuntimeError("injected failure after the source update")
        return original(self, session, run_id, events, commit=commit)

    monkeypatch.setattr(AgentRunRepository, "_append_events_in_session", failing_append)
    monkeypatch.setattr(
        "requirement_agent.application.requirement_service.run_analysis",
        lambda **kwargs: _result(marker),
    )

    with pytest.raises(RuntimeError, match="ownership lost"):
        RequirementService().submit_requirement(_source(marker))

    with SessionLocal() as session:
        source_id = int(
            session.execute(
                text("SELECT id FROM requirement_source WHERE idempotency_key LIKE 'publish-%' "
                     "ORDER BY id DESC LIMIT 1")
            ).scalar()
        )
        run_status = session.execute(
            text("SELECT status FROM agent_run WHERE source_id = :i"), {"i": source_id}
        ).scalar()
    cleanup.append(source_id)

    assert _source_row(source_id)["processing_status"] != "pending_review", (
        "同一事务里来源 UPDATE 必须跟着回滚"
    )
    assert run_status == "running"
    assert _capability_rows(source_id) == 0


# ── ③ 正常路径：三件事在同一事务里一次提交 ────────────────────────────────


def test_successful_publication_commits_source_run_and_event_together(monkeypatch, cleanup) -> None:
    marker = f"ok-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(
        "requirement_agent.application.requirement_service.run_analysis",
        lambda **kwargs: _result(marker),
    )

    result = RequirementService().submit_requirement(_source(marker))
    source_id = int(result["source_id"])
    cleanup.append(source_id)

    row = _source_row(source_id)
    assert row["processing_status"] == "pending_review"
    assert result["status"] == "pending_review"
    # 事务内钩子写下的提案：真实存在，且 metadata 里的 id 是真实 id（不是预演占位）。
    assert _capability_rows(source_id) == 1
    hits = row["metadata"]["capability_match"]["capabilities"]
    assert hits and hits[0]["capability_id"] is not None and hits[0]["proposed"] is True

    run = AgentRunRepository().list_by_source(source_id)[0]
    assert run["status"] == "waiting_review"
    assert run["ended_at"] is not None
    kinds = [event["event_type"] for event in AgentRunRepository().list_events(str(run["run_id"]))]
    assert kinds[-1] == "run_completed"


# ── ④ 发布前的既有写入本来就受 token 约束（审计结论）───────────────────────


def test_stale_executor_cannot_append_events_tool_calls_or_checkpoints() -> None:
    """`record_events` / `record_tool_calls` / `checkpoint` 都在 token 行锁下写。

    这三件事发生在发布之前，本文件审计它们是否会给被接管的执行者留下陈旧结果。
    结论是**不会**：三条写入都带 `(run_id, status='running', execution_token)` 谓词，
    接管后旧 token 全部写不进（这里用真库直接钉住，而不是靠读代码）。
    """
    repo = AgentRunRepository()
    run = repo.create_run(run_type="conversation", meta={"task_type": "assistant"})
    run_id = str(run["run_id"])
    token = str(repo.claim_run(run_id)["execution_token"])
    try:
        _takeover(run_id)  # 旧 token 就此失权
        assert repo.append_events_if_owned(run_id, token, [
            {"event_type": "tool_started", "node": "assistant", "payload": {}}
        ]) == []
        assert repo.record_tool_invocations_if_owned(run_id, token, [
            {"tool": "list_requirements", "params": {}, "status": "success", "duration_ms": 1}
        ]) == 0
        assert repo.checkpoint_if_owned(run_id, token, {"version": 1, "state": {}}) is False
        assert repo.finish_with_event_if_owned(
            run_id, token, status="completed", event={"event_type": "run_completed", "node": None, "payload": {}}
        ) is False
    finally:
        with SessionLocal() as session:
            session.execute(text("DELETE FROM agent_run WHERE run_id = CAST(:r AS UUID)"), {"r": run_id})
            session.commit()
