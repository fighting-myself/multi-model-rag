"""
召回率评测与 Benchmark 数据集 API
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.models.chunk import Chunk
from app.models.knowledge_base import KnowledgeBase
from app.schemas.evaluation import (
    RecallRunRequest,
    RecallRunResponse,
    BenchmarkDatasetCreate,
    BenchmarkDatasetUpdate,
    BenchmarkDatasetResponse,
    BenchmarkDatasetListResponse,
    BenchmarkItem,
    RAGMetricsResponse,
    RAGMetricItem,
    RunAccuracyRequest,
    RunRecallRequest,
    RunPrecisionRequest,
    RunLatencyRequest,
    RunHallucinationRequest,
    RunQPSRequest,
)
from app.api.v1.auth import get_current_active_user
from app.schemas.auth import UserResponse
from app.services.recall_evaluation_service import run_recall_evaluation
from app.services.rag_metrics_defaults import get_default_benchmarks
from app.services.rag_metrics_service import (
    run_accuracy,
    run_recall,
    run_precision,
    run_latency,
    run_hallucination,
    run_qps,
)
from app.models.benchmark_dataset import BenchmarkDataset

router = APIRouter()
logger = logging.getLogger(__name__)

# Advanced RAG 六大指标说明（按优先级）
RAG_METRICS_DEFINITIONS = [
    RAGMetricItem(
        priority=1,
        id="accuracy",
        name="答案准确率 / 正确率",
        name_en="Accuracy / Answer Correctness",
        description="看模型回答对不对、有没有幻觉。企业最关心能不能用、敢不敢给客户用。",
        tip="评估答案与事实、上下文、问题意图的一致性。",
        link=None,
        unit="%",
    ),
    RAGMetricItem(
        priority=2,
        id="recall",
        name="检索召回率 Recall",
        name_en="Recall@1 / @3 / @5 / @10",
        description="RAG 效果的根，召回差则答案必错。看正确的文档块有没有被检索出来。",
        tip="衡量正确文档块是否被检索系统找回。",
        link="/recall-evaluation",
        unit="%",
    ),
    RAGMetricItem(
        priority=3,
        id="precision",
        name="检索精准度 Precision",
        name_en="Precision",
        description="看检索回来的内容有没有用。召回高但精准低 → 给模型喂一堆垃圾 → 答案乱。",
        tip="衡量返回结果中有效信息占比，需与召回平衡。",
        link=None,
        unit="%",
    ),
    RAGMetricItem(
        priority=4,
        id="latency",
        name="首包延迟 / 首字延迟",
        name_en="TTFT & E2E Latency",
        description="首字延迟 TTFT（Time To First Token）、端到端延迟 E2E。用户体验生命线。",
        tip="衡量交互响应速度与端到端时延。",
        link=None,
        unit="ms",
    ),
    RAGMetricItem(
        priority=5,
        id="hallucination",
        name="幻觉率 Hallucination Rate",
        name_en="Hallucination Rate",
        description="看模型瞎编、捏造、引用错误的比例。企业风控红线。",
        tip="衡量回答中捏造、错误引用、无依据推断的比例。",
        link=None,
        unit="%",
    ),
    RAGMetricItem(
        priority=6,
        id="qps",
        name="并发能力 QPS",
        name_en="QPS / Concurrency",
        description="看系统能不能扛量。如 10/20/50 并发下的延迟、失败率。",
        tip="衡量系统在并发场景下的吞吐与稳定性。",
        link=None,
        unit="req/s",
    ),
]


@router.get("/rag-metrics", response_model=RAGMetricsResponse)
async def get_rag_metrics(
    current_user: UserResponse = Depends(get_current_active_user),
):
    """获取 RAG 六大指标说明与参考标准（用于前端展示）。"""
    return RAGMetricsResponse(
        metrics=RAG_METRICS_DEFINITIONS,
        latency_standards={
            "internal": "内网/内部工具：≤ 1~2 秒",
            "toc": "ToC 产品/对话助手：≤ 800ms~1s",
            "search": "搜索类：≤ 500ms",
        },
    )


@router.get("/rag-metrics/defaults")
async def get_rag_metrics_defaults(
    current_user: UserResponse = Depends(get_current_active_user),
):
    """获取默认评测集（若不存在则生成并保存）。用于一键评测。"""
    return get_default_benchmarks()


@router.get("/rag-metrics/precheck")
async def get_rag_metrics_precheck(
    knowledge_base_id: Optional[int] = Query(None),
    eval_mode: str = Query("normal"),
    metric_id: Optional[str] = Query(None),
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """评测前诊断：返回样本来源、知识库 chunk 情况、模式与关键提醒。"""
    mode = (eval_mode or "normal").strip().lower()
    if mode not in ("normal", "super"):
        mode = "normal"
    metric = (metric_id or "").strip().lower()
    kb_name = None
    chunk_count = 0
    avg_chunk_chars = 0
    has_access = True
    if knowledge_base_id:
        kb_q = await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.user_id == current_user.id,
            )
        )
        kb = kb_q.scalar_one_or_none()
        if not kb:
            has_access = False
        else:
            kb_name = kb.name
            cc_q = await db.execute(
                select(
                    func.count(Chunk.id),
                    func.avg(func.length(Chunk.content)),
                ).where(
                    Chunk.knowledge_base_id == knowledge_base_id,
                    Chunk.content != "",
                )
            )
            c, avg_len = cc_q.one()
            chunk_count = int(c or 0)
            avg_chunk_chars = int(avg_len or 0)
    sample_source = "default_seed"
    if knowledge_base_id and chunk_count > 0:
        sample_source = "adaptive_kb"
    warnings = []
    if knowledge_base_id and not has_access:
        warnings.append("所选知识库不存在或无权限访问，将回退为默认评测集。")
    if metric in ("recall", "precision") and not knowledge_base_id:
        warnings.append("召回率/精准度未选择知识库时，结果参考意义较弱。")
    if sample_source == "default_seed":
        warnings.append("当前将使用内置默认样本，若知识库主题不一致可能导致分数偏低。")
    if chunk_count == 0 and knowledge_base_id:
        warnings.append("当前知识库暂无可用 chunk，评测结果可能为 0。")
    if mode == "normal":
        warnings.append("普通模式评测已禁用跨会话记忆注入，优先反映基础检索与响应性能。")
    else:
        warnings.append("超能模式会启用多阶段编排，更贴近真实问答效果。")
    if metric in ("recall", "precision"):
        warnings.append("召回率采用归一化口径：Recall@k = 命中数 / min(相关数, k)。")
    return {
        "knowledge_base_id": knowledge_base_id,
        "knowledge_base_name": kb_name,
        "eval_mode": mode,
        "metric_id": metric or None,
        "chunk_count": chunk_count,
        "avg_chunk_chars": avg_chunk_chars,
        "sample_source": sample_source,
        "memory_context_disabled_for_eval": True,
        "warnings": warnings,
    }


@router.post("/rag-metrics/run-accuracy")
async def run_rag_accuracy(
    body: RunAccuracyRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """一键评测：答案准确率。使用默认 Q&A 集跑 RAG，与期望答案对比。"""
    try:
        return await run_accuracy(
            db=db,
            user_id=current_user.id,
            knowledge_base_id=body.knowledge_base_id,
            knowledge_base_ids=body.knowledge_base_ids,
            eval_mode=body.eval_mode,
        )
    except Exception as e:
        logger.exception("准确率评测失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rag-metrics/run-recall")
async def run_rag_recall(
    body: RunRecallRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """一键评测：召回率。使用默认 benchmark 在指定知识库上运行。"""
    try:
        return await run_recall(
            db=db,
            user_id=current_user.id,
            knowledge_base_id=body.knowledge_base_id,
            eval_mode=body.eval_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("召回率评测失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rag-metrics/run-precision")
async def run_rag_precision(
    body: RunPrecisionRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """一键评测：精准度。同召回检索，额外计算 Precision@k。"""
    try:
        return await run_precision(
            db=db,
            user_id=current_user.id,
            knowledge_base_id=body.knowledge_base_id,
            eval_mode=body.eval_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("精准度评测失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rag-metrics/run-latency")
async def run_rag_latency(
    body: RunLatencyRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """一键评测：首字/端到端延迟。发送数次流式请求，汇总 TTFT、E2E。"""
    try:
        return await run_latency(
            db=db,
            user_id=current_user.id,
            num_samples=body.num_samples,
            eval_mode=body.eval_mode,
        )
    except Exception as e:
        logger.exception("延迟评测失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rag-metrics/run-hallucination")
async def run_rag_hallucination(
    body: RunHallucinationRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """一键评测：幻觉率。用默认集跑 RAG，简单判回答是否偏离期望/上下文。"""
    try:
        return await run_hallucination(
            db=db,
            user_id=current_user.id,
            knowledge_base_id=body.knowledge_base_id,
            knowledge_base_ids=body.knowledge_base_ids,
            eval_mode=body.eval_mode,
        )
    except Exception as e:
        logger.exception("幻觉率评测失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rag-metrics/run-qps")
async def run_rag_qps(
    body: RunQPSRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """一键评测：并发能力。多协程同时发请求，统计 QPS、延迟、失败率。"""
    try:
        return await run_qps(
            db=db,
            user_id=current_user.id,
            concurrency=body.concurrency,
            requests_per_worker=body.requests_per_worker,
            eval_mode=body.eval_mode,
        )
    except Exception as e:
        logger.exception("QPS 评测失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recall/run", response_model=RecallRunResponse)
async def run_recall_evaluation_api(
    body: RecallRunRequest,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """运行召回率评测：按所选检索方式在知识库上对 benchmark 样本逐条检索，计算 Recall@k、Hit@k、MRR。"""
    try:
        benchmark_items = [
            {"query": it.query, "relevant_chunk_ids": it.relevant_chunk_ids}
            for it in body.benchmark.items
        ]
        config = {
            "retrieval_mode": body.retrieval_config.retrieval_mode,
            "use_rerank": body.retrieval_config.use_rerank,
            "use_query_expand": body.retrieval_config.use_query_expand,
        }
        result = await run_recall_evaluation(
            db=db,
            user_id=current_user.id,
            knowledge_base_id=body.knowledge_base_id,
            benchmark_items=benchmark_items,
            retrieval_config=config,
            top_k_list=body.top_k_list or [1, 5, 10, 20],
        )
        return RecallRunResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("召回率评测失败")
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Benchmark 数据集 CRUD ----------


def _parse_items_json(items_text: Optional[str]) -> list:
    if not items_text or not items_text.strip():
        return []
    try:
        data = json.loads(items_text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


@router.get("/benchmarks", response_model=BenchmarkDatasetListResponse)
async def list_benchmark_datasets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    knowledge_base_id: Optional[int] = Query(None),
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的评测数据集列表（可选按知识库筛选）"""
    offset = (page - 1) * page_size
    count_stmt = select(func.count()).select_from(BenchmarkDataset).where(BenchmarkDataset.user_id == current_user.id)
    if knowledge_base_id is not None:
        count_stmt = count_stmt.where(BenchmarkDataset.knowledge_base_id == knowledge_base_id)
    total = (await db.execute(count_stmt)).scalar() or 0
    q = (
        select(BenchmarkDataset)
        .where(BenchmarkDataset.user_id == current_user.id)
        .order_by(BenchmarkDataset.updated_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    if knowledge_base_id is not None:
        q = q.where(BenchmarkDataset.knowledge_base_id == knowledge_base_id)
    rows = (await db.execute(q)).scalars().all()
    datasets = []
    for r in rows:
        items = _parse_items_json(r.items)
        datasets.append(
            BenchmarkDatasetResponse(
                id=r.id,
                user_id=r.user_id,
                knowledge_base_id=r.knowledge_base_id,
                name=r.name,
                description=r.description,
                items=[BenchmarkItem(**x) if isinstance(x, dict) else x for x in items],
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return BenchmarkDatasetListResponse(
        datasets=datasets,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/benchmarks", response_model=BenchmarkDatasetResponse, status_code=status.HTTP_201_CREATED)
async def create_benchmark_dataset(
    body: BenchmarkDatasetCreate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """创建评测数据集"""
    dataset = BenchmarkDataset(
        user_id=current_user.id,
        knowledge_base_id=body.knowledge_base_id,
        name=body.name,
        description=body.description,
        items=json.dumps([it.model_dump() for it in body.items], ensure_ascii=False),
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    items = _parse_items_json(dataset.items)
    return BenchmarkDatasetResponse(
        id=dataset.id,
        user_id=dataset.user_id,
        knowledge_base_id=dataset.knowledge_base_id,
        name=dataset.name,
        description=dataset.description,
        items=[BenchmarkItem(**x) if isinstance(x, dict) else x for x in items],
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


@router.get("/benchmarks/{dataset_id}", response_model=BenchmarkDatasetResponse)
async def get_benchmark_dataset(
    dataset_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单条评测数据集（用于编辑或加载到评测表单）"""
    result = await db.execute(
        select(BenchmarkDataset).where(
            BenchmarkDataset.id == dataset_id,
            BenchmarkDataset.user_id == current_user.id,
        )
    )
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="评测数据集不存在")
    items = _parse_items_json(dataset.items)
    return BenchmarkDatasetResponse(
        id=dataset.id,
        user_id=dataset.user_id,
        knowledge_base_id=dataset.knowledge_base_id,
        name=dataset.name,
        description=dataset.description,
        items=[BenchmarkItem(**x) if isinstance(x, dict) else x for x in items],
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


@router.put("/benchmarks/{dataset_id}", response_model=BenchmarkDatasetResponse)
async def update_benchmark_dataset(
    dataset_id: int,
    body: BenchmarkDatasetUpdate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """更新评测数据集"""
    result = await db.execute(
        select(BenchmarkDataset).where(
            BenchmarkDataset.id == dataset_id,
            BenchmarkDataset.user_id == current_user.id,
        )
    )
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="评测数据集不存在")
    if body.name is not None:
        dataset.name = body.name
    if body.description is not None:
        dataset.description = body.description
    if body.knowledge_base_id is not None:
        dataset.knowledge_base_id = body.knowledge_base_id
    if body.items is not None:
        dataset.items = json.dumps([it.model_dump() for it in body.items], ensure_ascii=False)
    await db.commit()
    await db.refresh(dataset)
    items = _parse_items_json(dataset.items)
    return BenchmarkDatasetResponse(
        id=dataset.id,
        user_id=dataset.user_id,
        knowledge_base_id=dataset.knowledge_base_id,
        name=dataset.name,
        description=dataset.description,
        items=[BenchmarkItem(**x) if isinstance(x, dict) else x for x in items],
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


@router.delete("/benchmarks/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_benchmark_dataset(
    dataset_id: int,
    current_user: UserResponse = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """删除评测数据集"""
    result = await db.execute(
        select(BenchmarkDataset).where(
            BenchmarkDataset.id == dataset_id,
            BenchmarkDataset.user_id == current_user.id,
        )
    )
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="评测数据集不存在")
    await db.delete(dataset)
    await db.commit()
    return None
