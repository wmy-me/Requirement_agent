-- requirement_attachment：001 曾建但全仓 0 处代码引用、0 行数据
-- （原始文件现在走 MinIO + original_payload / document_asset），清理冗余表。
DROP TABLE IF EXISTS requirement_attachment;
