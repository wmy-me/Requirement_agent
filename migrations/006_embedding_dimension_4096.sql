-- 006: embedding 维度 1536 → 4096（Doubao-embedding 返回 4096 维）
--
-- 背景：
-- 1) 旧实现无真实 embedding，写入的是 1536 维哈希伪向量/占位向量，语义无检索价值。
-- 2) 切换真实 embedding 后 Doubao-embedding 恒返回 4096 维（忽略 dimensions 参数）。
-- 3) pgvector 的 HNSW / IVFFlat 索引均上限 2000 维，4096 维无法建向量索引；
--    本项目数据量小（百级），改走无索引精确扫描（`ORDER BY embedding <=> ? LIMIT k`），
--    量级上完全够用且召回更准（无近似损失）。
--
-- 步骤：删旧索引 → 清空旧向量 → 改列类型。清空后对存量数据重新触发 embedding。

DROP INDEX IF EXISTS idx_requirement_embedding_similarity;
DROP INDEX IF EXISTS idx_document_chunk_hnsw;
DROP INDEX IF EXISTS idx_memory_hnsw;

DELETE FROM requirement_embedding;
UPDATE document_chunk SET embedding = NULL;
UPDATE memory_note SET embedding = NULL;

ALTER TABLE requirement_embedding ALTER COLUMN embedding TYPE vector(4096);
ALTER TABLE document_chunk ALTER COLUMN embedding TYPE vector(4096);
ALTER TABLE memory_note ALTER COLUMN embedding TYPE vector(4096);
