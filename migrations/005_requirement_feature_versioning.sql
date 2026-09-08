CREATE TABLE IF NOT EXISTS requirement_feature (
    id BIGSERIAL PRIMARY KEY,
    requirement_id BIGINT NOT NULL REFERENCES requirement_master(id) ON DELETE CASCADE,
    feature_key TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deleted')),
    ordinal INT NOT NULL DEFAULT 0,
    origin_source_id BIGINT REFERENCES requirement_source(id) ON DELETE SET NULL,
    origin_requirement_key TEXT,
    origin_version_no INT NOT NULL DEFAULT 1,
    removed_version_no INT,
    provenance JSONB NOT NULL DEFAULT '[]',
    content_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_req_key
    ON requirement_feature (requirement_id, feature_key);

CREATE INDEX IF NOT EXISTS idx_feature_req_active
    ON requirement_feature (requirement_id, status, ordinal);

CREATE INDEX IF NOT EXISTS idx_feature_origin_source
    ON requirement_feature (origin_source_id);

ALTER TABLE requirement_version
    ADD COLUMN IF NOT EXISTS feature_changes JSONB,
    ADD COLUMN IF NOT EXISTS parent_version_no INT;

DROP TRIGGER IF EXISTS trg_requirement_feature_updated_at ON requirement_feature;
CREATE TRIGGER trg_requirement_feature_updated_at
BEFORE UPDATE ON requirement_feature
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
