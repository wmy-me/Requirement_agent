-- 能力关联与版本快照（方案批次 3）。
--
-- 三件事：
--   1. `feature_capability`：功能条目 ↔ 能力的关联（人工裁决状态默认 proposed）
--   2. `requirement_version` 新增能力/条件**快照**列
--   3. `requirement_version` 新增 `status`，并保证「一条主线只有一个 current」

-- ── 1. 功能条目 ↔ 能力 ────────────────────────────────────────────────
-- 关联挂在 feature 上（不是挂在 REQ 上）是方案的核心决定：
-- feature 已有一整套生命周期（status / origin_version_no / removed_version_no），
-- 需求更新后不再支持某能力 → 该 feature 被软删 → **关联自动失效，不需要新代码**。
CREATE TABLE IF NOT EXISTS feature_capability (
    feature_id BIGINT NOT NULL REFERENCES requirement_feature(id) ON DELETE CASCADE,
    capability_id BIGINT NOT NULL REFERENCES capability(id),
    -- 支撑这个能力的原话。与 feature.content 可能不同（模型会改写），
    -- 保留它是为了让审核页能把「功能原文」与「能力」并排给人工看
    raw_text TEXT NOT NULL DEFAULT '',
    confidence NUMERIC(5,4),
    -- proposed：AI 提议、待人工裁决。**只有 confirmed 才代表这个能力正式成立**
    review_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (review_status IN ('proposed', 'confirmed', 'dismissed')),
    decided_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (feature_id, capability_id)
);
CREATE INDEX IF NOT EXISTS idx_feature_capability_capability
    ON feature_capability (capability_id, review_status);

DROP TRIGGER IF EXISTS trg_feature_capability_updated_at ON feature_capability;
CREATE TRIGGER trg_feature_capability_updated_at
BEFORE UPDATE ON feature_capability
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();


-- ── 2 & 3. 版本：快照列 + 状态 ─────────────────────────────────────────
--
-- 快照为什么必须单独存、而不是每次从 feature 现算：
-- 能力/条件的**词表会变**（今天把「按部门筛选」改名，历史版本的派生结果会一起变），
-- 历史就被污染了。存快照是把「当时是怎么理解的」钉死。
--
-- 注意：这里存的是**快照**，不是真相。能力关联的真相在 feature_capability，
-- 快照只是那个时刻的留档，两者不一致时以 feature_capability 为准。
ALTER TABLE requirement_version
    ADD COLUMN IF NOT EXISTS capability_snapshot JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS constraint_snapshot JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'current'
        CHECK (status IN ('draft', 'pending_review', 'current', 'superseded')),
    ADD COLUMN IF NOT EXISTS superseded_by_version_no INTEGER;

-- ⚠️ 回填**必须先于**唯一索引：现有 5 个版本全都没有 status，
-- 加上默认值 'current' 之后，REQ-000015 的两条版本会同时是 current，
-- 唯一索引会直接建失败。先把「非最新」的历史版本标成 superseded。
UPDATE requirement_version v
SET status = 'superseded'
WHERE EXISTS (
    SELECT 1 FROM requirement_version newer
    WHERE newer.requirement_id = v.requirement_id
      AND newer.version_no > v.version_no
);

-- 一条需求主线只能有一个 current —— 用**部分唯一索引**在数据库层保证，
-- 而不是靠应用代码自觉（`master.current_version` 与它是冗余的两个表达，
-- 这里让数据库守住不变式）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_version_current
    ON requirement_version (requirement_id) WHERE status = 'current';

CREATE INDEX IF NOT EXISTS idx_version_status
    ON requirement_version (requirement_id, status);
