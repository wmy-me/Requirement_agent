"""Worker 独立化（B5）。

**这一批的核心是「Worker 终于能自己消费了」。** 此前 `workers/` 只是个 HTTP 壳 ——
四个「手动触发一次 `process_pending`」的端点，**没有消费循环**，启动它不会消费任何事件。
真正在消费的是 API 进程 lifespan 里的内嵌消费者。

要钉住四件事：

1. **循环真的跑**（不是「端点能通」就算数）—— 看 `poll_count` 是否在涨；
2. **`/health` 如实报告「循环在不在跑」** —— 一个「进程活着但循环关了」的 Worker
   会让事件一直堆着，而探活看起来完全健康；
3. **`OUTBOX_CONSUMER_ENABLED=false` 时能正常启停** —— 这是 B5 推荐的生产形态
   （API 侧关掉内嵌消费），而它此前会**在关闭时报错**；
4. **失败重试有退避** —— 此前失败立刻回 pending，3 次重试全挤在 ~15 秒里。

打真实库（`claim_pending` 与退避都在 SQL 里）。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.worker.outbox import OutboxRepository
from requirement_agent.workers.tasks import app as worker_app

repo = OutboxRepository()
_PROBE_TYPE = "b5_probe_event"


def _new_probe_event() -> int:
    with SessionLocal() as session:
        event_id = session.execute(
            text(
                "INSERT INTO outbox_event (aggregate_type, aggregate_id, event_type, payload) "
                "VALUES ('probe', 'probe', :t, CAST('{}' AS JSONB)) RETURNING id"
            ),
            {"t": _PROBE_TYPE},
        ).scalar_one()
        session.commit()
    return int(event_id)


def _drop(event_id: int) -> None:
    with SessionLocal() as session:
        session.execute(text("DELETE FROM outbox_event WHERE id = :i"), {"i": event_id})
        session.commit()


def _row(event_id: int) -> dict:
    with SessionLocal() as session:
        return dict(
            session.execute(
                text(
                    "SELECT status, retry_count, next_attempt_at FROM outbox_event WHERE id = :i"
                ),
                {"i": event_id},
            ).mappings().one()
        )


# ── ① 循环真的在跑 ────────────────────────────────────────────────────────


def test_worker_runs_its_own_consumer_loop(monkeypatch) -> None:
    """**B5 的核心交付。** 启动 Worker 就会自己消费，不需要人反复 POST。

    用很短的轮询间隔（0.2s）验证循环在**推进**，而不是只验证端点能通。
    """
    monkeypatch.setattr(settings, "outbox_poll_interval", 0.2)
    monkeypatch.setattr(settings, "outbox_consumer_enabled", True)

    with TestClient(worker_app) as client:
        time.sleep(0.7)
        stats = client.get("/stats").json()["consumer"]

    assert stats["poll_count"] >= 2, "消费循环没有推进 —— Worker 还是那个手动触发的壳"
    assert stats["interval_seconds"] == 0.2


def test_worker_health_reports_loop_state(monkeypatch) -> None:
    monkeypatch.setattr(settings, "outbox_poll_interval", 0.2)
    monkeypatch.setattr(settings, "outbox_consumer_enabled", True)

    with TestClient(worker_app) as client:
        body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["consumer_running"] is True


# ── ② 关掉循环时，探活要如实说 ────────────────────────────────────────────


def test_worker_health_says_loop_is_off_when_disabled(monkeypatch) -> None:
    """**「进程活着」不等于「在干活」。** 只看 `status: ok` 会漏掉
    「Worker 起着但消费关了、事件一直堆着」这种故障。"""
    monkeypatch.setattr(settings, "outbox_consumer_enabled", False)

    with TestClient(worker_app) as client:
        body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["consumer_running"] is False, "循环没跑却报 running"


# ── ③ 关闭内嵌消费时能正常启停（两处都修过的 bug）────────────────────────


@pytest.mark.parametrize("which", ["worker", "api"])
def test_shutdown_is_clean_with_consumer_disabled(monkeypatch, which: str) -> None:
    """**回归**：`OUTBOX_CONSUMER_ENABLED=false` 时关闭进程会抛
    `RuntimeError: cannot join thread before it is started`。

    根因：lifespan 的 `finally` 无条件 `thread.join()`，而那个线程**从没被 start 过**。
    而这恰好是 B5 推荐的生产形态（API 侧关掉内嵌消费），**一按推荐配置就踩中**。

    这里对 API 与 Worker **两个 lifespan** 都验一遍 —— 那段代码是复制过去的，
    修一处漏一处的话这条会红。
    """
    monkeypatch.setattr(settings, "outbox_consumer_enabled", False)
    if which == "worker":
        from requirement_agent.workers.tasks import app as target
    else:
        from requirement_agent.api.app import app as target

    with TestClient(target) as client:  # 关闭发生在 with 退出时
        assert client.get("/health").status_code == 200
    # 走到这里没抛异常就算过


# ── ④ 失败重试的退避 ──────────────────────────────────────────────────────


def test_backoff_sequence_is_exponential_and_capped() -> None:
    assert repo._backoff_seconds(1) == settings.outbox_retry_backoff_seconds
    assert repo._backoff_seconds(2) == settings.outbox_retry_backoff_seconds * 2
    assert repo._backoff_seconds(3) == settings.outbox_retry_backoff_seconds * 4
    # 封顶，且不会因为指数过大而溢出
    assert repo._backoff_seconds(30) == settings.outbox_retry_max_backoff_seconds


def test_failed_event_is_backed_off(monkeypatch) -> None:
    """**退避的核心断言**：失败后回 pending，但**退避期内领不到它**。

    此前失败会立刻回 pending，下一个轮询周期（默认 5s）就重试 —— 3 次重试
    全挤在 ~15 秒里，对「远端 LLM 超时」几乎等于不重试。
    """
    monkeypatch.setattr(settings, "outbox_retry_backoff_seconds", 60.0)
    event_id = _new_probe_event()
    try:
        claimed = repo.claim_pending(limit=50, event_type=_PROBE_TYPE)
        assert [e.id for e in claimed] == [event_id]

        repo.mark_failed(claimed[0], error="probe failure")

        row = _row(event_id)
        assert row["status"] == "pending", "未达上限应回 pending 等待重试"
        assert row["retry_count"] == 1
        assert row["next_attempt_at"] is not None, "没有设退避时间 —— 会立刻重试"

        # 退避期内再领：应该一条都领不到
        assert repo.claim_pending(limit=50, event_type=_PROBE_TYPE) == []
    finally:
        _drop(event_id)


def test_new_event_has_no_backoff() -> None:
    """退避只作用于**失败重试**；新事件必须立即可领（`next_attempt_at IS NULL`）。"""
    event_id = _new_probe_event()
    try:
        assert _row(event_id)["next_attempt_at"] is None
        assert [e.id for e in repo.claim_pending(limit=50, event_type=_PROBE_TYPE)] == [event_id]
    finally:
        _drop(event_id)


def test_dead_letter_clears_backoff() -> None:
    """进死信是**终态**，不该留着退避时间 —— 那会让人以为它还会被重试。"""
    event_id = _new_probe_event()
    try:
        claimed = repo.claim_pending(limit=50, event_type=_PROBE_TYPE)
        # max_retries=1：第一次失败就到上限
        repo.mark_failed(claimed[0], error="probe failure", max_retries=1)

        row = _row(event_id)
        assert row["status"] == "dead_letter"
        assert row["next_attempt_at"] is None
    finally:
        _drop(event_id)


def test_stale_processing_is_reclaimed_regardless_of_backoff() -> None:
    """**僵尸回收不受退避影响** —— 那是「消费者崩了」，不是「失败了要等一会」。
    卡住它只会让事件更晚被处理。"""
    event_id = _new_probe_event()
    try:
        repo.claim_pending(limit=50, event_type=_PROBE_TYPE)  # 领成 processing
        with SessionLocal() as session:
            # 把锁定时间拨到很久以前，模拟消费者崩溃
            session.execute(
                text("UPDATE outbox_event SET locked_at = NOW() - INTERVAL '1 hour' WHERE id = :i"),
                {"i": event_id},
            )
            session.commit()

        reclaimed = repo.claim_pending(limit=50, event_type=_PROBE_TYPE)
        assert [e.id for e in reclaimed] == [event_id], "僵尸事件应该能被重领"
    finally:
        _drop(event_id)
