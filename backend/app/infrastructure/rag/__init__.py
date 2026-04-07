"""RAG 基础设施：混合检索管线与公共算子。

`HybridRetrievalPipeline` 请从 `app.infrastructure.rag.hybrid_retrieval_pipeline` 导入，避免包级导入拉起重依赖。
"""
from app.infrastructure.rag.hybrid_ops import rrf_score

__all__ = ["rrf_score"]
