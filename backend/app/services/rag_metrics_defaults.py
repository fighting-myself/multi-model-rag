"""
RAG 六大指标默认评测集：启动时或首次访问时确保存在，支持一键评测。
数据持久化到 backend/data/rag_default_benchmarks.json，若已存在则直接加载。
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from app.core.config import settings

logger = logging.getLogger(__name__)

# 默认存储路径（项目 backend 下 data 目录）
def _data_dir() -> Path:
    d = Path(settings.PROJECT_ROOT) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _benchmarks_path() -> Path:
    return _data_dir() / "rag_default_benchmarks.json"


# 内置种子数据（首次无文件时写入）
ACCURACY_SEED = [
    {"query": "什么是RAG？", "expected_answer": "RAG是检索增强生成，结合检索与生成模型的技术。"},
    {"query": "RAG的主要步骤有哪些？", "expected_answer": "主要步骤包括检索相关文档、将文档与问题拼接、送入生成模型得到答案。"},
    {"query": "向量数据库在RAG中的作用？", "expected_answer": "用于存储和快速检索文档的向量表示。"},
    {"query": "什么是召回率？", "expected_answer": "召回率指检索结果中相关文档占全部相关文档的比例。"},
    {"query": "首字延迟TTFT是什么？", "expected_answer": "首字延迟指从发送请求到收到第一个token的时间。"},
]

# 召回/精准：提供 relevant_keywords 便于在任意知识库中按内容解析相关 chunk，避免占位 ID 导致召回恒为 0
RECALL_SEED = [
    {"query": "什么是RAG？", "relevant_chunk_ids": [1, 2], "relevant_keywords": ["RAG", "检索", "生成"]},
    {"query": "RAG的主要步骤", "relevant_chunk_ids": [1, 2, 3], "relevant_keywords": ["RAG", "步骤", "检索", "生成"]},
    {"query": "向量检索与召回", "relevant_chunk_ids": [2, 3], "relevant_keywords": ["向量", "检索", "召回"]},
]

HALLUCINATION_SEED = [
    {"query": "什么是RAG？", "expected_answer": "RAG是检索增强生成。"},
    {"query": "RAG的主要步骤有哪些？", "expected_answer": "检索、拼接、生成。"},
    {"query": "向量数据库的作用？", "expected_answer": "存储和检索向量。"},
]


def _ensure_file() -> None:
    path = _benchmarks_path()
    if path.exists():
        return
    payload = {
        "accuracy": ACCURACY_SEED,
        "recall": RECALL_SEED,
        "precision": RECALL_SEED,
        "hallucination": HALLUCINATION_SEED,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("已生成默认 RAG 评测集: %s", path)
    except Exception as e:
        logger.warning("写入默认评测集失败: %s，将使用内存默认值", e)


def _ensure_recall_keywords(items: List[Dict[str, Any]]) -> None:
    """为召回/精准项补全 relevant_keywords（从问题中抽简短词），便于在任意知识库按内容解析相关 chunk。"""
    import re
    for it in items:
        if it.get("relevant_keywords"):
            continue
        q = (it.get("query") or "").strip()
        if not q:
            continue
        words = [w for w in re.split(r"[，。！？\s、？]+", q) if len(w) >= 2]
        if words:
            it["relevant_keywords"] = words[:5]


def get_default_benchmarks() -> Dict[str, List[Dict[str, Any]]]:
    """获取默认评测集；若文件不存在则创建并写入种子数据。"""
    _ensure_file()
    path = _benchmarks_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        recall = data.get("recall", RECALL_SEED)
        precision = data.get("precision", RECALL_SEED)
        _ensure_recall_keywords(recall)
        _ensure_recall_keywords(precision)
        return {
            "accuracy": data.get("accuracy", ACCURACY_SEED),
            "recall": recall,
            "precision": precision,
            "hallucination": data.get("hallucination", HALLUCINATION_SEED),
        }
    except Exception as e:
        logger.warning("读取默认评测集失败: %s，使用内存默认值", e)
        recall = list(RECALL_SEED)
        precision = list(RECALL_SEED)
        _ensure_recall_keywords(recall)
        _ensure_recall_keywords(precision)
        return {
            "accuracy": ACCURACY_SEED,
            "recall": recall,
            "precision": precision,
            "hallucination": HALLUCINATION_SEED,
        }
