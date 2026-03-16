"""
召回率评测与 Benchmark 数据集相关 Schema
"""
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime


class BenchmarkItem(BaseModel):
    """单条评测样本：问题 + 相关 chunk id 列表（标准答案）"""
    query: str = Field(..., description="查询文本")
    relevant_chunk_ids: List[int] = Field(default_factory=list, description="相关文档块 id 列表（标准答案）")


class BenchmarkData(BaseModel):
    """评测数据集（请求体或保存用）"""
    items: List[BenchmarkItem] = Field(default_factory=list, description="评测样本列表")


class RetrievalConfig(BaseModel):
    """检索方式组合配置"""
    retrieval_mode: str = Field(
        default="hybrid",
        description="vector=仅向量 | fulltext=仅全文(BM25) | hybrid=向量+全文 RRF 融合",
    )
    use_rerank: bool = Field(default=True, description="是否使用 Rerank 重排序")
    use_query_expand: bool = Field(default=False, description="是否使用查询改写/子问题扩展")


class RecallRunRequest(BaseModel):
    """发起召回率评测请求"""
    knowledge_base_id: int = Field(..., description="知识库 ID")
    retrieval_config: RetrievalConfig = Field(default_factory=RetrievalConfig)
    benchmark: BenchmarkData = Field(..., description="评测数据（问题与标准答案）")
    top_k_list: List[int] = Field(default=[1, 5, 10, 20], description="计算 Recall@k 的 k 列表")


class RecallRunResponse(BaseModel):
    """召回率评测结果"""
    config_snapshot: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    details: List[Dict[str, Any]] = Field(default_factory=list)


# --- Benchmark 数据集 CRUD ---


class BenchmarkDatasetCreate(BaseModel):
    """创建评测数据集"""
    name: str = Field(..., max_length=128)
    description: Optional[str] = None
    knowledge_base_id: Optional[int] = None
    items: List[BenchmarkItem] = Field(default_factory=list)


class BenchmarkDatasetUpdate(BaseModel):
    """更新评测数据集"""
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    knowledge_base_id: Optional[int] = None
    items: Optional[List[BenchmarkItem]] = None


class BenchmarkDatasetResponse(BaseModel):
    """评测数据集响应（列表/详情）"""
    id: int
    user_id: int
    knowledge_base_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    items: List[BenchmarkItem] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BenchmarkDatasetListResponse(BaseModel):
    """评测数据集列表响应"""
    datasets: List[BenchmarkDatasetResponse]
    total: int
    page: int
    page_size: int


# --- Advanced RAG 六大指标（说明与数据展示）---


class RAGMetricItem(BaseModel):
    """单条 RAG 指标定义（用于前端展示说明与跳转）"""
    priority: int = Field(..., description="优先级 1-6")
    id: str = Field(..., description="accuracy | recall | precision | latency | hallucination | qps")
    name: str = Field(..., description="指标名称")
    name_en: str = Field(default="", description="英文名")
    description: str = Field(default="", description="指标说明")
    tip: str = Field(default="", description="企业/面试场景说明")
    link: Optional[str] = Field(None, description="前端路由，如 /recall-evaluation")
    unit: Optional[str] = Field(None, description="单位，如 ms、%")


class RAGMetricsResponse(BaseModel):
    """Advanced RAG 六大指标说明与参考标准"""
    metrics: List[RAGMetricItem] = Field(default_factory=list)
    latency_standards: Dict[str, str] = Field(
        default_factory=lambda: {
            "internal": "内网/内部工具：≤ 1~2 秒",
            "toc": "ToC 产品/对话助手：≤ 800ms~1s",
            "search": "搜索类：≤ 500ms",
        }
    )


# --- 一键评测请求/响应 ---


class RunAccuracyRequest(BaseModel):
    """答案准确率一键评测"""
    knowledge_base_id: Optional[int] = None
    knowledge_base_ids: Optional[List[int]] = None


class RunRecallRequest(BaseModel):
    """召回率一键评测"""
    knowledge_base_id: int = Field(..., description="知识库 ID")


class RunPrecisionRequest(BaseModel):
    """精准度一键评测"""
    knowledge_base_id: int = Field(..., description="知识库 ID")


class RunLatencyRequest(BaseModel):
    """延迟一键评测"""
    num_samples: int = Field(default=3, ge=1, le=10)


class RunHallucinationRequest(BaseModel):
    """幻觉率一键评测"""
    knowledge_base_id: Optional[int] = None
    knowledge_base_ids: Optional[List[int]] = None


class RunQPSRequest(BaseModel):
    """QPS 一键评测"""
    concurrency: int = Field(default=5, ge=1, le=20)
    requests_per_worker: int = Field(default=2, ge=1, le=5)
