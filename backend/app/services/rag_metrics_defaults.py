"""
RAG 六大指标默认评测集：启动时或首次访问时确保存在，支持一键评测。
数据持久化到 backend/data/rag_default_benchmarks.json，若已存在则直接加载。
"""
import json
import logging
import os
import hashlib
from pathlib import Path
from typing import Any, Dict, List

from app.core.config import settings

logger = logging.getLogger(__name__)
DEFAULT_BENCHMARKS_VERSION = 2

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
    payload = {
        "meta": {
            "version": DEFAULT_BENCHMARKS_VERSION,
            "signature": _seed_signature(),
        },
        "accuracy": ACCURACY_SEED,
        "recall": RECALL_SEED,
        "precision": RECALL_SEED,
        "hallucination": HALLUCINATION_SEED,
    }
    if path.exists():
        # 已存在时由 sync_default_benchmarks() 判定是否更新
        return
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


def _seed_signature() -> str:
    payload = {
        "accuracy": ACCURACY_SEED,
        "recall": RECALL_SEED,
        "precision": RECALL_SEED,
        "hallucination": HALLUCINATION_SEED,
        "version": DEFAULT_BENCHMARKS_VERSION,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_default_payload() -> Dict[str, Any]:
    out = {
        "meta": {
            "version": DEFAULT_BENCHMARKS_VERSION,
            "signature": _seed_signature(),
        },
        "accuracy": list(ACCURACY_SEED),
        "recall": list(RECALL_SEED),
        "precision": list(RECALL_SEED),
        "hallucination": list(HALLUCINATION_SEED),
    }
    _ensure_recall_keywords(out["recall"])
    _ensure_recall_keywords(out["precision"])
    return out


def sync_default_benchmarks() -> Dict[str, Any]:
    """
    启动时同步默认评测数据：
    - 文件不存在：创建默认
    - 存在且版本/签名匹配：保留
    - 存在但不匹配：删除旧文件并写入新默认
    """
    path = _benchmarks_path()
    expected_sig = _seed_signature()
    expected_ver = DEFAULT_BENCHMARKS_VERSION
    desired = _build_default_payload()
    need_rebuild = False
    old_meta: Dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f) or {}
            old_meta = old.get("meta") or {}
            old_sig = str(old_meta.get("signature") or "")
            old_ver = int(old_meta.get("version") or 0)
            if old_sig != expected_sig or old_ver != expected_ver:
                need_rebuild = True
        except Exception:
            need_rebuild = True
    else:
        need_rebuild = True
    if need_rebuild:
        try:
            if path.exists():
                path.unlink()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(desired, f, ensure_ascii=False, indent=2)
            logger.info(
                "默认评测集已同步重建 path=%s old_meta=%s new_version=%s",
                path,
                old_meta,
                expected_ver,
            )
        except Exception as e:
            logger.warning("同步默认评测集失败: %s", e)
    return desired


def get_default_benchmarks() -> Dict[str, List[Dict[str, Any]]]:
    """获取默认评测集；若文件不存在则创建并写入种子数据。"""
    _ensure_file()
    path = _benchmarks_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 运行期兜底：旧文件缺少 meta 或签名不匹配时，立即重建并返回新数据
        meta = data.get("meta") or {}
        if int(meta.get("version") or 0) != DEFAULT_BENCHMARKS_VERSION or str(meta.get("signature") or "") != _seed_signature():
            data = sync_default_benchmarks()
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
