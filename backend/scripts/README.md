# 数据库迁移脚本（集中存放）

本目录存放所有**数据库表结构迁移**用的 SQL 脚本，按需执行（通常表已存在、需新增列时使用）。

| 脚本 | 说明 |
|------|------|
| `add_audit_request_id.sql` | audit_logs 表增加 request_id（链路追踪） |
| `add_kb_chunk_config.sql` | 知识库/分块相关配置列 |
| `add_kb_config_columns.sql` | 知识库级配置（模型、温度、rerank、混合检索等） |
| `add_message_rag_fields.sql` | messages 表 RAG 相关字段 |
| `add_sources_column.sql` | messages 表 sources 列（引用溯源） |
| `add_tools_used.sql` | messages 表 tools_used 列（MCP 工具列表） |
| `add_user_last_login_at.sql` | users 表 last_login_at |
| `add_users_password_hash.sql` | users 表密码哈希相关 |
| `add_web_search_columns.sql` | messages 表联网检索字段（web_retrieved_context、web_sources） |
| `add_benchmark_datasets.sql` | 召回率评测 benchmark_datasets 表（可选，应用 create_all 会自动建表） |
| `sync_users_table.sql` | 用户表结构同步 |

执行前请根据实际数据库类型（PostgreSQL / MySQL / SQLite）选用或注释脚本内对应段落，并按脚本头部说明执行。
