"""雪花算法 ID 生成器（snowflake）。

id 布局（64 位，符号位恒 0，故为正整数）：
    41 位毫秒时间戳（自 EPOCH 起） | 10 位 worker_id | 12 位同毫秒序列
生成 id 为 63 位正整数，最大约 9.2e18，在 PostgreSQL BIGINT (2^63-1) 以内，可直接存 BIGINT 列。

特点：
- 线程安全（threading.Lock）。
- 进程内按生成顺序严格递增（趋势递增）。
- 同毫秒序列耗尽时自旋等待下一毫秒。
- 时钟回拨时沿用上次时间戳（保证单调不倒退），不抛错。
"""

from __future__ import annotations

import threading
import time

from requirement_agent.config.settings import settings

# 时间戳起始（UTC）：2025-01-01 00:00:00.000，毫秒级。41 位容量覆盖约 69 年。
_EPOCH_MS = 1735689600000

_WORKER_BITS = 10
_SEQUENCE_BITS = 12
_MAX_WORKER_ID = (1 << _WORKER_BITS) - 1  # 1023
_MAX_SEQUENCE = (1 << _SEQUENCE_BITS) - 1  # 4095

_WORKER_SHIFT = _SEQUENCE_BITS
_TIMESTAMP_SHIFT = _WORKER_BITS + _SEQUENCE_BITS


def _real_clock_ms() -> int:
    """当前 UTC 毫秒时间戳。"""
    return int(time.time() * 1000)


class Snowflake:
    """线程安全的雪花 id 生成器。"""

    def __init__(
        self,
        worker_id: int = 0,
        *,
        epoch_ms: int = _EPOCH_MS,
        now_fn=None,
    ) -> None:
        """初始化生成器。

        - `worker_id`：0-1023，多实例部署时每实例取不同值。
        - `epoch_ms`：时间戳起始点，id 自该时刻起增大。
        - `now_fn`：毫秒时钟函数（测试注入用），缺省取真实时钟。
        """
        if not 0 <= worker_id <= _MAX_WORKER_ID:
            raise ValueError(f"worker_id must be in [0, {_MAX_WORKER_ID}], got {worker_id}")
        self._worker_id = worker_id
        self._epoch_ms = epoch_ms
        self._now_fn = now_fn or _real_clock_ms
        self._lock = threading.Lock()
        self._last = -1
        self._sequence = 0

    def next_id(self) -> int:
        """生成下一个全局唯一、进程内严格递增的 63 位正整数 id。"""
        with self._lock:
            now = self._now_fn()
            if now < self._last:
                # 时钟回拨：沿用上次时间戳，靠序列保证不倒退、不重复。
                now = self._last
            if now == self._last:
                self._sequence = (self._sequence + 1) & _MAX_SEQUENCE
                if self._sequence == 0:
                    # 同毫秒 4096 个序列耗尽，等到下一毫秒再分配。
                    now = self._wait_next_ms(self._last)
            else:
                self._sequence = 0
            self._last = now
            return (
                ((now - self._epoch_ms) << _TIMESTAMP_SHIFT)
                | (self._worker_id << _WORKER_SHIFT)
                | self._sequence
            )

    def _wait_next_ms(self, last_ms: int) -> int:
        """轮询毫秒时钟，直到进入 last_ms 的下一毫秒。"""
        now = self._now_fn()
        while now <= last_ms:
            time.sleep(0.001)
            now = self._now_fn()
        return now


# 模块级单例：worker_id 取自配置；多实例部署时每实例设不同 SNOWFLAKE_WORKER_ID。
snowflake = Snowflake(worker_id=settings.snowflake_worker_id)


def new_id() -> int:
    """生成一个新的雪花 id。"""
    return snowflake.next_id()


def to_sid(value: object) -> str | None:
    """把雪花 id 序列化成**字符串**供 API 响应使用；None 原样透传。

    **为什么要字符串化。** 雪花 id 是 63 位正整数，普遍超过 JS 的
    `Number.MAX_SAFE_INTEGER`（2^53）。以 JSON number 发出去，前端 `JSON.parse`
    会悄悄改掉它 —— 实测库里就有 `outbox_event.id = 225548242094391297`，
    `int(float())` 变成 `...296`，**差 1**；前端拿它拼 `dead-letters/{id}/retry`
    就会打到错误的一行（更早的一次事故正是这个形状：审核回传 `source_id` 后
    报 `not found` → 409）。

    能在 double 里精确表示的 id，要求末尾连续 0 位 ≥ 5 —— 而那取决于同毫秒内
    有没有产生过第二个 id（seq≠0），**不是可以依赖的性质**。

    **为什么按字段做，而不是挂一个全局 JSON 编码器**：编码器只能按「值大不大」判断，
    于是同一个字段在小 id 时是 number、大 id 时是 string —— 那正是
    `docs/api-contract.md` §8.1 警告过的「同一数组里两种元素形状」。类型必须由
    **字段语义**决定，所以在调用点显式声明哪些字段是 id。

    接受 int 与 str（幂等）；空串与空白归一为 None。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["Snowflake", "new_id", "snowflake", "to_sid"]