-- 为 messages 表增加 tools_used 列，用于存储本条回复调用的 MCP 工具名列表（JSON 数组字符串）
-- 执行方式：SQLite: sqlite3 your.db < add_tools_used.sql
--          MySQL/PostgreSQL: 使用下方对应语句执行。
-- 若已存在该列会报错，可忽略。MySQL/PostgreSQL 可先检查列是否存在再 ADD COLUMN。

-- SQLite / 通用:
ALTER TABLE messages ADD COLUMN tools_used TEXT;
