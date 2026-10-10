-- Execution ownership is represented by a short-lived token in meta so this
-- migration stays backwards compatible with existing agent_run rows.
CREATE INDEX IF NOT EXISTS idx_agent_run_resumable
    ON agent_run (run_id, status)
    WHERE status IN ('queued', 'paused', 'retrying');
