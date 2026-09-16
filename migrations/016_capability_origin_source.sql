-- 能力提案的来源溯源（方案批次 2 的收尾修复）。
--
-- 背景：批次 2 起，提交需求时会把抽取出的能力候选写成 `pending_confirmation` 提案。
-- 于是**任何走真实提交路径的测试都会往 capability 表写行**，而既有的测试清理只删
-- `requirement_source`，管不到这里 —— 实测跑一次测试套件就在词表里留下一行
-- 「(导出, 报表)」垃圾。项目此前为同类问题专门修过一次（`78829fd`：不再往真实库堆
-- 「接口冒烟-」垃圾），不该在这里重犯。
--
-- 修法不是让每个测试各自记得清理，而是**让提案记住自己是谁提的**：
-- 加上 `origin_source_id` 并 `ON DELETE CASCADE`，来源一删，提案跟着走。
-- 既有的 `_purge_sources` 只删来源，清理就自动完整了。
--
-- 顺带有真实产品价值：审核页可以显示「这条能力是哪个需求提出来的」，
-- 否则人工只能看到一对光秃秃的 (action, object)，无从判断该不该确认。

ALTER TABLE capability
    ADD COLUMN IF NOT EXISTS origin_source_id BIGINT
        REFERENCES requirement_source(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_capability_origin_source
    ON capability (origin_source_id);

-- 条件目前**不自动入表**（批次 2 实测噪声率约 90%，见 capability_match_service 的说明），
-- 所以这里不给 constraint_vocab 加同样的列 —— 等真有自动提议路径时再加。
