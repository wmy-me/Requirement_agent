CREATE TABLE IF NOT EXISTS document_asset (
    id BIGSERIAL PRIMARY KEY,
    file_name TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    storage_uri TEXT NOT NULL,
    checksum TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'web',
    source_id BIGINT REFERENCES requirement_source(id) ON DELETE SET NULL,
    original_text TEXT,
    extracted_text TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_document_asset_created_at
    ON document_asset (created_at DESC);

CREATE TABLE IF NOT EXISTS document_chunk (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES document_asset(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_document_chunk_document_id
    ON document_chunk (document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_document_chunk_hnsw
    ON document_chunk USING hnsw (embedding vector_cosine_ops);

CREATE OR REPLACE FUNCTION update_document_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_asset_updated_at ON document_asset;
CREATE TRIGGER trg_document_asset_updated_at
BEFORE UPDATE ON document_asset
FOR EACH ROW EXECUTE FUNCTION update_document_updated_at_column();

DROP TRIGGER IF EXISTS trg_document_chunk_updated_at ON document_chunk;
CREATE TRIGGER trg_document_chunk_updated_at
BEFORE UPDATE ON document_chunk
FOR EACH ROW EXECUTE FUNCTION update_document_updated_at_column();
