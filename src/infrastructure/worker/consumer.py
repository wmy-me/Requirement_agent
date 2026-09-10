"""后台 outbox 消费循环。

把「申请表里的 pending 事件」真正消费掉：持续把 `embedding_sync` 与
`document_chunk_sync` 两类事件取出来跑对应的异步任务，支撑语义检索。

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

from src.config.settings import settings
from src.infrastructure.worker.tasks import DocumentChunkingTask, EmbeddingTask

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
        interval: float | None = None,
        batch: int | None = None,
    ) -> None:
        self.embedding_task = embedding_task or EmbeddingTask()
        self.chunk_task = chunk_task or DocumentChunkingTask()
        self.interval = settings.outbox_poll_interval if interval is None else interval
        self.batch = settings.outbox_poll_batch if batch is None else batch
        self._stop = False
        self._poll_count = 0
        self._last_empty_rounds = 0

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

    def _poll_once(self) -> bool:
        """执行一轮消费，返回是否领到并处理了任何事件。"""
        self._poll_count += 1
        done = 0
        try:
            done += len(self.embedding_task.process_pending(limit=self.batch))
        except Exception as exc:  # noqa: BLE001 - 消费失败不应终止整个循环
            logger.exception("embedding outbox 消费失败（第 %d 次）: %s", self._poll_count, exc)
        try:
            done += len(self.chunk_task.process_pending(limit=self.batch))
        except Exception as exc:  # noqa: BLE001
            logger.exception("document-chunk outbox 消费失败（第 %d 次）: %s", self._poll_count, exc)
        return done > 0

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