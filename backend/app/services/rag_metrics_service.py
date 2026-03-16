"""
RAG 六大指标一键评测服务：使用默认评测集运行并返回指标结果。
准确率/幻觉率支持「一次输入、一次 LLM 输出、一次判断」的批量调用。
批量评测采用固定输入/输出格式，便于模型按格式返回、解析稳定。
"""
import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.knowledge_base import KnowledgeBase
from app.services.chat_service import ChatService
from app.services.llm_service import chat_completion as llm_chat_completion
from app.services.recall_evaluation_service import run_recall_evaluation
from app.services.rag_metrics_defaults import get_default_benchmarks

logger = logging.getLogger(__name__)

# 批量问答：固定输入/输出格式，便于稳定解析
_BATCH_QA_SECTION_SEP = "\n\n--- "
_BATCH_QA_CTX_LABEL = "[上下文]\n"
_BATCH_QA_QUERY_LABEL = "[问题]\n"
_BATCH_QA_OUTPUT_KEY = "a"  # 要求模型必须用键 "a" 表示每条回答
_MAX_CTX_LEN = 1200


def _accuracy_score(answer: str, expected: str) -> float:
    """简单打分：期望答案中的关键词在回答中出现的比例。"""
    if not (answer and expected):
        return 0.0
    answer = answer.strip()
    expected = expected.strip()
    if not expected:
        return 1.0
    words = [w for w in re.split(r"[，。！？\s、]+", expected) if len(w) >= 2]
    if not words:
        return 1.0 if expected in answer else 0.0
    hit = sum(1 for w in words if w in answer)
    return hit / len(words)


# LLM 批量返回的 JSON 中「回答」字段可能使用的键名（优先使用约定的 "a"）
_ANSWER_KEYS = ("a", "answer", "回答", "回答内容", "content", "reply", "回复", "模型回答")


def _build_batch_qa_prompt(
    items: List[Dict[str, Any]],
    contexts: List[str],
    max_ctx_len: int = _MAX_CTX_LEN,
) -> Tuple[str, str]:
    """
    构造固定格式的批量问答输入与输出要求，便于模型按格式返回、解析准确。
    返回 (user_content, system_content)。
    """
    n = len(items)
    parts = [f"[批量问答] 共 {n} 题。请按题号顺序，仅根据对应【上下文】作答。"]
    for i in range(n):
        q = (items[i].get("query") or "").strip()
        c = (contexts[i] if i < len(contexts) else "") or ""
        c = (c[:max_ctx_len] + ("..." if len(c) > max_ctx_len else "")) if c else "（无）"
        parts.append(f"{_BATCH_QA_SECTION_SEP}题目 {i + 1} ---\n{_BATCH_QA_CTX_LABEL}{c}\n{_BATCH_QA_QUERY_LABEL}{q}")
    user_content = "".join(parts)
    # 固定输出格式：仅 JSON 数组，每项必须为 {"a": "回答"}，禁止前后说明或 markdown
    example_items = ", ".join([f'{{"{_BATCH_QA_OUTPUT_KEY}":"答案{i+1}"}}' for i in range(min(n, 3))])
    if n > 3:
        example_items += ", ..."
    system_content = (
        "你是有帮助的助手。请仅根据上面每组【上下文】回答对应【问题】。\n\n"
        "【输出格式】你的回复必须是且仅是一个 JSON 数组，不要有任何前后说明、markdown 或代码块。\n"
        f"- 数组长度必须为 {n}，第 i 个元素对应第 i 题。\n"
        f"- 每个元素为对象，且必须包含键 \"{_BATCH_QA_OUTPUT_KEY}\"，值为你的回答文本。\n"
        f"示例（{min(n, 3)} 题时）：[{example_items}]"
    )
    return user_content, system_content


