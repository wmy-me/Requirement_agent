"""run 的阶段与断点落库（对话状态机 B 批：checkpoint，先不丢结果、不做续跑）。

核心语义：`stage` 用「已完成态」命名，`checkpoint` 是逐阶段累积的产物快照。
`update_run` 只更新**传了的字段**——否则某个阶段的写入会覆盖掉别的阶段已经存好的断点。
"""

import uuid

import pytest
from sqlalchemy import text

from requirement_agent.api.dependencies import chat_repo
from requirement_agent.infrastructure.db.session import SessionLocal


@pytest.fixture()
def run_row() -> dict[str, object]:
    """一个一次性会话里的 run，用完连会话一起删。"""
    conv = str(chat_repo.create_conversation(actor_id="ckpt-test", title="断点测试")["id"])
    run = chat_repo.create_run(conversation_id=conv, client_message_id="m-1", status="running")
    try:
        yield run
    finally:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM agent_run WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv}
            )
            session.execute(
                text("DELETE FROM agent_conversation WHERE id = CAST(:c AS UUID)"), {"c": conv}
            )
            session.commit()


def test_fresh_run_starts_at_queued_with_empty_checkpoint(run_row) -> None:
    assert run_row["stage"] == "queued"
    assert run_row["checkpoint"] == {}


def test_update_progress_writes_stage_and_checkpoint(run_row) -> None:
    run_id = run_row["run_id"]

    chat_repo.update_run(
        run_id=run_id, status="running", stage="extracted",
        checkpoint={"extracted": {"requirement_title": "登录增强"}},
    )

    row = chat_repo.get_run(run_id)
    assert row["stage"] == "extracted"
    assert row["checkpoint"]["extracted"]["requirement_title"] == "登录增强"


def test_partial_update_does_not_clobber_existing_checkpoint(run_row) -> None:
    """只传 stage（不传 checkpoint）不能把已存的断点清掉。"""
    run_id = run_row["run_id"]
    chat_repo.update_run(
        run_id=run_id, status="running", stage="extracted",
        checkpoint={"extracted": {"requirement_title": "登录增强"}},
    )

    # 只推进 stage，不带 checkpoint
    chat_repo.update_run(run_id=run_id, status="running", stage="retrieved")

    row = chat_repo.get_run(run_id)
    assert row["stage"] == "retrieved"
    assert row["checkpoint"]["extracted"]["requirement_title"] == "登录增强"  # 还在


def test_cumulative_checkpoint_keeps_all_stages(run_row) -> None:
    """save_progress 每次传「已算出的全部字段」，调用方负责累积 —— 最终断点应含全量。"""
    run_id = run_row["run_id"]
    chat_repo.update_run(run_id=run_id, status="running", stage="extracted",
                         checkpoint={"extracted": {"x": 1}})
    chat_repo.update_run(run_id=run_id, status="running", stage="retrieved",
                         checkpoint={"extracted": {"x": 1}, "candidates": [{"k": "REQ-1"}]})
    chat_repo.update_run(run_id=run_id, status="running", stage="assessed",
                         checkpoint={"extracted": {"x": 1}, "candidates": [{"k": "REQ-1"}],
                                     "analysis": {"duplicate": False}, "risk": {"quality_risk": "low"}})

    row = chat_repo.get_run(run_id)
    assert row["stage"] == "assessed"
    assert set(row["checkpoint"].keys()) == {"extracted", "candidates", "analysis", "risk"}


def test_completed_run_marks_done_stage(run_row) -> None:
    run_id = run_row["run_id"]
    chat_repo.update_run(run_id=run_id, status="completed", stage="done")

    row = chat_repo.get_run(run_id)
    assert row["status"] == "completed"
    assert row["stage"] == "done"
