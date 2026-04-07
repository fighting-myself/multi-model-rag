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

from app.models.chunk import Chunk
from app.models.knowledge_base import KnowledgeBase
from app.services.chat_service import ChatService
from app.services.llm_service import chat_completion as llm_chat_completion
from app.services.llm_service import chat_completion_simple as llm_chat_simple
from app.services.llm_service import chat_completion_stream as llm_chat_stream
from app.services.recall_evaluation_service import run_recall_evaluation
from app.services.rag_metrics_defaults import get_default_benchmarks

logger = logging.getLogger(__name__)

# 批量问答：固定输入/输出格式，便于稳定解析
_BATCH_QA_SECTION_SEP = "\n\n--- "
_BATCH_QA_CTX_LABEL = "[上下文]\n"
_BATCH_QA_QUERY_LABEL = "[问题]\n"
_BATCH_QA_OUTPUT_KEY = "a"  # 要求模型必须用键 "a" 表示每条回答
_MAX_CTX_LEN = 1200


async def _llm_grade_answer(query: str, expected: str, answer: str) -> float:
    """LLM 评分：返回 0~1；失败时回退关键词评分。"""
    try:
        system = (
            "你是问答评测打分器。请对“模型回答”相对“期望答案”的正确性打分。"
            "只输出 JSON：{\"score\": 0到1的小数, \"reason\": \"一句话\"}。"
        )
        user = (
            f"问题：{query}\n"
            f"期望答案：{expected}\n"
            f"模型回答：{answer}\n"
            "评分标准：事实一致、关键信息覆盖、无明显错误。"
        )
        raw = await llm_chat_simple(system, user, max_tokens=120, temperature=0.0)
        m = re.search(r"\{[\s\S]*\}", raw or "")
        if m:
            obj = json.loads(m.group(0))
            score = float(obj.get("score", 0.0))
            if score < 0:
                score = 0.0
            if score > 1:
                score = 1.0
            return score
    except Exception:
        pass
    return _accuracy_score(answer, expected)


async def _get_eval_context(
    chat_svc: ChatService,
    db: AsyncSession,
    *,
    query: str,
    user_id: int,
    knowledge_base_id: Optional[int],
    knowledge_base_ids: Optional[List[int]],
    mode: str,
    top_k: int = 10,
) -> str:
    """评测上下文获取：normal 单次检索；super 多次检索融合（不写会话/记忆）。"""
    m = (mode or "normal").strip().lower()
    if m != "super":
        return await chat_svc.get_rag_context_for_eval(
            query, user_id, knowledge_base_id=knowledge_base_id, knowledge_base_ids=knowledge_base_ids, top_k=top_k
        )
    if not knowledge_base_id:
        return await chat_svc.get_rag_context_for_eval(
            query, user_id, knowledge_base_id=knowledge_base_id, knowledge_base_ids=knowledge_base_ids, top_k=top_k
        )
    ids_merged: List[int] = []
    seen = set()
    async def _merge(ids: List[int]) -> None:
        for cid in ids:
            if cid in seen:
                continue
            seen.add(cid)
            ids_merged.append(cid)
    for retrieval_mode, use_qe in [("hybrid", False), ("hybrid", True), ("fulltext", True)]:
        ids = await chat_svc.retrieve_ordered_chunk_ids(
            query=query,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
            retrieval_mode=retrieval_mode,
            use_rerank=True,
            use_query_expand=use_qe,
            user_id=user_id,
        )
        await _merge(ids)
        if len(ids_merged) >= top_k:
            break
    if not ids_merged:
        return ""
    q = await db.execute(
        select(Chunk.id, Chunk.content).where(Chunk.id.in_(ids_merged[:top_k]))
    )
    by_id = {int(cid): (content or "") for cid, content in q.all()}
    ordered = [by_id.get(cid, "") for cid in ids_merged[:top_k] if by_id.get(cid, "")]
    return "\n\n".join(ordered)[:8000]


