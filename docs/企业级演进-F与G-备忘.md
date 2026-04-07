# 企业级演进：F 阶段（Agent / 记忆）与 G 阶段（集群 / 治理）

> 与 [`企业级改造计划与进度.md`](./企业级改造计划与进度.md) 中 **F-1～F-3、G-1～G-3** 对应；**以设计与边界说明为主**，实现可分期落地。

---

## F-1：RAG 作为 Tool 与「纯 Agent 对话」边界

**现状**：智能问答以 `POST /api/v1/chat/completions`（及流式）为主入口，内部按配置走自研 RAG、`HybridRetrievalPipeline`、Advanced RAG、LangChain 等；超能模式再叠加 MCP / Skills。

**建议边界**：

| 能力 | 角色 | 说明 |
|------|------|------|
| 检索增强问答 | 核心产品路径 | 单次请求内：选 KB → 检索 → 引用 → 生成；契约见 `schemas/chat_contract.py`、`ChatFacade`。 |
| RAG | 编排中的「工具」 | 在 Agent 侧表现为：给定 query + `kb_ids` → 返回 `context + citations`，不直接替代「任务规划」层。 |
| 纯 Agent 对话 | 少工具、多轮推理 | 宜单独语义：例如「无 KB 仅工具/联网」或「仅 MCP」；避免与「必选 KB 的合规问答」混在同一策略分支而不打标。 |

**API 分层**：对外可用路由前缀或标签区分「问答」「评测」「Agent/管家」；同一网关下保持 **鉴权、限流、trace_id** 一致，由路由层选择是否注入 RAG pipeline。

---

## F-2：跨会话记忆与 `memory_service` 对齐（设计）

**现状**：`memory_service` 使用本地 SQLite（`MEMORY_DB_PATH` / `data/memory.db`），按 `user_id` 写入；`MEMORY_ENABLED` 控制是否参与检索。

**建议策略（先设计、后实现）**：

1. **过期**：按 `memory_type` 分级 TTL（如 `execution_record` 短、`user_preference` 长）；定期清理或查询时过滤 `created_at`。
2. **容量**：每用户条数上限 + 单条 `content` 长度上限；超限则 FIFO 或按类型淘汰。
3. **权限**：记忆读写必须与 **认证用户 id** 一致；禁止按客户端传入的任意标识跨用户访问；与多租户演进时并入「租户/项目」作用域（参见 [`资源模型与多租户缺口.md`](./资源模型与多租户缺口.md)）。

---

## F-3：编排器原型（可选）

**方向**：多步任务状态机（待执行 / 执行中 / 失败可重试 / 成功）、单步超时与可观测 `trace_id` 贯穿；失败重试策略与 MCP / Skills 调用共享同一套退避与日志。

**与代码关系**：超能模式、电脑管家等已有「链式补上下文」；编排器是进一步 **显式化状态与重试**，避免隐式递归补全难以排障。

---

## G-1：部署与无状态 API（与 `docker-compose` 对照）

**参考**：根目录 `docker-compose.yml` 中 `postgres`、`redis`、`qdrant`、`minio`、`backend` 服务及 **healthcheck**。

**原则**：

- **API 多副本**：进程无本地会话状态；会话与缓存依赖 **Redis / DB**；滚动更新时依赖 readiness（建议 `GET /health` 或 `/api/v1/ops/snapshot` 子集）就绪后再摘流量。
- **向量与对象存储**：Qdrant / MinIO 为有状态依赖，与 API 副本独立扩缩。

更完整的拓扑与叙述可与 [`部署架构图.md`](./部署架构图.md)、[`05-部署方案.md`](./05-部署方案.md) 交叉引用。

---

## G-2：告警与 SLA（建议规则）

与 [`降级路径说明.md`](./降级路径说明.md)、`GET /api/v1/ops/snapshot` 配合，生产可对接 Prometheus / 云监控：

| 信号 | 说明 |
|------|------|
| HTTP 5xx 率 | 网关或应用错误率超阈值 |
| P95 延迟 | 问答或检索接口 |
| 检索空结果占比 | 与业务预期偏差过大时告警（需基线） |
| 向量库 / Redis 不可用 | 健康检查连续失败 |
| Embedding / LLM 重试耗尽 | 与 D-1 超时重试配置联动 |

SLA 以「可用性 + 核心路径延迟」表述即可，细则按业务合同调整。

---

## G-3：安全加固清单（按优先级迭代）

| 项 | 说明 |
|----|------|
| 密钥轮换 | `SECRET_KEY`、`JWT_SECRET_KEY`、云 API Key、向量库 Token 等定期更换 |
| 上传扫描 | `FILE_VIRUS_SCAN_ENABLED` + ClamAV 等（见 `config.py`） |
| 敏感信息 | `SENSITIVE_MASK_ENABLED`、入库/检索脱敏；审计侧 `summarize_text_for_audit`（E-3） |
| 审计与追溯 | `audit_logs` + `request_id` / `trace_id`；关键操作可查 |
| 传输与存储 | HTTPS、MinIO 访问策略、数据库最小权限 |

---

**维护**：若实现与上表偏差，请在 `企业级改造计划与进度.md` 的「关键决策备忘」中追加一行原因与折中。
