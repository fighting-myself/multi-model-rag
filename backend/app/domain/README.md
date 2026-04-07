# domain（领域层）

**职责**：领域模型、检索/生成**策略接口**（`Protocol`）、领域异常；不依赖 FastAPI、SQLAlchemy 具体类型。

**依赖方向**：仅被 `application` 与 `infrastructure` 引用；`domain` 不导入 `infrastructure` 实现类。

**当前状态**：`domain/rag/ports.py` 已定义 `VectorStore`、`FulltextIndex`、`Reranker`、`RetrievalPipeline`（`Protocol`）；具体实现仍在 `services/`，阶段 C-2 再迁入。
