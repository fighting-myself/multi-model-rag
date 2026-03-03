-- 为用户表增加 last_login_at 列
-- 执行方式：MySQL: mysql -u user -p database < add_user_last_login_at.sql
--          PostgreSQL: psql -U user -d database -f add_user_last_login_at.sql

ALTER TABLE users ADD COLUMN last_login_at DATETIME NULL;