def _one_answer_from_item(x: Any) -> str:
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, dict):
        for k in _ANSWER_KEYS:
            v = x.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def _parse_batch_answers(raw: str, num_expected: int) -> List[str]:
    """解析批量评测时 LLM 返回的 JSON 数组，按顺序得到每条回答。支持多种键名。"""
    raw = (raw or "").strip()
    if not raw:
        return [""] * num_expected
    # 尝试提取 JSON 数组（兼容被 markdown 包裹）
    for start in ("[", "```json\n[", "```\n["):
        if start in raw:
            idx = raw.find(start)
            if start != "[":
                idx = raw.find("[", idx)
            end = raw.rfind("]") + 1
            if end > idx:
                try:
                    arr = json.loads(raw[idx:end])
                    if isinstance(arr, list):
                        answers = [_one_answer_from_item(arr[i]) if i < len(arr) else "" for i in range(num_expected)]
                        return answers
                except json.JSONDecodeError:
                    pass
    try:
        arr = json.loads(raw)
        if isinstance(arr, list):
            return [_one_answer_from_item(x) for x in arr[:num_expected]]
    except json.JSONDecodeError:
        pass
    return [""] * num_expected


async def run_accuracy(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: Optional[int] = None,
    knowledge_base_ids: Optional[List[int]] = None,
    max_items: int = 5,
) -> Dict[str, Any]:
    """答案准确率评测：多题一次检索（并发）、一次 LLM、一次解析与判分。"""
    defaults = get_default_benchmarks()
    items = [it for it in defaults["accuracy"][:max_items] if (it.get("query") or "").strip()]
    if not items:
        return {"accuracy_pct": 0.0, "num_queries": 0, "details": []}
    chat_svc = ChatService(db)
    # 1）并发获取每条问题的 RAG 上下文
    async def ctx_one(it):
        q = (it.get("query") or "").strip()
        return await chat_svc.get_rag_context_for_eval(
            q, user_id, knowledge_base_id=knowledge_base_id, knowledge_base_ids=knowledge_base_ids, top_k=10
        )
    contexts = await asyncio.gather(*[ctx_one(it) for it in items])
    # 2）固定输入/输出格式，一次 LLM 调用
    user_content, system_content = _build_batch_qa_prompt(items, contexts)
    try:
        raw = await llm_chat_completion(user_content, system_content=system_content, context="")
    except Exception as e:
        logger.warning("Accuracy 批量 LLM 调用失败: %s", e)
        raw = ""
    answers = _parse_batch_answers(raw, len(items))
    # 若批量解析结果全部为空，回退为逐条 chat，避免整页显示 0
    if not any((a or "").strip() for a in answers):
        logger.info("Accuracy 批量解析无有效回答，回退为逐条 chat")
        answers = []
        for it in items:
            q = (it.get("query") or "").strip()
            try:
                resp = await chat_svc.chat(
                    user_id=user_id,
                    message=q,
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base_ids=knowledge_base_ids,
                    enable_rag=True,
                    enable_mcp_tools=False,
                    enable_skills_tools=False,
                )
                answers.append((resp.message or "").strip())
            except Exception as e:
                logger.warning("Accuracy 单条请求失败: %s", e)
                answers.append("")
    # 3）一次判断：逐条算分并写 details
    details: List[Dict[str, Any]] = []
    correct_sum = 0.0
    for it, answer in zip(items, answers):
        query = (it.get("query") or "").strip()
        expected = (it.get("expected_answer") or "").strip()
        score = _accuracy_score(answer, expected)
        correct_sum += score
        details.append({
            "query": query,
            "expected": expected,
            "answer": (answer or "")[:200],
            "score": round(score, 3),
        })
    n = len(details)
    accuracy_pct = round((correct_sum / n * 100), 1) if n else 0.0
    return {
        "accuracy_pct": accuracy_pct,
        "num_queries": n,
        "details": details,
    }


