-- 014: 需求模块 —— requirement_feature 增加模块标签
--
-- 方案 §3.1（docs/方案_对话状态机与Git式版本管理.md）：
-- 「模块」在业务上就是 feature 的分组（一条 REQ 的正文 = active features 拼接）。
-- 抽取阶段模型天然会输出模块结构（实测形如 {"module": "登录", "items": [...]}），
-- 但此前被 _flatten_item 糊成了一条字符串，模块信息丢失。
--
-- module_key 是跨 REQ 可复用的稳定标识（同名即同模块）；module_name 是展示名。
-- 都为 NULL 表示该功能不归属任何模块（退化行为与 014 之前一致）。

ALTER TABLE requirement_feature
    ADD COLUMN IF NOT EXISTS module_key TEXT,
    ADD COLUMN IF NOT EXISTS module_name TEXT;

CREATE INDEX IF NOT EXISTS idx_feature_module
    ON requirement_feature (requirement_id, module_key);
