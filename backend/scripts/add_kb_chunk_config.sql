-- 知识库分块策略可配置：为 knowledge_bases 表添加 chunk_size / chunk_overlap / chunk_max_expand_ratio
-- 执行方式：sqlite3 your.db < add_kb_chunk_config.sql 或 mysql/psql 对应方式

ALTER TABLE knowledge_bases ADD COLUMN chunk_size INTEGER NULL;
ALTER TABLE knowledge_bases ADD COLUMN chunk_overlap INTEGER NULL;
ALTER TABLE knowledge_bases ADD COLUMN chunk_max_expand_ratio VARCHAR(20) NULL;
