"""
BM25 关键词检索：对候选 chunk 做 BM25 打分，便于专有名词、编号、代码等精确匹配。
与向量检索结果经 RRF 融合后使用。
"""
import re
import math
from typing import List, Tuple, Any

# BM25 常数
K1 = 1.5
B = 0.75


def _tokenize(text: str) -> List[str]:
    """简单分词：按标点、空白切分，过滤短词与纯数字过长。"""
    if not text or not text.strip():
        return []
    # 保留中文连续、英文词、数字（编号等）
    tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", text)
    stop = {"的", "了", "是", "在", "和", "与", "或", "及", "等", "之", "为", "有", "被", "把", "对", "从", "到"}
    out = []
    for t in tokens:
        t = t.strip()
        if len(t) < 2:
            continue
        if t in stop:
            continue
        if t.isdigit() and len(t) > 20:
            continue
        out.append(t.lower() if t.isalpha() else t)
    return out


def bm25_score(
    query: str,
    chunks_with_content: List[Tuple[Any, str]],
    k1: float = K1,
    b: float = B,
) -> List[Tuple[Any, float]]:
    """
    对候选 (chunk_obj, content) 做 BM25 打分。
    
    Returns:
        [(chunk_obj, score), ...] 按 score 降序
    """
    if not chunks_with_content:
        return []
    q_terms = _tokenize(query)
    if not q_terms:
        return [(c, 0.0) for c, _ in chunks_with_content]

    docs = [content for _, content in chunks_with_content]
    N = len(docs)
    avgdl = sum(len(d) for d in docs) / N if N else 0
    if avgdl <= 0:
        return [(chunk, 0.0) for chunk, _ in chunks_with_content]

    # 文档长度
    doc_lens = [len(d) for d in docs]
    # 每个文档的 term 频率
    doc_tfs: List[dict] = []
    for d in docs:
        terms = _tokenize(d)
        tf = {}
        for t in terms:
            tf[t] = tf.get(t, 0) + 1
        doc_tfs.append(tf)

    # df(term) = 包含该 term 的文档数
    df = {}
    for t in q_terms:
        df[t] = sum(1 for dtf in doc_tfs if t in dtf)
    # IDF
    idf = {}
    for t in q_terms:
        n = df.get(t, 0)
        idf[t] = math.log((N - n + 0.5) / (n + 0.5) + 1.0)

    scored = []
    for i, (chunk, content) in enumerate(chunks_with_content):
        dtf = doc_tfs[i]
        dl = doc_lens[i]
        s = 0.0
        for t in q_terms:
            f = dtf.get(t, 0)
            if f == 0:
                continue
            s += idf[t] * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scored.append((chunk, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
