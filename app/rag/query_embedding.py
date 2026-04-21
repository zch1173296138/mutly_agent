import os
import json
import math
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import chromadb
from openai import AsyncOpenAI
from dotenv import load_dotenv

from app.llm import call_llm
from app.llm.prompt_manager import render

load_dotenv()

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    """
    当前文件路径假设为:
      app/infrastructure/query_embedding.py
    """
    return Path(__file__).resolve().parents[2]
def _parse_json_array(text: str) -> List[str]:
    """
    尽量从模型输出里解析 JSON 数组。
    兼容 ```json [...] ``` 这种情况。
    """
    text = (text or "").strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

    start = text.find("[")
    end = text.rfind("]")

    if start >= 0 and end >= start:
        text = text[start:end + 1]

    try:
        data = json.loads(text)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    return [str(x).strip() for x in data if str(x).strip()]


async def rewrite_search_queries(
    query: str,
    max_queries: int = 4,
) -> List[str]:
    """
    把用户问题改写成多个检索 query。
    重点解决：
      1. 中文问题 vs 英文论文内容不匹配
      2. 用户问题太口语，和论文术语不匹配
      3. 单 query 召回太窄
    """
    chat_model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")


    prompt = f"""
你是一个论文 RAG 检索 query 改写器。

用户原始问题：
{query}

请生成 {max_queries} 个适合向量检索论文 chunk 的 query。

要求：
1. 第一个 query 尽量保留原始问题语义。
2. 如果原问题是中文，请生成英文论文术语版本。
3. 覆盖可能出现在论文中的不同表达方式。
4. 不要回答问题，只输出 query。
5. 只输出 JSON 数组，不要输出其他解释。

示例输出：
["original meaning query", "technical term query", "method-related query", "experiment-related query"]
""".strip()

    try:
        response = await call_llm(
            model=chat_model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=400,
        )

        content = response.get("content","")
        rewritten = _parse_json_array(content)

        queries: List[str] = []

        if query.strip():
            queries.append(query.strip())

        for q in rewritten:
            if q not in queries:
                queries.append(q)

        return queries[:max_queries]

    except Exception:
        logger.exception("query rewrite 失败，回退到原始 query")
        return [query]


