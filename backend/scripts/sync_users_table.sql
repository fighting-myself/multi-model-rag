-- 将 users 表补齐为与代码中 User 模型一致（缺哪列补哪列）
-- 执行（加 -f 可在某列已存在时继续执行后续语句）:
--   mysql -f -h <host> -P <port> -u <user> -p <database> < scripts/sync_users_table.sql

-- password_hash
ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NOT NULL DEFAULT '';

-- phone
ALTER TABLE users ADD COLUMN phone VARCHAR(20) NULL;

-- avatar_url
ALTER TABLE users ADD COLUMN avatar_url VARCHAR(255) NULL;

-- role
ALTER TABLE users ADD COLUMN `role` VARCHAR(20) NOT NULL DEFAULT 'user';

-- plan_id（依赖 plans 表需已存在）
ALTER TABLE users ADD COLUMN plan_id INT NULL;

-- credits
ALTER TABLE users ADD COLUMN credits DECIMAL(10,2) NOT NULL DEFAULT 0;

-- is_active
ALTER TABLE users ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1;

-- created_at
ALTER TABLE users ADD COLUMN created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP;

-- updated_at
ALTER TABLE users ADD COLUMN updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
