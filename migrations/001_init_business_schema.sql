CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS requirement_source (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    source_event_id TEXT,
    requester_id TEXT,
    requester_name TEXT,
    original_text TEXT,
    extracted_text TEXT,
    original_payload JSONB NOT NULL DEFAULT '{}',
    metadata JSONB NOT NULL DEFAULT '{}',
    processing_status TEXT NOT NULL DEFAULT 'received'
        CHECK (processing_status IN (
            'received', 'extracting', 'analyzing', 'pending_review',
            'approved', 'rejected', 'committed', 'failed'
        )),
    error_message TEXT,
    submitted_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_requirement_source_channel_event
ON requirement_source(source_type, source_event_id)
WHERE source_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS requirement_attachment (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES requirement_source(id),
    file_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    object_uri TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    extraction_status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, file_hash)
);

CREATE SEQUENCE IF NOT EXISTS requirement_key_seq START 1;

CREATE TABLE IF NOT EXISTS requirement_master (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requirement_key TEXT NOT NULL UNIQUE,
    requirement_name TEXT NOT NULL,
    final_requirement TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 0 CHECK (current_version >= 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived', 'deleted')),
    lock_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS requirement_version (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requirement_id BIGINT NOT NULL REFERENCES requirement_master(id),
    parent_version_id BIGINT REFERENCES requirement_version(id),
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    version_title TEXT NOT NULL,
    change_type TEXT NOT NULL
        CHECK (change_type IN ('new', 'add', 'modify', 'delete')),
    requirement_snapshot TEXT NOT NULL,
    change_summary TEXT NOT NULL,
    diff_payload JSONB NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (requirement_id, version_no)
);

CREATE TABLE IF NOT EXISTS requirement_version_source (
    version_id BIGINT NOT NULL REFERENCES requirement_version(id),
    source_id BIGINT NOT NULL REFERENCES requirement_source(id),
    relation_type TEXT NOT NULL DEFAULT 'source'
        CHECK (relation_type IN ('source', 'related', 'conflict')),
    PRIMARY KEY (version_id, source_id, relation_type)
);

CREATE TABLE IF NOT EXISTS requirement_review (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES requirement_source(id),
    analysis_snapshot JSONB NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('approved', 'rejected', 'returned', 'timeout')),
    reviewer_id TEXT NOT NULL,
    reviewer_name TEXT,
    review_comment TEXT,
    edited_requirement TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_event (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trace_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    before_data JSONB,
    after_data JSONB,
    result_status TEXT NOT NULL,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbox_event (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed', 'completed')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_requirement_master_status_updated
ON requirement_master(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_requirement_version_requirement_id
ON requirement_version(requirement_id, version_no DESC);

CREATE INDEX IF NOT EXISTS idx_requirement_source_submitted_at
ON requirement_source(submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_outbox_event_status_created
ON outbox_event(status, created_at ASC);

CREATE TABLE IF NOT EXISTS requirement_embedding (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requirement_id BIGINT NOT NULL UNIQUE REFERENCES requirement_master(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    source_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_requirement_embedding_similarity
ON requirement_embedding USING hnsw (embedding vector_cosine_ops);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_requirement_master_updated_at ON requirement_master;
CREATE TRIGGER trg_requirement_master_updated_at
BEFORE UPDATE ON requirement_master
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_requirement_source_updated_at ON requirement_source;
CREATE TRIGGER trg_requirement_source_updated_at
BEFORE UPDATE ON requirement_source
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_outbox_event_updated_at ON outbox_event;
CREATE TRIGGER trg_outbox_event_updated_at
BEFORE UPDATE ON outbox_event
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
