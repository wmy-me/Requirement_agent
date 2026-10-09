-- 动态聊天在等待用户补充时不是审核待办，也不应占用同会话 running/paused 锁。
ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS agent_run_status_check;
ALTER TABLE agent_run ADD CONSTRAINT agent_run_status_check
    CHECK (status IN ('queued', 'running', 'paused', 'waiting_clarification', 'waiting_review',
                      'completed', 'failed', 'cancelled', 'retrying'));