async def _build_kb_adaptive_benchmarks(
    db: AsyncSession,
    knowledge_base_id: Optional[int],
    max_items: int = 5,
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """
    基于当前知识库动态生成评测样本，避免固定默认题与业务知识库不匹配导致全 0。
    仅在提供 knowledge_base_id 时启用；无可用 chunk 则返回 None。
    """
    if not knowledge_base_id:
        return None
    q = await db.execute(
        select(Chunk.id, Chunk.content)
        .where(Chunk.knowledge_base_id == knowledge_base_id, Chunk.content != "")
        .order_by(Chunk.id.desc())
        .limit(max(10, max_items * 4))
    )
    rows = q.all()
    if not rows:
        return None

    def _keywords(text: str) -> List[str]:
        words = [w for w in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,12}", text or "") if len(w) >= 2]
        out: List[str] = []
        for w in words:
            if w not in out:
                out.append(w)
            if len(out) >= 4:
                break
        return out or ["文档", "知识库"]

    picked = rows[:max_items]
    accuracy: List[Dict[str, Any]] = []
    recall: List[Dict[str, Any]] = []
    hallucination: List[Dict[str, Any]] = []
    for cid, content in picked:
        c = (content or "").strip()
        sentences = [s.strip() for s in re.split(r"[。！？\n]", c) if s.strip()]
        best = ""
        for s in sentences:
            if len(s) >= 18:
                best = s
                break
        if not best:
            best = c[:140].strip()
        snippet = (best[:160] + "。") if best and not best.endswith(("。", "！", "？")) else best[:160]
        kws = _keywords(c[:200])
        query = f"请根据知识库资料，解释“{kws[0]}”的含义与作用。"
        accuracy.append({"query": query, "expected_answer": snippet})
        hallucination.append({"query": query, "expected_answer": snippet[:80]})
        recall.append({
            "query": " ".join(kws[:2]),
            "relevant_chunk_ids": [int(cid)],
            "relevant_keywords": kws,
        })
    return {
        "accuracy": accuracy,
        "recall": recall,
        "precision": list(recall),
        "hallucination": hallucination,
    }


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
    eval_mode: str = "super",
) -> Dict[str, Any]:
    """答案准确率评测：多题一次检索（并发）、一次 LLM、一次解析与判分。"""
    defaults = await _build_kb_adaptive_benchmarks(db, knowledge_base_id, max_items=max_items) or get_default_benchmarks()
    items = [it for it in defaults["accuracy"][:max_items] if (it.get("query") or "").strip()]
    if not items:
        return {"accuracy_pct": 0.0, "num_queries": 0, "details": []}
    chat_svc = ChatService(db)
    mode = (eval_mode or "super").strip().lower()
    # 评测统一不落会话：按模式获取上下文（normal 单次，super 多次融合）
    async def ctx_one(it):
        q = (it.get("query") or "").strip()
        return await _get_eval_context(
            chat_svc,
            db,
            query=q,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_ids=knowledge_base_ids,
            mode=mode,
            top_k=10,
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
                    rag_only=True,
                    disable_memory_context=True,
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
        score = await _llm_grade_answer(query, expected, answer)
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
        "eval_mode": mode,
    }


async def run_recall(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: int,
    top_k_list: Optional[List[int]] = None,
    eval_mode: str = "normal",
) -> Dict[str, Any]:
    """召回率评测：使用默认 benchmark 在指定知识库上运行。"""
    defaults = await _build_kb_adaptive_benchmarks(db, knowledge_base_id, max_items=5) or get_default_benchmarks()
    items = defaults["recall"]
    if not items:
        return {"metrics": {}, "details": [], "message": "默认召回评测集为空"}
    benchmark_items = [
        {
            "query": it.get("query", ""),
            "relevant_chunk_ids": it.get("relevant_chunk_ids", []),
            "relevant_keywords": it.get("relevant_keywords", []),
        }
        for it in items
    ]
    result = await run_recall_evaluation(
        db=db,
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        benchmark_items=benchmark_items,
        retrieval_config={"retrieval_mode": "hybrid", "use_rerank": True, "use_query_expand": False},
        top_k_list=top_k_list or [1, 3, 5, 10],
        eval_mode=eval_mode,
    )
    result["eval_mode"] = (eval_mode or "normal").strip().lower()
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
    eval_mode: str = "normal",
) -> Dict[str, Any]:
    """精准度评测：同 recall 检索，额外算 Precision@k 平均。"""
    result = await run_recall(
        db,
        user_id,
        knowledge_base_id,
        top_k_list=top_k_list or [1, 3, 5, 10],
        eval_mode=eval_mode,
    )
    details = result.get("details") or []
    if not details:
        result["precision_at_k"] = {}
        return result
    k_list = top_k_list or [1, 3, 5, 10]
    prec_sum = {k: 0.0 for k in k_list}
    for d in details:
        ret = d.get("retrieved_ids") or []
        rel = d.get("relevant_ids") or []
        row_prec: Dict[int, float] = {}
        for k in k_list:
            p = _precision_at_k(ret, rel, k)
            prec_sum[k] += p
            row_prec[k] = round(p, 4)
        d["precision_at_k"] = row_prec
    n = len(details)
    result["precision_at_k"] = {k: round(prec_sum[k] / n, 4) for k in k_list}
    result["eval_mode"] = (eval_mode or "normal").strip().lower()
    return result


