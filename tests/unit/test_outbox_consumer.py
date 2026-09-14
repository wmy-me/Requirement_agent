"""OutboxConsumer 消费循环的单元测试（用 fake 任务，不碰数据库）。"""

import logging

from requirement_agent.infrastructure.worker.consumer import (
    _EMPTY_ROUNDS_BEFORE_BACKOFF,
    _MAX_BACKOFF_MULTIPLIER,
    OutboxConsumer,
)


class FakeTask:
    """可注入的假任务：记录调用次数，可选固定返回结果。"""

    def __init__(self, result: list[str] | None = None) -> None:
        self.calls = 0
        self.result = result or []

    def process_pending(self, *, limit: int = 20) -> list[str]:
        self.calls += 1
        return self.result


def test_delay_backs_off_after_empty_rounds() -> None:
    """连续空转应指数退避（上限受乘数保护），领到事件后回到基础间隔。"""
    consumer = OutboxConsumer(
        embedding_task=FakeTask(),
        chunk_task=FakeTask(),
        interval=1.0,
        batch=10,
    )
    # 前 _EMPTY_ROUNDS_BEFORE_BACKOFF 轮空转保持基础间隔
    for _ in range(_EMPTY_ROUNDS_BEFORE_BACKOFF):
        assert consumer._delay_after(False) == 1.0
    # 之后每轮空转 ×2，直到封顶 _MAX_BACKOFF_MULTIPLIER
    prev = consumer._delay_after(False)
    assert prev == 2.0
    for _ in range(6):
        delay = consumer._delay_after(False)
        assert delay >= prev
        prev = delay
    assert prev == 1.0 * _MAX_BACKOFF_MULTIPLIER
    # 领到事件后重置回基础间隔
    assert consumer._delay_after(True) == 1.0


def test_poll_once_calls_both_tasks_and_reports_work() -> None:
    """一轮消费同时扫描 embedding 与 document-chunk，任一成功即视为有产出。"""
    embedding = FakeTask(result=["processed:1:REQ"])
    chunk = FakeTask()
    consumer = OutboxConsumer(embedding_task=embedding, chunk_task=chunk, interval=0.0, batch=10)
    assert consumer._poll_once() is True
    assert embedding.calls == 1
    assert chunk.calls == 1


def test_poll_once_survives_task_exception() -> None:
    """单个任务抛异常不中断整轮，返回 False（无产出），继续走退避。"""

    class BoomTask:
        def process_pending(self, *, limit: int = 20) -> list[str]:
            raise RuntimeError("transient")

    consumer = OutboxConsumer(embedding_task=BoomTask(), chunk_task=FakeTask(), interval=0.0, batch=10)
    assert consumer._poll_once() is False


def test_poll_once_counts_by_result_prefix(caplog) -> None:
    """三个计数按结果摘要前缀分别累加——此前这些摘要被 len() 直接丢掉了。

    摘要形如 `processed:{id}:{agg}` / `pending:{id}:{agg}`（失败未达上限、退回重试）
    / `dead_letter:{id}:{agg}`（达到上限）。
    """
    embedding = FakeTask(result=["processed:1:REQ-A", "processed:2:REQ-B"])
    chunk = FakeTask(result=["pending:3:DOC", "dead_letter:4:DOC"])
    consumer = OutboxConsumer(embedding_task=embedding, chunk_task=chunk, interval=0.0, batch=10)

    with caplog.at_level(logging.INFO):
        assert consumer._poll_once() is True

    assert consumer.processed_total == 2
    assert consumer.retried_total == 1
    assert consumer.dead_letter_total == 1
    assert consumer.last_active_at is not None
    assert any("event=outbox_poll" in record.message for record in caplog.records)


def test_poll_once_stays_silent_and_uncounted_when_idle(caplog) -> None:
    """空转既不加计数也不打日志：该循环默认 5 秒一轮，空转也打会把日志刷满。"""
    consumer = OutboxConsumer(embedding_task=FakeTask(), chunk_task=FakeTask(), interval=0.0, batch=10)

    with caplog.at_level(logging.INFO):
        assert consumer._poll_once() is False

    assert consumer.processed_total == 0
    assert consumer.last_active_at is None
    assert not any("event=outbox_poll" in record.message for record in caplog.records)


def test_stats_reports_counters() -> None:
    consumer = OutboxConsumer(
        embedding_task=FakeTask(result=["processed:1:REQ"]), chunk_task=FakeTask(), interval=0.0, batch=10
    )
    consumer._poll_once()

    stats = consumer.stats()

    assert stats["processed_total"] == 1
    assert stats["poll_count"] == 1
    assert stats["running"] is True