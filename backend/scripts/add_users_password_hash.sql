-- 为 users 表添加 password_hash 列（若表已存在但缺该列时执行）
-- MySQL 执行: mysql -h <host> -u <user> -p <database> < scripts/add_users_password_hash.sql

-- 若列已存在会报错，可先检查: SHOW COLUMNS FROM users LIKE 'password_hash';
ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NOT NULL DEFAULT '';
