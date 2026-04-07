"""
检索相关端口（Protocol）：由 `infrastructure` 或过渡期内的 `services` 实现。
改造 C-1：仅定义契约，不改变现有调用路径。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

# 领域对象在实现层常为 ORM 或 DTO；此处用 Any 避免 domain 依赖 SQLAlchemy。
ChunkLike = Any


@runtime_checkable
class VectorStore(Protocol):
    """向量检索端口（按 query embedding 召回 chunk id / 分数）。"""

    async def search(
        self,
        *,
        query_embedding: List[float],
        knowledge_base_ids: List[int],
        top_k: int,
        user_id: int,
        **kwargs: Any,
    ) -> List[Tuple[ChunkLike, float]]:
        ...


@runtime_checkable
class FulltextIndex(Protocol):
    """全文检索端口（BM25 / 关键词等）。"""

    async def search(
        self,
        *,
        query: str,
        knowledge_base_ids: List[int],
        top_k: int,
        user_id: int,
        **kwargs: Any,
    ) -> List[Tuple[ChunkLike, float]]:
        ...


@runtime_checkable
class Reranker(Protocol):
    """重排序端口（query + 候选 chunk 文本 → 新分数或顺序）。"""

    async def rerank(
        self,
        *,
        query: str,
        candidates: List[Tuple[ChunkLike, float]],
        top_k: Optional[int] = None,
        **kwargs: Any,
    ) -> List[Tuple[ChunkLike, float]]:
        ...


@runtime_checkable
class RetrievalPipeline(Protocol):
    """完整检索管线：融合向量/全文、RRF、重排、窗口扩展等（由实现类组合上述能力）。"""

    async def retrieve(
        self,
        *,
        query: str,
        knowledge_base_ids: List[int],
        user_id: int,
        top_k: int = 10,
        **kwargs: Any,
    ) -> Tuple[str, float, Optional[str], List[ChunkLike], List[Tuple[ChunkLike, float]], Dict[str, Any]]:
        """
        返回：
        - rag_context: 拼入 prompt 的文本
        - rag_confidence: 置信度
        - max_confidence_context: 单段最高置信上下文（可选）
        - selected_chunks: 选中的 chunk 列表
        - scored_chunks: (chunk, score) 列表
        - debug: 可选调试信息
        """
        ...