async def run_recall(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: int,
    top_k_list: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """召回率评测：使用默认 benchmark 在指定知识库上运行。"""
    defaults = get_default_benchmarks()
    items = defaults["recall"]
    if not items:
        return {"metrics": {}, "details": [], "message": "默认召回评测集为空"}
    benchmark_items = [{"query": it.get("query", ""), "relevant_chunk_ids": it.get("relevant_chunk_ids", [])} for it in items]
    result = await run_recall_evaluation(
        db=db,
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        benchmark_items=benchmark_items,
        retrieval_config={"retrieval_mode": "hybrid", "use_rerank": True, "use_query_expand": False},
        top_k_list=top_k_list or [1, 3, 5, 10],
    )
    return result


def _precision_at_k(retrieved_ids: List[int], relevant_ids: List[int], k: int) -> float:
    """Precision@k = |retrieved[:k] ∩ relevant| / min(k, len(retrieved[:k]))"""
    if not retrieved_ids[:k]:
        return 0.0
    rel_set = set(relevant_ids)
    ret_set = set(retrieved_ids[:k])
    hit = len(rel_set & ret_set)
    return hit / len(ret_set)


async def run_precision(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: int,
    top_k_list: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """精准度评测：同 recall 检索，额外算 Precision@k 平均。"""
    result = await run_recall(db, user_id, knowledge_base_id, top_k_list=top_k_list or [1, 3, 5, 10])
    details = result.get("details") or []
    if not details:
        result["precision_at_k"] = {}
        return result
    k_list = top_k_list or [1, 3, 5, 10]
    prec_sum = {k: 0.0 for k in k_list}
    for d in details:
        ret = d.get("retrieved_ids") or []
        rel = d.get("relevant_ids") or []
        for k in k_list:
            prec_sum[k] += _precision_at_k(ret, rel, k)
    n = len(details)
    result["precision_at_k"] = {k: round(prec_sum[k] / n, 4) for k in k_list}
    return result


async def run_latency(
    db: AsyncSession,
    user_id: int,
    num_samples: int = 3,
) -> Dict[str, Any]:
    """首字/端到端延迟评测：发 num_samples 次流式请求，汇总 ttft_ms、e2e_ms。"""
    chat_svc = ChatService(db)
    ttft_list: List[float] = []
    e2e_list: List[float] = []
    for _ in range(num_samples):
        t_start = time.perf_counter()
        first_token_time: Optional[float] = None
        try:
            async for event in chat_svc.chat_stream(
                user_id=user_id,
                message="测速",
                knowledge_base_id=None,
                knowledge_base_ids=None,
                enable_rag=True,
                enable_mcp_tools=False,
                enable_skills_tools=False,
            ):
                if isinstance(event, dict):
                    if event.get("type") == "token" and first_token_time is None:
                        first_token_time = time.perf_counter()
                    if event.get("type") == "done":
                        if event.get("ttft_ms") is not None:
                            ttft_list.append(float(event["ttft_ms"]))
                        if event.get("e2e_ms") is not None:
                            e2e_list.append(float(event["e2e_ms"]))
                        break
        except Exception as e:
            logger.warning("Latency 单次请求失败: %s", e)
        t_end = time.perf_counter()
        if not e2e_list or len(e2e_list) < len(ttft_list) + 1:
            e2e_list.append((t_end - t_start) * 1000)
    ttft_avg = round(sum(ttft_list) / len(ttft_list), 0) if ttft_list else None
    e2e_avg = round(sum(e2e_list) / len(e2e_list), 0) if e2e_list else None
    return {
        "ttft_ms_avg": ttft_avg,
        "e2e_ms_avg": e2e_avg,
        "samples": len(ttft_list),
        "ttft_ms_samples": ttft_list,
        "e2e_ms_samples": e2e_list,
    }


async def run_hallucination(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: Optional[int] = None,
    knowledge_base_ids: Optional[List[int]] = None,
    max_items: int = 3,
) -> Dict[str, Any]:
    """幻觉率评测：多题一次检索（并发）、一次 LLM、一次解析与幻觉判断。"""
    defaults = get_default_benchmarks()
    items = [it for it in defaults["hallucination"][:max_items] if (it.get("query") or "").strip()]
    if not items:
        return {"hallucination_rate_pct": 0.0, "num_queries": 0, "details": []}
    chat_svc = ChatService(db)
    # 1）并发获取每条问题的 RAG 上下文
    async def ctx_one(it):
        q = (it.get("query") or "").strip()
        return await chat_svc.get_rag_context_for_eval(
            q, user_id, knowledge_base_id=knowledge_base_id, knowledge_base_ids=knowledge_base_ids, top_k=10
        )
    contexts = await asyncio.gather(*[ctx_one(it) for it in items])
    # 2）固定输入/输出格式，一次 LLM 调用（与 accuracy 共用格式）
    user_content, system_content = _build_batch_qa_prompt(items, contexts)
    try:
        raw = await llm_chat_completion(user_content, system_content=system_content, context="")
    except Exception as e:
        logger.warning("Hallucination 批量 LLM 调用失败: %s", e)
        raw = ""
    answers = _parse_batch_answers(raw, len(items))
    # 3）一次判断：逐条算分并判是否疑似幻觉
    details: List[Dict[str, Any]] = []
    hallucination_count = 0
    for it, answer, ctx in zip(items, answers, contexts):
        query = (it.get("query") or "").strip()
        expected = (it.get("expected_answer") or "").strip()
        score = _accuracy_score(answer, expected)
        ctx_snippet = (ctx or "")[:500].strip()
        is_likely_hallucination = score < 0.3 or (len(answer) > 100 and not ctx_snippet)
        if is_likely_hallucination:
            hallucination_count += 1
        details.append({
            "query": query,
            "answer_snippet": (answer or "")[:150],
            "score": round(score, 3),
            "is_likely_hallucination": is_likely_hallucination,
        })
    n = len(details)
    rate_pct = round((hallucination_count / n * 100), 1) if n else 0.0
    return {
        "hallucination_rate_pct": rate_pct,
        "num_queries": n,
        "details": details,
    }


async def run_qps(
    db: AsyncSession,
    user_id: int,
    concurrency: int = 5,
    requests_per_worker: int = 2,
) -> Dict[str, Any]:
    """并发能力评测：多协程同时发请求，统计延迟与失败率。每请求使用独立 Session，避免共享 db 导致 commit 冲突。"""
    total = concurrency * requests_per_worker
    latencies: List[float] = []
    errors = 0

    async def one_request() -> Optional[float]:
        t0 = time.perf_counter()
        try:
            async with AsyncSessionLocal() as session:
                chat_svc = ChatService(session)
                async for event in chat_svc.chat_stream(
                    user_id=user_id,
                    message="QPS测速",
                    enable_rag=True,
                    enable_mcp_tools=False,
                    enable_skills_tools=False,
                ):
                    if isinstance(event, dict) and event.get("type") == "done":
                        break
            return (time.perf_counter() - t0) * 1000
        except Exception as e:
            logger.warning("QPS 单次请求失败: %s", e)
            return None

    async def worker() -> None:
        nonlocal errors
        for _ in range(requests_per_worker):
            lat = await one_request()
            if lat is not None:
                latencies.append(lat)
            else:
                errors += 1

    start = time.perf_counter()
    await asyncio.gather(*[worker() for _ in range(concurrency)])
    elapsed = time.perf_counter() - start
    success = len(latencies)
    failure_rate_pct = round((errors / total * 100), 1) if total else 0.0
    qps = round(success / elapsed, 2) if elapsed > 0 else 0.0
    avg_latency_ms = round(sum(latencies) / len(latencies), 0) if latencies else None
    return {
        "qps": qps,
        "total_requests": total,
        "success": success,
        "failure_rate_pct": failure_rate_pct,
        "avg_latency_ms": avg_latency_ms,
        "elapsed_sec": round(elapsed, 2),
    }
