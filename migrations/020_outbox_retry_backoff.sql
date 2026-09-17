-- outbox 失败重试的退避（B5 阶段：统一「重试次数与退避策略」）。
--
-- ## 现状的问题
--
-- `mark_failed` 把事件置回 `pending` 并清空 `locked_at`。于是**下一个轮询周期**
-- （默认 `OUTBOX_POLL_INTERVAL_SECONDS=5`）就会立刻重试它。而 `max_retries` 是 3 ——
-- **一个事件从第一次失败到进死信，总共只有约 15 秒**。
--
-- 对「远端 LLM 超时」「数据库瞬时拒绝连接」这类故障，15 秒几乎没有恢复窗口：
-- 三次重试全部落在同一次抖动里，然后事件就进死信了 —— 而它本来再等半分钟就能成功。
--
-- ## 做法
--
-- 加一列 `next_attempt_at`：失败时按 `retry_count` 指数退避地设一个「最早可重试时间」，
-- `claim_pending` 跳过还没到点的事件。
--
-- ⚠️ **退避只作用于失败重试，不影响正常轮询。** 新事件的 `next_attempt_at` 为 NULL，
-- 立即可领（`NULL OR <= NOW()` 的判据）。空轮询的退避仍然由消费循环自己控制
-- （`_delay_after`），两者是不同层面的东西，别混。
--
-- ⚠️ **僵尸回收不受影响**：`status='processing'` 且 `locked_at` 超时的事件
-- 仍然照旧可被重领 —— 那是「消费者崩了」，不是「失败了要退避」。

ALTER TABLE outbox_event
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;

-- 领取候选的判据里会用到它；部分索引只覆盖 pending（processing 的僵尸回收不看这列）。
CREATE INDEX IF NOT EXISTS idx_outbox_event_next_attempt
    ON outbox_event (next_attempt_at)
    WHERE status = 'pending';

COMMENT ON COLUMN outbox_event.next_attempt_at IS
    '最早可重试时间（失败退避用）；NULL = 立即可领。不影响 processing 的僵尸回收。';
