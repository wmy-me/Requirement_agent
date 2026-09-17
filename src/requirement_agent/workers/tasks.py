"""独立 Worker 进程（B5）。

**它是什么。** 一个**能自己消费 outbox** 的进程 —— 此前它只是个 HTTP 壳：
四个「手动触发一次 `process_pending`」的端点，**没有消费循环**，启动它不会消费任何事件，
得人反复 POST。那时真正在消费的是 **API 进程 lifespan 里的内嵌消费者**。

**B5 起它有了自己的循环**（`OutboxConsumer`），于是长任务可以从 API 进程里拆出去：
LLM 分析一次要跑 4 个步骤、几十秒，占着 API 进程的线程和连接池没有任何好处。

## 两种形态，可以同时跑

| 形态 | 谁在消费 | 开关 |
|---|---|---|
| **独立 Worker（推荐）** | 本进程的循环 | `OUTBOX_CONSUMER_ENABLED=true`（默认） |
| **API 内嵌（兼容）** | API 进程 lifespan 的线程 | 同上 |

⚠️ **两者同时跑是安全的** —— `OutboxRepository.claim_pending` 用
`FOR UPDATE SKIP LOCKED` 领取事件，两个消费者各领一部分、**不会重复处理**。
只是会多一份空轮询。推荐的生产形态是「独立 Worker 跑消费 + API 侧关掉内嵌」。

## 启动

```bash
python -m requirement_agent.workers        # 监听 :8200
```

`/stats` 暴露消费循环的运行统计（与 `/api/v1/ops/outbox` 的 `consumer` 字段同源）。

## 为什么还保留那四个手动触发端点

它们是**运维用的单步工具**（`POST /tasks/embedding/process` 等），
在排查「某类事件卡住了」时比等循环轮询更快。**不是主路径**，
且它们与循环领取的是同一批事件、同样走 SKIP LOCKED，不会冲突。
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from requirement_agent.api.dependencies import requirement_analysis_task
from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.worker.consumer import OutboxConsumer
from requirement_agent.infrastructure.worker.outbox import OutboxRepository
from requirement_agent.infrastructure.worker.tasks import DocumentChunkingTask, EmbeddingTask

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启停本进程自己的消费循环。

    与 API 进程 lifespan 的写法一致（`api/app.py`）：循环跑在守护线程里，
    统计挂在 `app.state` 供 `/stats` 读。
    """
    # 显式注入分析任务：不注入的话 `requirement_analysis` 事件不会被消费，
    # 渠道接入的需求会永远停在 received（consumer 会为此打告警日志）。
    consumer = OutboxConsumer(analysis_task=requirement_analysis_task)
    app.state.outbox_consumer = consumer
    thread = threading.Thread(target=consumer.start, name="worker-outbox-consumer", daemon=True)
    # 线程本身也挂上去：`/health` 要报的是「循环真的在跑吗」，
    # 而 `consumer.stats()["running"]` 只是 `not _stop` —— 一个**从没启动过**的消费者
    # 也会报 True。拿线程的 is_alive() 才是真信号。
    app.state.outbox_thread = thread

    if settings.outbox_consumer_enabled:
        thread.start()
        logger.info(
            "event=worker_consumer_started interval=%s batch=%s",
            settings.outbox_poll_interval, settings.outbox_poll_batch,
        )
    else:
        logger.warning(
            "event=worker_consumer_disabled OUTBOX_CONSUMER_ENABLED=false —— "
            "本进程不会消费任何事件（那四个手动触发端点仍可用）"
        )
    try:
        yield
    finally:
        consumer.stop()
        # ⚠️ **必须先判 `is_alive()`** —— `Thread.join()` 在**没启动过**的线程上会抛
        # `RuntimeError: cannot join thread before it is started`。而
        # `OUTBOX_CONSUMER_ENABLED=false` 时正是「不启动」，于是**关进程就报错**。
        # 实测踩到；而这恰好是 B5 推荐的生产形态（API 侧关掉内嵌消费）。
        # is_alive() 为假有两种情况——没启动过、或已经跑完——两种都不需要 join。
        if thread.is_alive():
            thread.join(timeout=settings.outbox_poll_interval + 1)


app = FastAPI(
    title="Requirement Agent Worker",
    version="0.1.0",
    description="Outbox worker and async task entrypoint.",
    lifespan=lifespan,
)


@app.get("/health")
async def healthcheck() -> dict[str, object]:
    """探活：顺带给出「消费循环到底在不在跑」。

    ⚠️ **只回 `{"status": "ok"}` 是不够的** —— 一个「进程活着但消费循环关了」的 Worker
    会让事件一直堆着，而探活看起来完全健康。所以这里把 `consumer.running` 一起报出来。
    """
    thread = getattr(app.state, "outbox_thread", None)
    consumer = getattr(app.state, "outbox_consumer", None)
    # ⚠️ 用线程的 is_alive()，不用 consumer.stats()["running"] —— 后者是 `not _stop`，
    # 一个从没启动过的消费者也会报 True（实测）。
    return {
        "status": "ok",
        "consumer_running": bool(thread.is_alive()) if thread is not None else False,
        "poll_count": int((consumer.stats() if consumer is not None else {}).get("poll_count", 0)),
    }


@app.get("/stats")
async def stats() -> dict[str, object]:
    """消费循环的运行统计（与 `/api/v1/ops/outbox` 的 `consumer` 字段同源）。"""
    consumer = getattr(app.state, "outbox_consumer", None)
    return {
        "consumer": consumer.stats() if consumer is not None else None,
        "counts": OutboxRepository().count_by_status(),
    }


@app.post("/tasks/embedding/process")
async def process_embedding_events(limit: int = 20) -> dict[str, object]:
    task = EmbeddingTask()
    results = task.process_pending(limit=limit)
    return {"status": "ok", "results": results}


@app.post("/tasks/document-chunk/process")
async def process_document_chunk_events(limit: int = 20) -> dict[str, object]:
    task = DocumentChunkingTask()
    results = task.process_pending(limit=limit)
    return {"status": "ok", "results": results}


@app.post("/tasks/requirement-analysis/process")
async def process_requirement_analysis_events(limit: int = 20) -> dict[str, object]:
    """消费渠道接入的分析事件，把来源从 received 推进到 pending_review。"""
    results = requirement_analysis_task.process_pending(limit=limit)
    return {"status": "ok", "results": results}


@app.get("/tasks/dead-letter")
async def list_dead_letter_events(limit: int = 50) -> dict[str, object]:
    events = OutboxRepository().list_dead_letters(limit=limit)
    return {
        "items": [
            {
                "id": event.id,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "retries": event.retries,
                "status": event.status,
                "last_error": event.last_error,
            }
            for event in events
        ]
    }
