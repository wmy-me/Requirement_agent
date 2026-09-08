CREATE TABLE IF NOT EXISTS agent_conversation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id TEXT NOT NULL DEFAULT 'api-user',
    title TEXT NOT NULL DEFAULT '新对话',
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_actor_upd
    ON agent_conversation (actor_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_message (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES agent_conversation(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    artifacts JSONB,
    client_message_id TEXT,
    run_id UUID,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_msg_conv
    ON agent_message (conversation_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_msg_conv_client
    ON agent_message (conversation_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_run (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES agent_conversation(id) ON DELETE CASCADE,
    client_message_id TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    error TEXT,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_run_once
    ON agent_run (conversation_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_note (
    id BIGSERIAL PRIMARY KEY,
    actor_id TEXT NOT NULL DEFAULT 'api-user',
    kind TEXT NOT NULL DEFAULT 'fact'
        CHECK (kind IN ('preference', 'decision', 'fact', 'idea', 'rejected', 'followup', 'requirement_ref')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'deleted')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    content TEXT NOT NULL,
    source_conversation_id UUID REFERENCES agent_conversation(id) ON DELETE SET NULL,
    source_message_id BIGINT,
    ref_requirement_key TEXT,
    superseded_by BIGINT REFERENCES memory_note(id),
    embedding vector(1536),
    importance SMALLINT NOT NULL DEFAULT 1,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_hnsw
    ON memory_note USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_memory_actor_active
    ON memory_note (actor_id, status, importance DESC, created_at DESC);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agent_conversation_updated_at ON agent_conversation;
CREATE TRIGGER trg_agent_conversation_updated_at
BEFORE UPDATE ON agent_conversation
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_agent_run_updated_at ON agent_run;
CREATE TRIGGER trg_agent_run_updated_at
BEFORE UPDATE ON agent_run
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_memory_note_updated_at ON memory_note;
CREATE TRIGGER trg_memory_note_updated_at
BEFORE UPDATE ON memory_note
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

UPDATE memory_note
SET active = (status = 'active')
WHERE active IS DISTINCT FROM (status = 'active');
