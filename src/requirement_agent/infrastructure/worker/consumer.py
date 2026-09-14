"""后台 outbox 消费循环。

把「申请表里的 pending 事件」真正消费掉：持续把 `embedding_sync`、`document_chunk_sync`
与 `requirement_analysis` 三类事件取出来跑对应的异步任务，支撑语义检索与渠道需求的分析。

设计要点：
- 与业务写分离：本循环持有自己的 Session/事务，不影响 API 请求路径。
- 频率与退避：默认每 `outbox_poll_interval` 秒扫一次；若连续若干轮无待处理事件，
  按 `outbox_poll_interval * 2^k`（上限 `outbox_max_backoff`）指数退避，减少空轮询
  对数据库的无谓开销；一旦再次领到事件即回到最快间隔。
- 在独立线程跑，避免阻塞事件循环/阻塞 worker 的请求响应。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from requirement_agent.common.time import as_display_iso, utc_now
from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.worker.tasks import (
    DocumentChunkingTask,
    EmbeddingTask,
    RequirementAnalysisTask,
)

logger = logging.getLogger(__name__)

_EMPTY_ROUNDS_BEFORE_BACKOFF = 3
_MAX_BACKOFF_MULTIPLIER = 1 << 5  # 最多退避到 32 × 基础间隔


class OutboxConsumer:
    """可启动/停止的后台循环，持续消费 outbox 中的待处理任务。"""

    def __init__(
        self,
        *,
        embedding_task: EmbeddingTask | None = None,
        chunk_task: DocumentChunkingTask | None = None,
        analysis_task: RequirementAnalysisTask | None = None,
        interval: float | None = None,
        batch: int | None = None,
    ) -> None:
        self.embedding_task = embedding_task or EmbeddingTask()
        self.chunk_task = chunk_task or DocumentChunkingTask()
        # 分析任务要注入应用层服务，无法在此自建。未注入时该类事件不会被消费，
        # 故显式告警——否则会变成「渠道落了库却永远停在 received」的无声故障。
        self.analysis_task = analysis_task
        if analysis_task is None:
            logger.warning(
                "event=analysis_task_missing 未注入 RequirementAnalysisTask，"
                "requirement_analysis 事件不会被消费"
            )
        self.interval = settings.outbox_poll_interval if interval is None else interval
        self.batch = settings.outbox_poll_batch if batch is None else batch
        self._stop = False
        self._poll_count = 0
        self._last_empty_rounds = 0
        # 累计计量（进程内，重启归零）：循环此前只算了个局部 `done` 用来返回 bool，
        # 处理了多少、退回了多少、死了多少全部不可见。
        self.processed_total = 0
        self.retried_total = 0
        self.dead_letter_total = 0
        self.last_active_at: datetime | None = None

    def start(self) -> None:
        """启动循环（阻塞调用方；常以线程/后台任务方式运行）。"""
        while not self._stop:
            worked = self._poll_once()
            if self._stop:
                break
            delay = self._delay_after(worked)
            try:
                time.sleep(delay)
            except KeyboardInterrupt:  # pragma: no cover - 交互式显式退出
                break

    def stop(self) -> None:
        """请求停止：当前 sleep 结束后退出循环。"""
        self._stop = True

    def _tasks(self) -> list[tuple[Any, str]]:
        """本轮要跑的任务与它们的日志标签（顺序与注册一致）。"""
        pairs: list[tuple[Any, str]] = [(self.embedding_task, "embedding"), (self.chunk_task, "document-chunk")]
        if self.analysis_task is not None:
            pairs.append((self.analysis_task, "requirement-analysis"))
        return pairs

    def _drain(self, task: Any, label: str) -> tuple[int, int, int]:
        """跑一个任务的 process_pending，返回 (成功, 退回重试, 转死信) 三个计数。

        结果摘要是 `processed:{id}:{agg}` / `pending:{id}:{agg}` / `dead_letter:{id}:{agg}`
        ——此前这些摘要被 `len()` 直接丢掉，于是循环的成功路径完全静默。
        """
        try:
            results = task.process_pending(limit=self.batch)
        except Exception as exc:  # noqa: BLE001 - 消费失败不应终止整个循环
            logger.exception("%s outbox 消费失败（第 %d 次）: %s", label, self._poll_count, exc)
            return 0, 0, 0
        texts = [str(item) for item in results]
        return (
            sum(1 for t in texts if t.startswith("processed:")),
            sum(1 for t in texts if t.startswith("pending:")),
            sum(1 for t in texts if t.startswith("dead_letter:")),
        )

    def _poll_once(self) -> bool:
        """执行一轮消费，返回是否领到并处理了任何事件。"""
        self._poll_count += 1
        processed = retried = dead = 0
        for task, label in self._tasks():
            p, r, d = self._drain(task, label)
            processed += p
            retried += r
            dead += d

        worked = (processed + retried + dead) > 0
        if worked:
            self.processed_total += processed
            self.retried_total += retried
            self.dead_letter_total += dead
            self.last_active_at = utc_now()
            # 只在真有事件时打一行：该循环默认 5 秒一轮，空转也打会把日志刷满
            logger.info(
                "event=outbox_poll processed=%d retried=%d dead_letter=%d",
                processed,
                retried,
                dead,
            )
        return worked

    def stats(self) -> dict[str, object]:
        """消费循环的运行快照，供运维面板展示（进程内累计，重启归零）。"""
        return {
            "running": not self._stop,
            "poll_count": self._poll_count,
            "processed_total": self.processed_total,
            "retried_total": self.retried_total,
            "dead_letter_total": self.dead_letter_total,
            "last_active_at": as_display_iso(self.last_active_at),
            "interval_seconds": self.interval,
            "batch": self.batch,
        }

    def _delay_after(self, worked: bool) -> float:
        """按上一轮是否有事件决定本次 sleep 时长（空转时指数退避）。"""
        if worked:
            self._last_empty_rounds = 0
            return self.interval
        self._last_empty_rounds += 1
        if self._last_empty_rounds <= _EMPTY_ROUNDS_BEFORE_BACKOFF:
            return self.interval
        multiplier = min(1 << (self._last_empty_rounds - _EMPTY_ROUNDS_BEFORE_BACKOFF), _MAX_BACKOFF_MULTIPLIER)
        return self.interval * multiplier


__all__ = ["OutboxConsumer"]