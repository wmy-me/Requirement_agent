-- 需求管理 Agent 最小可用表结构
-- 适用于 PostgreSQL + pgvector

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- 1) 主需求表：一个真实需求的统一状态和最终版本
CREATE TABLE IF NOT EXISTS requirement_master (
    id SERIAL PRIMARY KEY,
    requirement_key TEXT NOT NULL UNIQUE,
    requirement_name TEXT NOT NULL,
    final_requirement TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'archived', 'rejected')),
    source_channel TEXT,
    requester TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) 版本表：需求随时间的变更记录
CREATE TABLE IF NOT EXISTS requirement_version (
    id SERIAL PRIMARY KEY,
    requirement_id INTEGER NOT NULL REFERENCES requirement_master(id) ON DELETE CASCADE,
    version_title TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('create', 'update', 'archive', 'reject')),
    version_requirement TEXT NOT NULL,
    change_summary TEXT,
    source_record_ids TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3) 原始需求记录：用户提交、附件/描述等原始录入信息
CREATE TABLE IF NOT EXISTS requirement_record (
    id SERIAL PRIMARY KEY,
    source_channel TEXT,
    requester TEXT,
    requirement_summary TEXT,
    original_requirement TEXT,
    attachment_summary TEXT,
    original_files JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'approved', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE requirement_record ADD COLUMN IF NOT EXISTS requirement_summary TEXT;
ALTER TABLE requirement_record ADD COLUMN IF NOT EXISTS original_files JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE requirement_record ADD COLUMN IF NOT EXISTS raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE requirement_record ADD COLUMN IF NOT EXISTS input_time TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 4) 审计日志：每一步的执行状态必须可追踪
CREATE TABLE IF NOT EXISTS task_audit_log (
    id SERIAL PRIMARY KEY,
    task_id TEXT,
    node_name TEXT,
    input_summary TEXT,
    output_summary TEXT,
    result_status TEXT NOT NULL CHECK (result_status IN ('success', 'failed', 'pending')),
    error_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5) 需求知识表：用于 pgvector 相似需求检索
CREATE TABLE IF NOT EXISTS requirement_knowledge (
    id SERIAL PRIMARY KEY,
    requirement_id INTEGER REFERENCES requirement_master(id) ON DELETE SET NULL,
    requirement_key TEXT,
    requirement_name TEXT,
    summary TEXT,
    final_requirement TEXT,
    functional_modules JSONB,
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引：按时间和状态查询
CREATE INDEX IF NOT EXISTS idx_requirement_master_status_updated
    ON requirement_master(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_requirement_version_requirement_id
    ON requirement_version(requirement_id, version_no DESC);

CREATE INDEX IF NOT EXISTS idx_requirement_record_created_at
    ON requirement_record(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_audit_log_task_id
    ON task_audit_log(task_id);

-- HNSW 索引：适合相似度搜索
-- 如果你的 pgvector 版本支持 HNSW，建议开启
CREATE INDEX IF NOT EXISTS idx_requirement_knowledge_embedding_hnsw
    ON requirement_knowledge USING hnsw (embedding vector_cosine_ops);

-- 触发器：自动更新 updated_at
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

-- 采样数据：方便后续测试
INSERT INTO requirement_master (requirement_key, requirement_name, final_requirement, current_version, status, source_channel, requester)
VALUES
    ('REQ-001', '用户登录', '支持邮箱和手机号登录，并支持验证码校验。', 1, 'active', 'web', 'alice'),
    ('REQ-002', '需求审批流', '需求提交后必须经过审批，审批通过后才能进入开发。', 1, 'active', 'web', 'bob')
ON CONFLICT (requirement_key) DO NOTHING;

INSERT INTO requirement_version (requirement_id, version_title, version_no, change_type, version_requirement, change_summary, source_record_ids)
SELECT id, 'v1', 1, 'create', final_requirement, '初始化版本', '1'
FROM requirement_master
WHERE requirement_key = 'REQ-001'
ON CONFLICT DO NOTHING;

INSERT INTO requirement_record (source_channel, requester, requirement_summary, original_requirement, attachment_summary, review_status)
VALUES
    ('web', 'alice', '登录方式扩展', '用户登录，支持邮箱和手机号登录', '无附件', 'approved'),
    ('web', 'bob', '审批流治理', '需求审批流，审批后才进入开发', '无附件', 'approved')
ON CONFLICT DO NOTHING;
