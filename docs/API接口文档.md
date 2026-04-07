# API 接口文档（设计说明）

## 1. 总则

| 项 | 说明 |
|----|------|
| Base URL | `{origin}`，API 前缀为 **`/api/v1`** |
| 协议 | HTTP/HTTPS；开发环境常见为 `http://localhost:8000` |
| 鉴权 | 多数业务接口需 **`Authorization: Bearer <access_token>`**（登录接口 `POST /api/v1/auth/login` 返回 token） |
| 权威文档 | 部署后访问 **`/docs`（Swagger UI）**、**`/redoc`**，与代码内 Pydantic 模型一致 |
| 请求追踪 | 可传 **`X-Request-ID`**、**`X-Trace-Id`**；服务端会生成/回传 `X-Request-ID`，并回传 **`X-Trace-Id`**（与上下文日志对齐） |

根路径：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息与文档入口提示 |
| GET | `/health` | 健康检查 |
| GET | `/api/v1/ops/snapshot` | 进程内轻量指标（如 embedding 传输重试计数）；无鉴权，生产建议网关限制 |

---

## 2. 认证 `/api/v1/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register` | 注册 |
| POST | `/auth/login` | 登录（表单：`username`, `password`） |
| GET | `/auth/me` | 当前用户 |
| PUT | `/auth/me/password` | 修改密码 |

---

## 3. 仪表盘 `/api/v1/dashboard`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/dashboard/stats` | 仪表盘统计 |

---

## 4. 文件 `/api/v1/files`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/files/upload` | 上传（可选 `knowledge_base_id`） |
| POST | `/files/batch-upload` | 批量上传 |
| GET | `/files` | 文件列表 |
| GET | `/files/{file_id}` | 文件详情 |
| GET | `/files/{file_id}/download` | 下载 |
| DELETE | `/files/{file_id}` | 删除 |

---

## 5. 知识库 `/api/v1/knowledge-bases`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/knowledge-bases` | 创建 |
| GET | `/knowledge-bases` | 列表 |
| GET | `/knowledge-bases/{kb_id}` | 详情 |
| PUT | `/knowledge-bases/{kb_id}` | 更新 |
| DELETE | `/knowledge-bases/{kb_id}` | 删除 |
| GET | `/knowledge-bases/{kb_id}/files` | 库内文件 |
| GET | `/knowledge-bases/{kb_id}/files/{file_id}/chunks` | 分块列表 |
| POST | `/knowledge-bases/{kb_id}/files` | 添加文件到库 |
| POST | `/knowledge-bases/{kb_id}/files/async` | 异步添加 |
| POST | `/knowledge-bases/{kb_id}/files/stream` | 流式处理场景 |
| DELETE | `/knowledge-bases/{kb_id}/files/{file_id}` | 从库移除文件 |
| POST | `/knowledge-bases/{kb_id}/files/{file_id}/reindex` | 重索引 |
| POST | `/knowledge-bases/{kb_id}/files/{file_id}/reindex-async` | 异步重索引 |
| POST | `/knowledge-bases/{kb_id}/reindex-all-async` | 全库异步重索引 |
| GET | `/knowledge-bases/{kb_id}/export` | 导出 |

---

## 6. 异步任务 `/api/v1/tasks`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/tasks/{task_id}` | 任务状态 |

---

## 7. 检索 `/api/v1/search`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/search/images` | 图片检索 |
| POST | `/search/unified` | 统一检索 |
| POST | `/search/by-image` | 以图搜图等 |
| POST | `/search/by-image/upload` | 上传图片检索 |

---

## 8. 问答 `/api/v1/chat`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/chat/settings/chat-attachment` | 附件相关设置 |
| POST | `/chat/completions` | 非流式补全 |
| POST | `/chat/completions/stream` | **流式**补全（SSE/流式响应） |
| POST | `/chat/attachments/upload` | 对话附件上传 |
| GET | `/chat/conversations` | 会话列表 |
| GET | `/chat/conversations/{conv_id}` | 会话详情 |
| GET | `/chat/conversations/{conv_id}/messages` | 消息列表 |
| DELETE | `/chat/conversations/{conv_id}` | 删除会话 |

---

## 9. 计费 `/api/v1/billing`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/billing/usage` | 用量 |
| GET | `/billing/usage-limits` | 用量上限 |
| GET | `/billing/plans` | 套餐列表 |
| GET | `/billing/plans/{plan_id}` | 套餐详情 |
| POST | `/billing/subscribe` | 订阅 |
| GET | `/billing/invoices` | 账单 |

---

## 10. 审计 `/api/v1/audit-logs`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/audit-logs` | 审计日志列表 |

---

## 11. MCP `/api/v1/mcp-servers`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/mcp-servers` | Server 列表 |
| POST | `/mcp-servers` | 注册 Server |
| GET | `/mcp-servers/mcp-available` | 可用性探测类 |
| GET | `/mcp-servers/{server_id}` | 详情 |
| PUT | `/mcp-servers/{server_id}` | 更新 |
| DELETE | `/mcp-servers/{server_id}` | 删除 |
| GET | `/mcp-servers/{server_id}/tools` | 工具列表 |
| POST | `/mcp-servers/{server_id}/tools/call` | 调用工具 |

---

## 12. 助手与审批

| 前缀 | 方法 | 路径 | 说明 |
|------|------|------|------|
| `/api/v1/steward` | POST | `/run` | 浏览器助手执行 |
| `/api/v1/computer-steward` | POST | `/run` | 电脑管家执行 |
| `/api/v1/bash` | POST | `/approve` | Bash 审批 |
| `/api/v1/bash` | GET | `/pending` | 待审批列表 |

---

## 13. 评测 `/api/v1/evaluation`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/evaluation/rag-metrics` | RAG 指标聚合/配置类 |
| GET | `/evaluation/rag-metrics/defaults` | 默认评测集 |
| POST | `/evaluation/rag-metrics/run-accuracy` | 准确率 |
| POST | `/evaluation/rag-metrics/run-recall` | 召回 |
| POST | `/evaluation/rag-metrics/run-precision` | 精准度 |
| POST | `/evaluation/rag-metrics/run-latency` | 延迟 |
| POST | `/evaluation/rag-metrics/run-hallucination` | 幻觉率 |
| POST | `/evaluation/rag-metrics/run-qps` | QPS |
| POST | `/evaluation/recall/run` | 召回评测运行 |
| GET | `/evaluation/benchmarks` | Benchmark 列表 |
| POST | `/evaluation/benchmarks` | 创建 Benchmark |
| GET | `/evaluation/benchmarks/{dataset_id}` | 详情 |
| PUT | `/evaluation/benchmarks/{dataset_id}` | 更新 |
| DELETE | `/evaluation/benchmarks/{dataset_id}` | 删除 |

---

## 14. 错误与版本

- HTTP 状态码遵循 REST 惯例：401 未授权、403 禁止、404 不存在、409 冲突、422 校验错误、429 限流、429/500 等。
- **版本**：当前应用版本见 `GET /` 返回或 `backend/app/main.py` 中 `FastAPI(version=...)`。

## 15. 前端类型映射

前端 `frontend/src/types/api.ts` 与后端 `app/schemas/` 应对齐；接口变更时请同时更新类型定义与本文档。
