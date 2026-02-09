"""
向量存储服务：支持 Zilliz Cloud / Qdrant
"""
import hashlib
from typing import List, Optional, Dict, Any

# ========== 兼容性修复：必须在导入 pymilvus 之前执行 ==========

# 1. Python 3.12 + setuptools>=68.0.0 兼容性修复：确保 pkg_resources 可用
# setuptools>=68.0.0 移除了 pkg_resources，但 pymilvus 需要它
try:
    import pkg_resources
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.error(
        "pkg_resources 模块不可用。这是因为 setuptools>=68.0.0 移除了 pkg_resources。\n"
        "pymilvus 需要 pkg_resources。请运行以下命令降级 setuptools：\n"
        "pip install 'setuptools>=65.5.0,<68.0.0'\n"
        "然后重启后端服务。"
    )
    raise ImportError(
        "pkg_resources 模块不可用。pymilvus 需要它。\n"
        "请运行: pip install 'setuptools>=65.5.0,<68.0.0'"
    )

# 2. marshmallow 版本兼容性修复：marshmallow 4.x 移除了 __version_info__，pymilvus 需要它
# 必须在导入 pymilvus 之前修复，因为 pymilvus 在导入时会访问 marshmallow.__version_info__
try:
    import marshmallow
    if not hasattr(marshmallow, '__version_info__'):
        # marshmallow 4.x 移除了 __version_info__，手动添加兼容属性
        version_str = getattr(marshmallow, '__version__', '4.0.0')
        try:
            # 解析版本字符串，例如 "4.0.0" -> (4, 0, 0)
            version_parts = [int(x) for x in str(version_str).split('.')[:3]]
            while len(version_parts) < 3:
                version_parts.append(0)
            marshmallow.__version_info__ = tuple(version_parts)
        except (ValueError, AttributeError):
            # 如果解析失败，使用默认值
            marshmallow.__version_info__ = (4, 0, 0)
except ImportError:
    pass

from app.core.config import settings


def chunk_id_to_vector_id(chunk_id: int) -> str:
    """由 chunk_id 得到确定性向量 id（与插入时一致，跨进程不变）。"""
    h = hashlib.sha256(str(chunk_id).encode()).hexdigest()[:16]
    return str(int(h, 16) % (2**63))


# 模块级别的客户端缓存，避免每次请求都创建新连接
_vector_client_cache: Optional[Any] = None


def get_vector_client():
    """根据 VECTOR_DB_TYPE 返回对应客户端（Zilliz 或 Qdrant）。
    
    使用模块级别的缓存，确保整个应用生命周期内复用同一个客户端实例，
    避免每次请求都创建新的连接。
    """
    global _vector_client_cache
    if _vector_client_cache is None:
        if settings.VECTOR_DB_TYPE == "zilliz":
            _vector_client_cache = ZillizVectorStore()
        else:
            _vector_client_cache = QdrantVectorStore()
    return _vector_client_cache


