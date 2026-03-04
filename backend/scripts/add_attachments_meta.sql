-- 为 messages 表添加 attachments_meta 列（豆包式会话附件展示）
-- 执行：mysql -u user -p database_name < add_attachments_meta.sql 或在客户端中执行下面语句

ALTER TABLE messages ADD COLUMN attachments_meta TEXT NULL;