def rrf_fuse(
    ranked_lists: List[List[Dict[str, Any]]],
    rrf_k: int = 60,
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion.

    适合融合：
      query1 的向量检索结果
      query2 的向量检索结果
      query3 的向量检索结果
      BM25 的检索结果
      section-title 的检索结果
    """
    fused: Dict[str, Dict[str, Any]] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list, start=1):
            chunk_id = item.get("chunk_id")

            if not chunk_id:
                continue

            rrf_score = 1.0 / (rrf_k + rank)

            if chunk_id not in fused:
                new_item = dict(item)
                new_item["rrf_score"] = rrf_score
                new_item["rrf_hits"] = 1
                fused[chunk_id] = new_item
            else:
                fused[chunk_id]["rrf_score"] += rrf_score
                fused[chunk_id]["rrf_hits"] += 1

                # 保留原始 Chroma score 更高的那份文本和 metadata
                if item.get("score", 0.0) > fused[chunk_id].get("score", 0.0):
                    old_rrf_score = fused[chunk_id]["rrf_score"]
                    old_rrf_hits = fused[chunk_id]["rrf_hits"]

                    new_item = dict(item)
                    new_item["rrf_score"] = old_rrf_score
                    new_item["rrf_hits"] = old_rrf_hits

                    fused[chunk_id] = new_item

    results = list(fused.values())

    results.sort(
        key=lambda x: (
            x.get("rrf_score", 0.0),
            x.get("rrf_hits", 0),
            x.get("score", 0.0),
        ),
        reverse=True,
    )

    return results
def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0

    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _min_max_normalize(values: List[float]) -> List[float]:
    if not values:
        return []

    min_v = min(values)
    max_v = max(values)

    if abs(max_v - min_v) < 1e-8:
        return [0.5 for _ in values]

    return [(v - min_v) / (max_v - min_v) for v in values]


async def embed_candidate_texts(candidates: List[Dict[str, Any]]) -> List[List[float]]:
    texts = []

    for item in candidates:
        section_title = item.get("section_title", "")
        text = item.get("text", "")

        # 截断，避免 embedding 太长
        texts.append(f"{section_title}\n{text}"[:3000])

    if not texts:
        return []

    return await embed_queries(texts)


def mmr_select(
    query_embedding: List[float],
    candidates: List[Dict[str, Any]],
    candidate_embeddings: List[List[float]],
    top_k: int,
    lambda_mult: float = 0.65,
) -> List[Dict[str, Any]]:
    """
    MMR 最终选择。

    MMR = lambda * relevance(query, candidate)
          - (1 - lambda) * max_similarity(candidate, selected)

    lambda_mult:
      越大越重视相关性；
      越小越重视多样性。

    推荐：
      论文 QA：0.65
      方法总结：0.60
      精确问答：0.75
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        selected = []

        for rank, item in enumerate(candidates, start=1):
            new_item = dict(item)
            new_item["mmr_rank"] = rank
            new_item["mmr_score"] = None
            selected.append(new_item)

        return selected

    if len(candidates) != len(candidate_embeddings):
        return candidates[:top_k]

    relevance_scores = [
        _cosine_similarity(query_embedding, emb)
        for emb in candidate_embeddings
    ]

    relevance_scores = _min_max_normalize(relevance_scores)

    selected_indices: List[int] = []
    remaining_indices = list(range(len(candidates)))

    while remaining_indices and len(selected_indices) < top_k:
        best_idx: Optional[int] = None
        best_score = -1e9

        for idx in remaining_indices:
            relevance = relevance_scores[idx]

            if not selected_indices:
                diversity_penalty = 0.0
            else:
                diversity_penalty = max(
                    _cosine_similarity(
                        candidate_embeddings[idx],
                        candidate_embeddings[selected_idx],
                    )
                    for selected_idx in selected_indices
                )

                # cosine similarity 范围可能是 [-1, 1]，这里转成 [0, 1]
                diversity_penalty = (diversity_penalty + 1.0) / 2.0

            mmr_score = (
                lambda_mult * relevance
                - (1.0 - lambda_mult) * diversity_penalty
            )

            # 可选：如果前面用了 RRF，可以加入一点稳定项
            mmr_score += 0.1 * float(candidates[idx].get("rrf_score", 0.0))

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx

        if best_idx is None:
            break

        selected_indices.append(best_idx)
        remaining_indices.remove(best_idx)

    selected = []

    for rank, idx in enumerate(selected_indices, start=1):
        item = dict(candidates[idx])
        item["mmr_rank"] = rank
        item["mmr_score"] = float(relevance_scores[idx])
        selected.append(item)

    return selected
def _get_chroma_collection(collection_name: str = "paper_chunks"):
    """
    获取 Chroma collection。
    需要和 step4 写入时使用同一个 storage/chroma 路径。
    """
    chroma_dir = _project_root() / "storage" / "chroma"

    client = chromadb.PersistentClient(path=str(chroma_dir))

    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    return collection


def _safe_json_loads(value: Any, default: Any):
    """
    Chroma metadata 中 image_paths/images/headers 是 JSON 字符串。
    这里做安全反序列化。
    """
    if value is None:
        return default

    if isinstance(value, (list, dict)):
        return value

    if not isinstance(value, str):
        return default

    try:
        return json.loads(value)
    except Exception:
        return default


def _safe_preview(text: str, max_chars: int = 500) -> str:
    text = text or ""
    text = text.replace("\n", " ").strip()

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "..."


async def embed_queries(queries: List[str]) -> List[List[float]]:
    """
    批量生成 query / chunk embedding。
    """
    embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )

    response = await client.embeddings.create(
        model=embedding_model,
        input=queries,
    )

    return [item.embedding for item in response.data]


async def embed_query(query: str) -> List[float]:
    embeddings = await embed_queries([query])
    return embeddings[0]


