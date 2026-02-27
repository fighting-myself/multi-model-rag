-- 为 audit_logs 表增加 request_id 列（链路追踪）
-- MySQL:    mysql -u user -p database < migrations_add_audit_request_id.sql
-- PostgreSQL: psql -U user -d database -f migrations_add_audit_request_id.sql
-- SQLite:   第二行改为 CREATE INDEX IF NOT EXISTS ix_audit_logs_request_id ON audit_logs(request_id);
-- 若列或索引已存在，可忽略对应报错。

ALTER TABLE audit_logs ADD COLUMN request_id VARCHAR(64) NULL;
CREATE INDEX ix_audit_logs_request_id ON audit_logs(request_id);
