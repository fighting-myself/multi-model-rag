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
from app.schemas.evaluation import (
    RecallRunRequest,
    RecallRunResponse,
    BenchmarkDatasetCreate,
    BenchmarkDatasetUpdate,
    BenchmarkDatasetResponse,
    BenchmarkDatasetListResponse,
    BenchmarkItem,
)
from app.api.v1.auth import get_current_active_user
from app.schemas.auth import UserResponse
from app.services.recall_evaluation_service import run_recall_evaluation
from app.models.benchmark_dataset import BenchmarkDataset

router = APIRouter()
logger = logging.getLogger(__name__)


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
