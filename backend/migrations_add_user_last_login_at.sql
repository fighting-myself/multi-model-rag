-- 为用户表增加 last_login_at 列
ALTER TABLE users ADD COLUMN last_login_at DATETIME NULL;
