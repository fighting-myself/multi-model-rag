# infrastructure（基础设施适配器）

**职责**：向量库、Redis、对象存储、LLM SDK、外部 HTTP 等**具体实现**，实现 `domain` 中定义的端口。

**依赖方向**：实现 `domain` 接口；可被 `application` 注入。

**当前状态**：`infrastructure/rag/` 已含 `HybridRetrievalPipeline`（混合检索 + RRF + Rerank，由 `ChatService` 委托）、`hybrid_ops.rrf_score`、`progress.rag_progress_call`；其余仍多在 `app/services/`。
