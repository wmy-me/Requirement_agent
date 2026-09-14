-- 008: 删除从未被使用的 requirement_attachment 表
--
-- 背景：001 里建了这张表用于「与来源需求绑定的附件元数据」，但**代码从未读写过它**
-- （全仓只有建表语句命中，没有任何 Python 引用；配套的领域类 RequirementAttachment
-- 也因无人引用而被删除）。附件实际上由 document_asset + document_chunk 承担，
-- 两个设计功能重叠，留着只会让人以为存在两套附件模型。
--
-- 本迁移前该表为 0 行，删除无数据损失。

DROP TABLE IF EXISTS requirement_attachment;
