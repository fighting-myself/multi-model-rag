-- 为 messages 表添加 sources 列（引用溯源 JSON）
-- 执行方式示例：
--   SQLite:   sqlite3 your.db < add_sources_column.sql
--   MySQL:    mysql -u user -p database < add_sources_column.sql
--   PostgreSQL: psql -U user -d database -f add_sources_column.sql

ALTER TABLE messages ADD COLUMN sources TEXT NULL;
