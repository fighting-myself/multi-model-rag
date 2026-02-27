-- 为 messages 表增加 tools_used 列，用于存储本条回复调用的 MCP 工具名列表（JSON 数组字符串）
-- 执行一次即可：sqlite3 使用下方 SQLite 语句；MySQL/PostgreSQL 使用通用语句。

-- SQLite:
ALTER TABLE messages ADD COLUMN tools_used TEXT;

-- 若已存在该列会报错，可忽略。MySQL/PostgreSQL 可先检查列是否存在再 ADD COLUMN。