class ZillizVectorStore:
    """Zilliz Cloud（Milvus 兼容）向量存储。"""

    def __init__(self):
        self._client = None
        self._collection = settings.ZILLIZ_COLLECTION_NAME
        self._dim = settings.ZILLIZ_DIM

    @property
    def client(self):
        if self._client is None:
            # 确保在导入 pymilvus 之前 marshmallow 兼容性已修复
            try:
                import marshmallow
                if not hasattr(marshmallow, '__version_info__'):
                    version_str = getattr(marshmallow, '__version__', '4.0.0')
                    try:
                        version_parts = [int(x) for x in str(version_str).split('.')[:3]]
                        while len(version_parts) < 3:
                            version_parts.append(0)
                        marshmallow.__version_info__ = tuple(version_parts)
                    except (ValueError, AttributeError):
                        marshmallow.__version_info__ = (4, 0, 0)
            except ImportError:
                pass
            
            from pymilvus import MilvusClient
            if not settings.ZILLIZ_URI or not settings.ZILLIZ_TOKEN:
                raise ValueError("ZILLIZ_URI 和 ZILLIZ_TOKEN 必须在 .env 中配置")
            self._client = MilvusClient(
                uri=settings.ZILLIZ_URI,
                token=settings.ZILLIZ_TOKEN,
            )
        return self._client

    def ensure_collection(self, actual_dim: Optional[int] = None) -> None:
        """若集合不存在则创建。
        
        Args:
            actual_dim: 实际的向量维度（如果提供，优先使用此维度；否则使用配置的维度）
        """
        import logging
        try:
            if self.client.has_collection(self._collection):
                return
            # 使用实际维度或配置维度
            dim_to_use = actual_dim if actual_dim is not None else self._dim
            logging.info(f"创建集合 {self._collection}，维度: {dim_to_use}")
            # MilvusClient.create_collection 的参数格式
            self.client.create_collection(
                collection_name=self._collection,
                dimension=dim_to_use,
                metric_type="COSINE",
            )
            logging.info(f"集合 {self._collection} 创建成功")
        except Exception as e:
            logging.warning(f"创建集合 {self._collection} 失败: {e}")
            # 如果创建失败，再次检查是否已存在（可能是并发创建或其他进程已创建）
            try:
                if self.client.has_collection(self._collection):
                    logging.info(f"集合 {self._collection} 已存在（可能是并发创建）")
                    return
            except Exception as e2:
                logging.error(f"检查集合存在性时出错: {e2}")
            # 如果集合仍然不存在，抛出异常
            raise ValueError(f"无法创建集合 {self._collection}，请检查 Zilliz 配置和权限")

    def insert(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """插入向量。ids 为 chunk_id 字符串，用确定性算法转成整数 id。
        
        metadatas 中的内容会保存到 Milvus 的标量字段中，包括文档块原文。
        
        注意：调用此方法前，应确保集合已存在（通常在 add_files 时通过 ensure_collection() 创建）。
        """
        data = []
        for i, (sid, vec) in enumerate(zip(ids, vectors)):
            try:
                cid = int(sid)
                vid = int(chunk_id_to_vector_id(cid))
            except ValueError:
                vid = int(hashlib.sha256(sid.encode()).hexdigest()[:16], 16) % (2**63)
            row = {"id": vid, "vector": vec}
            # 将 metadata 中的字段添加到 row 中（Milvus 会自动处理标量字段）
            if metadatas and i < len(metadatas):
                metadata = metadatas[i]
                # 将 metadata 中的字段添加到 row（Milvus 支持动态字段）
                for key, value in metadata.items():
                    # 确保值类型符合 Milvus 要求（字符串、整数、浮点数）
                    if isinstance(value, (str, int, float, bool)):
                        row[key] = value
                    elif isinstance(value, (list, dict)):
                        # 复杂类型转换为字符串
                        import json
                        row[key] = json.dumps(value, ensure_ascii=False)
                    else:
                        row[key] = str(value)
            data.append(row)
        self.client.insert(collection_name=self._collection, data=data)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """向量相似度搜索。
        
        注意：集合应该在创建知识库时通过 ensure_collection() 创建，
        检索时如果集合不存在，直接返回空结果，不尝试创建。
        """
        import logging
        # 检查集合是否存在（不创建，只检查）
        try:
            if not self.client.has_collection(self._collection):
                logging.warning(f"集合 {self._collection} 不存在，返回空结果（集合应在创建知识库时创建）")
                return []
        except Exception as e:
            logging.warning(f"检查集合存在性时出错: {e}，返回空结果")
            return []
        
        search_params = {"metric_type": "COSINE", "params": {}}
        try:
            res = self.client.search(
                collection_name=self._collection,
                data=[query_vector],
                limit=top_k,
                filter=filter_expr or "",
                search_params=search_params,
            )
            if res and len(res) > 0:
                return res[0]  # 单条 query 返回 List[dict]，每项含 id, distance, entity
        except Exception as e:
            logging.error(f"向量搜索失败: {e}")
            # 如果是集合不存在的错误，返回空结果而不是抛出异常
            if "collection not found" in str(e).lower() or "code=100" in str(e):
                logging.warning(f"集合 {self._collection} 不存在，返回空结果")
                return []
            raise
        return []


class QdrantVectorStore:
    """Qdrant 向量存储（保留原有逻辑，VECTOR_DB_TYPE=qdrant 时使用）。"""

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY or None,
            )
        return self._client

    def ensure_collection(self) -> None:
        """若集合不存在则创建。"""
        from qdrant_client.models import Distance, VectorParams
        collections = self.client.get_collections().collections
        names = [c.name for c in collections]
        if "documents" not in names:
            self.client.create_collection(
                collection_name="documents",
                vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            )

    def insert(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """插入向量。"""
        from qdrant_client.models import PointStruct
        points = [
            PointStruct(
                id=hash(ids[i]) % (2**63),
                vector=vectors[i],
                payload=metadatas[i] if metadatas and i < len(metadatas) else {},
            )
            for i in range(len(ids))
        ]
        self.client.upsert(collection_name="documents", points=points)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_expr: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """向量相似度搜索。"""
        hits = self.client.search(
            collection_name="documents",
            query_vector=query_vector,
            limit=top_k,
            query_filter=filter_expr,
        )
        return [{"id": h.id, "score": h.score, "payload": h.payload or {}} for h in hits]
