-- 对话 Run 的暂停/取消状态机。
--
-- paused 是可恢复的协作式停点；cancelled 是用户明确终止，永远不可恢复。
-- 024 只放宽已有 CHECK。012 的同会话活跃索引已经包含 paused，因此暂停中的
-- run 仍占用该会话，避免用户在旧上下文未处理完时并发发起另一条运行。

ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS agent_run_status_check;
ALTER TABLE agent_run ADD CONSTRAINT agent_run_status_check
    CHECK (status IN ('queued', 'running', 'paused', 'waiting_review',
                      'completed', 'failed', 'cancelled', 'retrying'));
