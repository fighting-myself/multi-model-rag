-- 实时联网检索字段（豆包式 RAG+联网）
-- 若 messages 表已存在且无以下列，请按数据库类型执行对应语句。
-- 执行方式：PostgreSQL: psql -U user -d database -f add_web_search_columns.sql
--          MySQL: mysql -u user -p database < add_web_search_columns.sql

-- PostgreSQL:
ALTER TABLE messages ADD COLUMN IF NOT EXISTS web_retrieved_context TEXT NULL;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS web_sources TEXT NULL;

-- MySQL（无 IF NOT EXISTS 时可先检查列是否存在）:
-- ALTER TABLE messages ADD COLUMN web_retrieved_context TEXT NULL;
-- ALTER TABLE messages ADD COLUMN web_sources TEXT NULL;
