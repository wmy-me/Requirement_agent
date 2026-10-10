-- Ownership fence for resumable executors.  Nullable keeps old runs readable.
ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS execution_token UUID;
ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_agent_run_lease_expiry
    ON agent_run (lease_expires_at) WHERE status = 'running';
