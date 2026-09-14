"""死信处理（重投 / 放弃）的集成测试，打真实库，自带清理。

守护的核心语义：**两个动作都只认 `dead_letter`**。不限定状态的话，一次误点的「重试」
会把正在处理或已完成的事件也重置回 pending —— 那是 embedding 重复写、文档重复切片。
"""

from sqlalchemy import text

from requirement_agent.common.snowflake import new_id
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.worker.outbox import OutboxRepository

repo = OutboxRepository()


def _set_status(event_id: int, status: str, *, retry_count: int = 3) -> None:
    with SessionLocal() as session:
        session.execute(
            text("UPDATE outbox_event SET status = :s, retry_count = :r WHERE id = :i"),
            {"s": status, "r": retry_count, "i": event_id},
        )
        session.commit()


def _read(event_id: int) -> dict:
    with SessionLocal() as session:
        return dict(
            session.execute(
                text("SELECT status, retry_count, last_error FROM outbox_event WHERE id = :i"),
                {"i": event_id},
            ).mappings().one()
        )


def _make_dead_letter() -> int:
    event_id = new_id()
    with SessionLocal() as session:
        session.execute(
            text(
                "INSERT INTO outbox_event (id, aggregate_type, aggregate_id, event_type, payload, "
                "status, retry_count, last_error) "
                "VALUES (:i, 'requirement_master', 'REQ-TEST', 'embedding_sync', '{}', 'dead_letter', 3, '模拟失败')"
            ),
            {"i": event_id},
        )
        session.commit()
    return event_id


def _purge(event_id: int) -> None:
    with SessionLocal() as session:
        session.execute(text("DELETE FROM outbox_event WHERE id = :i"), {"i": event_id})
        session.commit()


def test_retry_resets_dead_letter_to_pending() -> None:
    event_id = _make_dead_letter()
    try:
        assert repo.retry_dead_letter(event_id) is True

        row = _read(event_id)
        assert row["status"] == "pending"
        assert row["retry_count"] == 0
        assert row["last_error"] is None  # 清空错误，避免下一次失败时误以为是旧错
    finally:
        _purge(event_id)


def test_retry_refuses_non_dead_letter() -> None:
    """待处理中的事件不能被「重试」重置 —— 那会凭空多跑一次。"""
    event_id = _make_dead_letter()
    try:
        _set_status(event_id, "pending")

        assert repo.retry_dead_letter(event_id) is False
        assert _read(event_id)["status"] == "pending"
    finally:
        _purge(event_id)


def test_discard_marks_terminal_state() -> None:
    event_id = _make_dead_letter()
    try:
        assert repo.discard_dead_letter(event_id) is True
        assert _read(event_id)["status"] == "discarded"
        # 幂等：已经放弃过的不再重复计入
        assert repo.discard_dead_letter(event_id) is False
    finally:
        _purge(event_id)


def test_discard_refuses_non_dead_letter() -> None:
    event_id = _make_dead_letter()
    try:
        _set_status(event_id, "completed")

        assert repo.discard_dead_letter(event_id) is False
    finally:
        _purge(event_id)


def test_count_by_status_includes_all_known_keys() -> None:
    counts = repo.count_by_status()

    assert set(counts) == {"pending", "processing", "completed", "dead_letter", "discarded"}
    assert all(isinstance(value, int) for value in counts.values())
    # `sent` / `failed` 是建表遗留、从未写入，故意不列进统计
    assert "sent" not in counts and "failed" not in counts