async def retrieve_paper_context(
    query: str,
    top_k: int = 6,
    fetch_k: int = 30,
    fused_fetch_k: int = 40,
    collection_name: str = "",
    use_query_rewrite: bool = True,
    use_rrf: bool = True,
    use_mmr: bool = True,
) -> Dict[str, Any]:
    """
    改进版论文上下文检索。

    流程：
      query
        -> query rewrite
        -> multi-query Chroma fetch_k
        -> RRF 融合
        -> MMR 选择最终 top_k
    """
    if not query or not query.strip():
        return {
            "query": query,
            "collection_name": collection_name,
            "results": [],
            "message": "query 为空，无法检索。",
        }

    collection_name = collection_name or os.getenv("CHROMA_COLLECTION", "paper_chunks")

    logger.info(
        f"开始增强检索: query={query}, top_k={top_k}, fetch_k={fetch_k}, "
        f"fused_fetch_k={fused_fetch_k}, collection_name={collection_name}, "
        f"use_query_rewrite={use_query_rewrite}, use_rrf={use_rrf}, use_mmr={use_mmr}"
    )

    collection = _get_chroma_collection(collection_name)

    if use_query_rewrite:
        search_queries = await rewrite_search_queries(query, max_queries=4)
    else:
        search_queries = [query]

    query_embeddings = await embed_queries(search_queries)
    main_query_embedding = query_embeddings[0]

    raw_result = collection.query(
        query_embeddings=query_embeddings,
        n_results=fetch_k,
        include=["documents", "metadatas", "distances", "embeddings"],
    )

    all_ids = raw_result.get("ids", [])
    all_documents = raw_result.get("documents", [])
    all_metadatas = raw_result.get("metadatas", [])
    all_distances = raw_result.get("distances", [])
    all_embeddings = raw_result.get("embeddings", [])

    ranked_lists: List[List[Dict[str, Any]]] = []

    for q_idx, search_query in enumerate(search_queries):
        ids = all_ids[q_idx] if q_idx < len(all_ids) else []
        documents = all_documents[q_idx] if q_idx < len(all_documents) else []
        metadatas = all_metadatas[q_idx] if q_idx < len(all_metadatas) else []
        distances = all_distances[q_idx] if q_idx < len(all_distances) else []
        embeddings = all_embeddings[q_idx] if q_idx < len(all_embeddings) else []

        ranked_list: List[Dict[str, Any]] = []

        for i in range(len(ids)):
            metadata = metadatas[i] or {}
            distance = float(distances[i])

            image_paths = _safe_json_loads(metadata.get("image_paths"), [])
            images = _safe_json_loads(metadata.get("images"), [])
            headers = _safe_json_loads(metadata.get("headers"), {})

            chunk_id = metadata.get("chunk_id", ids[i])

            # cosine distance 越小越相关
            score = 1.0 - distance

            embedding = None
            if embeddings is not None and i < len(embeddings):
                embedding = embeddings[i]

            item = {
                "score": float(score),
                "distance": distance,
                "chunk_id": chunk_id,
                "chunk_index": metadata.get("chunk_index", -1),
                "section_title": metadata.get("section_title", ""),
                "headers": headers,
                "text": documents[i],
                "image_paths": image_paths,
                "images": images,
                "char_count": metadata.get("char_count", 0),
                "matched_query": search_query,
                "matched_query_index": q_idx,
                "embedding": embedding,
            }

            ranked_list.append(item)

        ranked_lists.append(ranked_list)

    if use_rrf:
        candidates = rrf_fuse(ranked_lists, rrf_k=60)
    else:
        # 不用 RRF 时，直接合并去重，按向量分数排序
        merged: Dict[str, Dict[str, Any]] = {}

        for ranked_list in ranked_lists:
            for item in ranked_list:
                chunk_id = item.get("chunk_id")
                if not chunk_id:
                    continue

                old = merged.get(chunk_id)
                if old is None or item.get("score", 0.0) > old.get("score", 0.0):
                    merged[chunk_id] = item

        candidates = list(merged.values())
        candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    candidates = candidates[:fused_fetch_k]

    if use_mmr:
        candidate_embeddings = []

        has_missing_embedding = False

        for item in candidates:
            embedding = item.get("embedding")

            if embedding is None:
                has_missing_embedding = True
                break

            candidate_embeddings.append(list(embedding))

        # 如果 Chroma 没有返回 embedding，就重新对候选文本做 embedding
        if has_missing_embedding:
            candidate_embeddings = await embed_candidate_texts(candidates)

        final_results = mmr_select(
            query_embedding=main_query_embedding,
            candidates=candidates,
            candidate_embeddings=candidate_embeddings,
            top_k=top_k,
            lambda_mult=0.65,
        )
    else:
        final_results = candidates[:top_k]

    # 不要把 embedding 返回给前端/LLM，太大
    for item in final_results:
        item.pop("embedding", None)

    logger.info(
        f"增强检索完成: rewrite_queries={len(search_queries)}, "
        f"ranked_lists={len(ranked_lists)}, candidates={len(candidates)}, "
        f"final={len(final_results)}"
    )

    return {
        "query": query,
        "search_queries": search_queries,
        "collection_name": collection_name,
        "top_k": top_k,
        "fetch_k": fetch_k,
        "fused_fetch_k": fused_fetch_k,
        "use_query_rewrite": use_query_rewrite,
        "use_rrf": use_rrf,
        "use_mmr": use_mmr,
        "results": final_results,
    }


