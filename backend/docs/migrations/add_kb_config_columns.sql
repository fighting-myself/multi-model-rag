-- 知识库级配置字段迁移（模型、温度、分块、rerank、混合检索）
-- 若表已存在且无以下列，请按数据库类型执行对应语句。

-- PostgreSQL:
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(80);
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS llm_model VARCHAR(80);
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS temperature FLOAT;
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS enable_rerank BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS enable_hybrid BOOLEAN NOT NULL DEFAULT TRUE;

-- MySQL（无 IF NOT EXISTS 时可先检查列是否存在）:
-- ALTER TABLE knowledge_bases ADD COLUMN embedding_model VARCHAR(80) NULL;
-- ALTER TABLE knowledge_bases ADD COLUMN llm_model VARCHAR(80) NULL;
-- ALTER TABLE knowledge_bases ADD COLUMN temperature FLOAT NULL;
-- ALTER TABLE knowledge_bases ADD COLUMN enable_rerank TINYINT(1) NOT NULL DEFAULT 1;
-- ALTER TABLE knowledge_bases ADD COLUMN enable_hybrid TINYINT(1) NOT NULL DEFAULT 1;
