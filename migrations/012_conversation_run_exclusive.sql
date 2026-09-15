-- 012: 单对话并发隔离 —— 同一对话最多一个活跃运行
--
-- 需求（docs/方案_对话状态机与Git式版本管理.md §2）：一个对话一次只答一个问题，
-- 但开新对话可以并行。约束的键是 conversation_id，**不是整个用户** ——
-- 部分唯一索引天然做到「同对话互斥、跨对话并行」，且跨进程/多 worker 也成立
-- （优于进程内的 asyncio.Lock）。
--
-- 既有 uq_run_once (conversation_id, client_message_id) 是**幂等去重**：
-- 两个不同的 client_message_id 打同一对话，两条都会成功、并行跑 —— 那正是要堵的洞。

CREATE UNIQUE INDEX IF NOT EXISTS uq_run_active_per_conversation
    ON agent_run (conversation_id)
    WHERE status IN ('running', 'paused');