def build_rag_context(results: List[Dict[str, Any]]) -> str:
    """
    把检索到的 chunk 拼成 LLM 可读上下文。
    """
    context_blocks = []

    for i, item in enumerate(results, start=1):
        image_paths = item.get("image_paths", [])
        section_title = item.get("section_title", "")

        block = f"""
[来源 {i}]
chunk_id: {item.get("chunk_id", "")}
section_title: {section_title}
score: {item.get("score", -1)}
distance: {item.get("distance", -1)}
image_paths: {json.dumps(image_paths, ensure_ascii=False)}

content:
{item.get("text", "")}
""".strip()

        context_blocks.append(block)

    return "\n\n---\n\n".join(context_blocks)


def collect_unique_image_paths(results: List[Dict[str, Any]]) -> List[str]:
    """
    从检索结果中收集去重后的图片路径。
    """
    image_paths: List[str] = []

    for item in results:
        for path in item.get("image_paths", []):
            if path and path not in image_paths:
                image_paths.append(path)

    return image_paths


async def answer_with_retrieved_context(
    query: str,
    results: List[Dict[str, Any]],
) -> str:
    """
    根据 Chroma 检索到的 chunk 调用 LLM 生成最终回答。
    """
    if not results:
        return "没有检索到相关上下文，无法回答。"

    context_text = build_rag_context(results)

    chat_model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")


    system_prompt = render("rag_qa_system")

    user_prompt = render("rag_qa_user",query=query,context_text=context_text)

    response = await call_llm(
        model=chat_model,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=1800,
    )

    answer = response.get("content", "")

    if response.get("error"):
        raise ValueError(response["error"])

    return answer.strip()


async def ask_paper_agent_core(
    query: str,
    top_k: int = 6,
    fetch_k: int = 30,
    fused_fetch_k: int = 40,
    collection_name: str = "",
) -> Dict[str, Any]:
    """
    给 MCP Tool 调用的核心函数。

    内部流程：
      query
        → query embedding
        → Chroma 检索 chunk
        → LLM 回答
        → 返回 answer + sources + image_paths
    """
    collection_name = collection_name or os.getenv("CHROMA_COLLECTION", "paper_chunks")

    try:
        retrieved = await retrieve_paper_context(
            query=query,
            top_k=top_k,
            fetch_k=fetch_k,
            fused_fetch_k=fused_fetch_k,
            collection_name=collection_name,
            use_query_rewrite=True,
            use_rrf=True,
            use_mmr=True,
        )
    except Exception as e:
        logger.exception("论文检索失败")
        return {
            "query": query,
            "answer": f"论文检索失败: {e}",
            "collection_name": collection_name,
            "sources": [],
            "image_paths": [],
        }

    results = retrieved.get("results", [])

    if not results:
        return {
            "query": query,
            "answer": retrieved.get("message", "没有检索到相关上下文。"),
            "collection_name": collection_name,
            "sources": [],
            "image_paths": [],
        }

    try:
        answer = await answer_with_retrieved_context(query, results)
    except Exception as e:
        logger.exception("LLM 问答失败")
        answer = f"检索成功，但生成回答失败: {e}"

    image_paths = collect_unique_image_paths(results)

    sources = []

    for i, item in enumerate(results, start=1):
        sources.append({
            "source_id": i,
            "chunk_id": item.get("chunk_id", ""),
            "chunk_index": item.get("chunk_index", -1),
            "score": item.get("score", -1),
            "distance": item.get("distance", -1),
            "rrf_score": item.get("rrf_score", None),
            "rrf_hits": item.get("rrf_hits", None),
            "mmr_rank": item.get("mmr_rank", None),
            "mmr_score": item.get("mmr_score", None),
            "matched_query": item.get("matched_query", ""),
            "section_title": item.get("section_title", ""),
            "image_paths": item.get("image_paths", []),
            "preview": _safe_preview(item.get("text", ""), max_chars=500),
        })

    return {
        "query": query,
        "answer": answer,
        "collection_name": collection_name,
        "sources": sources,
        "image_paths": image_paths,
    }