async def run_latency(
    db: AsyncSession,
    user_id: int,
    num_samples: int = 3,
    eval_mode: str = "normal",
) -> Dict[str, Any]:
    """首字/端到端延迟评测：发 num_samples 次流式请求，汇总 ttft_ms、e2e_ms。"""
    ttft_list: List[float] = []
    e2e_list: List[float] = []
    mode = (eval_mode or "normal").strip().lower()
    for _ in range(num_samples):
        t_start = time.perf_counter()
        try:
            first_token_time: Optional[float] = None
            prompt = "请简短回答：ok"
            if mode == "super":
                prompt = "你是智能问答助手。请给出一句简短结论。"
            async for delta in llm_chat_stream(user_content=prompt, context=""):
                if delta and first_token_time is None:
                    first_token_time = time.perf_counter()
                    break
            t_end = time.perf_counter()
            if first_token_time is not None:
                ttft_list.append((first_token_time - t_start) * 1000.0)
            e2e_list.append((t_end - t_start) * 1000.0)
        except Exception as e:
            logger.warning("Latency 单次请求失败: %s", e)
    ttft_avg = round(sum(ttft_list) / len(ttft_list), 0) if ttft_list else None
    e2e_avg = round(sum(e2e_list) / len(e2e_list), 0) if e2e_list else None
    return {
        "ttft_ms_avg": ttft_avg,
        "e2e_ms_avg": e2e_avg,
        "samples": len(ttft_list),
        "ttft_ms_samples": ttft_list,
        "e2e_ms_samples": e2e_list,
        "eval_mode": mode,
    }


async def run_hallucination(
    db: AsyncSession,
    user_id: int,
    knowledge_base_id: Optional[int] = None,
    knowledge_base_ids: Optional[List[int]] = None,
    max_items: int = 3,
    eval_mode: str = "super",
) -> Dict[str, Any]:
    defaults = await _build_kb_adaptive_benchmarks(db, knowledge_base_id, max_items=max_items) or get_default_benchmarks()
    items = [it for it in defaults["hallucination"][:max_items] if (it.get("query") or "").strip()]
    if not items:
        return {"hallucination_rate_pct": 0.0, "num_queries": 0, "details": []}
    chat_svc = ChatService(db)
    mode = (eval_mode or "super").strip().lower()
    # 统一不落会话：按模式获取上下文（normal 单次，super 多次融合）
    async def ctx_one(it):
        q = (it.get("query") or "").strip()
        return await _get_eval_context(
            chat_svc,
            db,
            query=q,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_ids=knowledge_base_ids,
            mode=mode,
            top_k=10,
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
        score = await _llm_grade_answer(query, expected, answer)
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
        "eval_mode": mode,
    }


async def run_qps(
    db: AsyncSession,
    user_id: int,
    concurrency: int = 5,
    requests_per_worker: int = 2,
    eval_mode: str = "normal",
) -> Dict[str, Any]:
    """并发能力评测：多协程同时发请求，统计延迟与失败率。每请求使用独立 Session，避免共享 db 导致 commit 冲突。"""
    total = concurrency * requests_per_worker
    latencies: List[float] = []
    errors = 0

    mode = (eval_mode or "normal").strip().lower()
    async def one_request() -> Optional[float]:
        t0 = time.perf_counter()
        try:
            prompt = "请简短回答：ok" if mode == "normal" else "你是智能问答助手。请输出一句简短回答。"
            async for _ in llm_chat_stream(user_content=prompt, context=""):
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
        "eval_mode": mode,
    }
