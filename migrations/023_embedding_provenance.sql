-- 向量的来源标记（B3.1，追加实施文档 §4.6）。
--
-- ## 缺口是什么
--
-- 三张向量表（`requirement_embedding` / `document_chunk` / `memory_note`）**只存向量，
-- 不存「这条向量是哪个模型算的」**。于是换 embedding 模型之后：
--
--   · 库里是**旧模型**的向量，查询用**新模型**编码；
--   · pgvector 照常算余弦，**返回一个看起来完全正常的分数**；
--   · 没有任何地方会察觉两个向量根本不在同一个空间里。
--
-- 这是本项目一直在猎杀的那类失败：**不报错、不告警、结果看起来正常**。
-- §4.6 的原话是「模型变化后**不能**直接把新旧向量混在同一索引中」——
-- 在此之前，混不混完全取决于运维有没有记得手动清库重算。
--
-- ## 两列而不是一列
--
-- `embedding_model` 与 `embedding_dimension` 分开存，不拼成一个 version 字符串：
-- 维度不匹配是**硬故障**（pgvector 会直接拒绝写入），模型不匹配是**软故障**
-- （写得进去，但算出来的相似度没有意义）。两者要能分别查询与分别告警。
--
-- ## 为什么可空
--
-- 存量行（本次迁移之前写入的）无从得知是哪个模型算的 —— **不能编**。
-- `NULL` 的准确含义是「不知道」，检出逻辑会把它当作「需要重新索引」处理，
-- 而不是当作「与当前模型一致」。用默认值填一个当前模型名是**撒谎**：
-- 那些向量可能来自任何模型，下次换模型时它们会被误认为匹配。

ALTER TABLE requirement_embedding ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE requirement_embedding ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER;

ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER;

ALTER TABLE memory_note ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE memory_note ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER;

-- 检出「库里有哪些模型的向量」是这个功能最常用的查询（按模型分组计数）。
-- 三张表各建一条部分索引：只覆盖有向量的行，NULL 行（存量）不进索引。
CREATE INDEX IF NOT EXISTS idx_requirement_embedding_model
    ON requirement_embedding (embedding_model)
    WHERE embedding_model IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_document_chunk_model
    ON document_chunk (embedding_model)
    WHERE embedding_model IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_note_model
    ON memory_note (embedding_model)
    WHERE embedding_model IS NOT NULL;

COMMENT ON COLUMN requirement_embedding.embedding_model IS
    '生成这条向量的 EMBEDDING_MODEL。**NULL = 不知道**（本次迁移之前的存量行），'
    '检出逻辑一律按「需要重新索引」处理 —— 不要用当前模型名去回填。';
COMMENT ON COLUMN requirement_embedding.embedding_dimension IS
    '生成这条向量时的维度。与 embedding_model 分开存：维度不匹配是硬故障'
    '（pgvector 直接拒绝），模型不匹配是软故障（写得进但相似度无意义）。';
