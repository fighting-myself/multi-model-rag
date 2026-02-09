-- 为 messages 表添加 RAG 相关字段
-- 执行方式：mysql -u用户名 -p数据库名 < add_message_rag_fields.sql

ALTER TABLE messages 
ADD COLUMN confidence TEXT NULL COMMENT '检索置信度（存储为字符串，前端解析为 float）',
ADD COLUMN retrieved_context TEXT NULL COMMENT '检索到的上下文内容',
ADD COLUMN max_confidence_context TEXT NULL COMMENT '最高置信度对应的单个上下文';
