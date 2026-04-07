-- 为 audit_logs 表增加 trace_id（与 X-Trace-Id / 请求上下文对齐，便于与结构化日志关联）
-- MySQL / PostgreSQL 通用片段；若列已存在可忽略对应报错。

ALTER TABLE audit_logs ADD COLUMN trace_id VARCHAR(64) NULL;
CREATE INDEX ix_audit_logs_trace_id ON audit_logs(trace_id);
