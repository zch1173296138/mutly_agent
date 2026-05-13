import os
import re
import json
import math
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from rank_bm25 import BM25Okapi
from dotenv import load_dotenv

from app.llm import call_llm
from app.llm.prompt_manager import render
from app.rag.common import (
    embed_texts,
    get_chroma_collection,
    safe_json_loads,
    safe_preview,
)

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
    chat_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    prompt = f"""
    你是论文 RAG 检索 query 改写器。

    用户原始问题：
    {query}

    请生成 {max_queries} 个适合检索论文 chunk 的 query。

    要求：
    1. 保留原始问题的核心意图。
    2. 如果原问题是中文，可以生成英文论文术语版本。
    3. 不要引入用户问题中没有的具体技术领域词。
    4. 不要使用 model architecture、neural network、deep learning 这类词，除非用户原问题明确提到。
    5. 对“main method / 核心方法”类问题，优先生成：
       - proposed method
       - technical approach
       - algorithm overview
       - method described in this paper
    6. 不要回答问题，只输出 query。
    7. 只输出 JSON 数组，不要输出其他解释。

    示例输出：
    ["What is the main method proposed in this paper?", "proposed method and technical approach", "algorithm overview of this paper", "method described in this paper"]
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
                new_item["retrieval_sources"] = [item.get("retrieval_source", "")]
                fused[chunk_id] = new_item
            else:
                old = fused[chunk_id]

                old["rrf_score"] += rrf_score
                old["rrf_hits"] += 1

                old_sources = old.get("retrieval_sources", [])
                if not old_sources:
                    old_sources = [old.get("retrieval_source", "")]

                new_source = item.get("retrieval_source", "")
                if new_source and new_source not in old_sources:
                    old_sources.append(new_source)

                old["retrieval_sources"] = old_sources

                # 保留有 embedding 的版本，方便后续 MMR 不用重新 embedding
                old_has_embedding = old.get("embedding") is not None
                new_has_embedding = item.get("embedding") is not None

                if new_has_embedding and not old_has_embedding:
                    keep_rrf_score = old["rrf_score"]
                    keep_rrf_hits = old["rrf_hits"]
                    keep_sources = old["retrieval_sources"]

                    new_item = dict(item)
                    new_item["rrf_score"] = keep_rrf_score
                    new_item["rrf_hits"] = keep_rrf_hits
                    new_item["retrieval_sources"] = keep_sources

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
def detect_chunk_type(text: str) -> str:
    text = (text or "").strip()

    has_figure = (
        "【图表信息补充开始】" in text
        and "【图表信息补充结束】" in text
    )

    has_equation = (
        "【公式信息开始】" in text
        and "【公式信息结束】" in text
    )

    if text.startswith("【图表信息补充开始】"):
        return "figure"

    if text.startswith("【公式信息开始】"):
        return "equation"

    if has_figure:
        return "figure_mixed"

    if has_equation:
        return "equation_mixed"

    return "text"


def detect_query_intent(query: str) -> str:
    q = (query or "").lower()

    if any(x in q for x in [
        "公式", "方程", "equation", "eq.", "eqn",
        "latex", "alpha", "theta", "constraint", "约束"
    ]):
        return "equation"

    if any(x in q for x in [
        "图", "图片", "图表", "figure", "fig.",
        "table", "表格", "caption"
    ]):
        return "figure"

    if any(x in q for x in [
        "method", "main method", "approach", "algorithm",
        "核心方法", "主要方法", "方法", "算法", "流程",
        "pipeline", "framework"
    ]):
        return "method"

    if any(x in q for x in [
        "contribution", "novel", "innovation",
        "贡献", "创新"
    ]):
        return "contribution"

    if any(x in q for x in [
        "problem", "challenge", "motivation",
        "解决什么问题", "问题", "挑战", "背景"
    ]):
        return "problem"

    return "general"
def _metadata_to_item(
    chunk_id: str,
    document: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = metadata or {}

    image_paths = _safe_json_loads(metadata.get("image_paths"), [])
    images = _safe_json_loads(metadata.get("images"), [])
    headers = _safe_json_loads(metadata.get("headers"), {})

    item = {
        "score": 0.0,
        "distance": None,
        "chunk_id": metadata.get("chunk_id", chunk_id),
        "chunk_index": metadata.get("chunk_index", -1),
        "section_title": metadata.get("section_title", ""),
        "headers": headers,
        "text": document or "",
        "image_paths": image_paths,
        "images": images,
        "char_count": metadata.get("char_count", 0),
        "matched_query": "",
        "matched_query_index": -1,
    }

    item["chunk_type"] = detect_chunk_type(item["text"])
    return item


def load_all_chroma_items(collection) -> List[Dict[str, Any]]:
    """
    从 Chroma collection 中读取所有 chunk，用于 BM25 / 关键词 / 公式 / 图表检索。

    适合当前论文级别 collection。
    如果后面 collection 很大，可以改成缓存或独立索引文件。
    """
    data = collection.get(
        include=["documents", "metadatas"],
    )

    ids = data.get("ids", [])
    documents = data.get("documents", [])
    metadatas = data.get("metadatas", [])

    items = []

    for i in range(len(ids)):
        item = _metadata_to_item(
            chunk_id=ids[i],
            document=documents[i] if i < len(documents) else "",
            metadata=metadatas[i] if i < len(metadatas) else {},
        )
        items.append(item)

    return items
def _tokenize_for_bm25(text: str) -> List[str]:
    text = (text or "").lower()

    tokens = re.findall(
        r"[a-zA-Z][a-zA-Z0-9_\-]*"
        r"|\\[a-zA-Z]+"
        r"|[0-9]+"
        r"|[\u4e00-\u9fff]+",
        text,
    )

    stopwords = {
        "the", "a", "an", "of", "to", "in", "on", "and", "or",
        "for", "with", "is", "are", "was", "were", "this", "that",
        "what", "how", "paper"
    }

    return [
        token.strip().lower()
        for token in tokens
        if token.strip() and token.strip().lower() not in stopwords
    ]
def _normalize_ranked_scores(
    ranked_list: List[Dict[str, Any]],
    score_key: str = "score",
) -> List[Dict[str, Any]]:
    if not ranked_list:
        return ranked_list

    scores = [float(item.get(score_key, 0.0)) for item in ranked_list]
    max_score = max(scores)

    if max_score <= 1e-8:
        return ranked_list

    for item in ranked_list:
        raw_score = float(item.get(score_key, 0.0))
        item["raw_score"] = raw_score
        item[score_key] = raw_score / max_score

    return ranked_list
def bm25_search(
    query: str,
    items: List[Dict[str, Any]],
    top_k: int = 30,
    source_name: str = "bm25",
) -> List[Dict[str, Any]]:
    """
    使用 rank-bm25 做关键词召回。
    """
    if not query or not items:
        return []

    corpus_texts = []

    for item in items:
        section_title = item.get("section_title", "")
        text = item.get("text", "")
        chunk_type = item.get("chunk_type") or detect_chunk_type(text)

        # BM25 索引文本：标题权重要高一点，所以重复一次 section_title
        corpus_texts.append(
            f"{section_title}\n{section_title}\n{text}\nchunk_type:{chunk_type}"
        )

    tokenized_corpus = [
        _tokenize_for_bm25(text)
        for text in corpus_texts
    ]

    tokenized_query = _tokenize_for_bm25(query)

    if not tokenized_query:
        return []

    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(tokenized_query)

    ranked_indices = sorted(
        range(len(scores)),
        key=lambda i: scores[i],
        reverse=True,
    )

    results = []

    for rank, idx in enumerate(ranked_indices[:top_k], start=1):
        score = float(scores[idx])

        if score <= 0:
            continue

        item = dict(items[idx])
        item["score"] = score
        item["bm25_rank"] = rank
        item["retrieval_source"] = source_name
        item["matched_query"] = query
        item["chunk_type"] = item.get("chunk_type") or detect_chunk_type(item.get("text", ""))

        results.append(item)

    return _normalize_ranked_scores(results)
def extract_equation_number_from_query(query: str) -> str:
    patterns = [
        r"公式\s*([0-9]+)",
        r"方程\s*([0-9]+)",
        r"equation\s*\(?\s*([0-9]+)\s*\)?",
        r"eq\.?\s*\(?\s*([0-9]+)\s*\)?",
        r"eqn\.?\s*\(?\s*([0-9]+)\s*\)?",
    ]

    for pattern in patterns:
        m = re.search(pattern, query or "", flags=re.IGNORECASE)
        if m:
            return m.group(1)

    return ""


def equation_tag_search(
    query: str,
    items: List[Dict[str, Any]],
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    equation_no = extract_equation_number_from_query(query)

    if not equation_no:
        return []

    patterns = [
        rf"\\tag\s*\{{\s*{equation_no}\s*\}}",
        rf"\\tag\s*{equation_no}",
        rf"公式编号:\s*{equation_no}",
        rf"Equation number:\s*{equation_no}",
        rf"Equation\s*\(?\s*{equation_no}\s*\)?",
        rf"Eqn\.?\s*\(?\s*{equation_no}\s*\)?",
    ]

    results = []

    for item in items:
        text = item.get("text", "")

        matched = any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in patterns
        )

        if not matched:
            continue

        new_item = dict(item)
        new_item["score"] = 1.0
        new_item["raw_score"] = 10.0
        new_item["retrieval_source"] = "equation_tag"
        new_item["matched_query"] = query
        results.append(new_item)

    return results[:top_k]
def section_title_search(
    query: str,
    items: List[Dict[str, Any]],
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    intent = detect_query_intent(query)

    if intent == "method":
        target_terms = [
            "method",
            "optimal corner assignment",
            "templates",
            "vertex type adjustment",
            "introduction",
            "discussion",
            "conclusion",
        ]
    elif intent == "problem":
        target_terms = [
            "abstract",
            "introduction",
            "problem statement",
        ]
    elif intent == "contribution":
        target_terms = [
            "abstract",
            "introduction",
            "discussion",
            "conclusion",
        ]
    elif intent == "equation":
        target_terms = [
            "problem statement",
            "vertex type",
            "optimization",
            "linear programming",
        ]
    else:
        return []

    results = []

    for item in items:
        section_title = (item.get("section_title") or "").lower()
        text = (item.get("text") or "").lower()

        score = 0.0

        for term in target_terms:
            if term in section_title:
                score += 3.0
            elif term in text[:800]:
                score += 1.0

        if score <= 0:
            continue

        new_item = dict(item)
        new_item["score"] = score
        new_item["retrieval_source"] = "section_title"
        new_item["matched_query"] = query
        results.append(new_item)

    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    return _normalize_ranked_scores(results[:top_k])
def figure_search(
    query: str,
    items: List[Dict[str, Any]],
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    intent = detect_query_intent(query)

    if intent != "figure":
        return []

    results = []

    for item in items:
        chunk_type = item.get("chunk_type") or detect_chunk_type(item.get("text", ""))

        if chunk_type not in {"figure", "figure_mixed"}:
            continue

        new_item = dict(item)
        new_item["score"] = 1.0
        new_item["raw_score"] = 5.0
        new_item["retrieval_source"] = "figure"
        new_item["matched_query"] = query
        results.append(new_item)

    return results[:top_k]
async def hybrid_retrieve_ranked_lists(
    query: str,
    search_queries: List[str],
    query_embeddings: List[List[float]],
    collection,
    fetch_k: int,
) -> Tuple[List[List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    混合召回：
      1. Dense Chroma 向量召回
      2. BM25 关键词召回
      3. 章节标题召回
      4. 公式编号召回
      5. 图表召回

    返回：
      ranked_lists: 给 RRF 融合
      all_items: 全量 Chroma items，后续可复用
    """
    ranked_lists: List[List[Dict[str, Any]]] = []

    # 1. Dense multi-query retrieval
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
            score = 1.0 - distance

            item = _metadata_to_item(
                chunk_id=ids[i],
                document=documents[i],
                metadata=metadata,
            )

            item["score"] = float(score)
            item["distance"] = distance
            item["matched_query"] = search_query
            item["matched_query_index"] = q_idx
            item["retrieval_source"] = "dense"

            if embeddings is not None and i < len(embeddings):
                item["embedding"] = embeddings[i]
            else:
                item["embedding"] = None

            ranked_list.append(item)

        ranked_lists.append(ranked_list)

    # 2. Load all items for lexical indexes
    all_items = load_all_chroma_items(collection)

    # 3. BM25 for original query
    ranked_lists.append(
        bm25_search(
            query=query,
            items=all_items,
            top_k=fetch_k,
            source_name="bm25_original",
        )
    )

    # 4. BM25 for rewritten queries
    for q in search_queries[1:]:
        ranked_lists.append(
            bm25_search(
                query=q,
                items=all_items,
                top_k=max(10, fetch_k // 2),
                source_name="bm25_rewrite",
            )
        )

    # 5. Equation exact index
    eq_results = equation_tag_search(
        query=query,
        items=all_items,
        top_k=fetch_k,
    )
    if eq_results:
        ranked_lists.append(eq_results)

    # 6. Section title index
    section_results = section_title_search(
        query=query,
        items=all_items,
        top_k=fetch_k,
    )
    if section_results:
        ranked_lists.append(section_results)

    # 7. Figure index only for figure questions
    fig_results = figure_search(
        query=query,
        items=all_items,
        top_k=fetch_k,
    )
    if fig_results:
        ranked_lists.append(fig_results)

    # Remove empty lists
    ranked_lists = [lst for lst in ranked_lists if lst]

    return ranked_lists, all_items
def filter_candidates_by_intent(
    candidates: List[Dict[str, Any]],
    query: str,
    min_keep: int,
) -> List[Dict[str, Any]]:
    intent = detect_query_intent(query)

    for item in candidates:
        item["chunk_type"] = item.get("chunk_type") or detect_chunk_type(item.get("text", ""))
        item["query_intent"] = intent

    if intent == "figure":
        return candidates

    if intent == "equation":
        filtered = [
            item for item in candidates
            if item.get("chunk_type") not in {"figure", "figure_mixed"}
        ]
        return filtered if len(filtered) >= min_keep else candidates

    # method/problem/contribution/general:
    # 过滤纯图表块
    filtered = [
        item for item in candidates
        if item.get("chunk_type") != "figure"
    ]

    return filtered if len(filtered) >= min_keep else candidates


def apply_chunk_type_adjustment(
    candidates: List[Dict[str, Any]],
    query: str,
) -> None:
    intent = detect_query_intent(query)

    for item in candidates:
        chunk_type = item.get("chunk_type") or detect_chunk_type(item.get("text", ""))
        item["chunk_type"] = chunk_type
        item["query_intent"] = intent

        adjust = 0.0

        if intent == "method":
            if chunk_type == "text":
                adjust += 0.10
            elif chunk_type == "equation":
                adjust -= 0.08
            elif chunk_type == "equation_mixed":
                adjust -= 0.03
            elif chunk_type == "figure_mixed":
                adjust -= 0.10
            elif chunk_type == "figure":
                adjust -= 0.35

        elif intent in {"problem", "contribution", "general"}:
            if chunk_type == "text":
                adjust += 0.08
            elif chunk_type in {"figure", "figure_mixed"}:
                adjust -= 0.15

        elif intent == "equation":
            if chunk_type in {"equation", "equation_mixed"}:
                adjust += 0.20
            elif chunk_type == "figure":
                adjust -= 0.25

        elif intent == "figure":
            if chunk_type in {"figure", "figure_mixed"}:
                adjust += 0.20

        item["chunk_type_adjust"] = adjust
        item["score"] = float(item.get("score", 0.0)) + adjust
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
    logger.info(f"需要重新 embedding 的候选 chunk 数量: {len(texts)}")
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

            retrieval_score = float(candidates[idx].get("score", 0.0))
            rrf_score = float(candidates[idx].get("rrf_score", 0.0))
            chunk_type_adjust = float(candidates[idx].get("chunk_type_adjust", 0.0))

            mmr_score = (
                    lambda_mult * relevance
                    - (1.0 - lambda_mult) * diversity_penalty
            )

            mmr_score += 0.20 * retrieval_score
            mmr_score += 0.10 * rrf_score
            mmr_score += 0.40 * chunk_type_adjust

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
    collection, _ = get_chroma_collection(collection_name)
    return collection


def _safe_json_loads(value: Any, default: Any):
    """
    Chroma metadata 中 image_paths/images/headers 是 JSON 字符串。
    这里做安全反序列化。
    """
    return safe_json_loads(value, default)


def _safe_preview(text: str, max_chars: int = 500) -> str:
    return safe_preview(text, max_chars=max_chars)


async def embed_queries(queries: List[str]) -> List[List[float]]:
    """
    批量生成 query / chunk embedding。

    注意：
    DashScope compatible embedding endpoint 限制 input batch size <= 10。
    所以这里必须分批调用，不能一次性传入所有 queries。
    """
    return await embed_texts(queries)


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

    ranked_lists, _ = await hybrid_retrieve_ranked_lists(
        query=query,
        search_queries=search_queries,
        query_embeddings=query_embeddings,
        collection=collection,
        fetch_k=fetch_k,
    )

    if use_rrf:
        candidates = rrf_fuse(ranked_lists, rrf_k=60)
    else:
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

    # 先扩大融合候选池
    pre_filter_limit = max(fused_fetch_k * 3, top_k * 10, 80)
    candidates = candidates[:pre_filter_limit]

    # intent-aware filtering
    candidates = filter_candidates_by_intent(
        candidates=candidates,
        query=query,
        min_keep=max(top_k * 2, 12),
    )

    # chunk type penalty / boost
    apply_chunk_type_adjustment(candidates, query)

    candidates.sort(
        key=lambda x: (
            x.get("score", 0.0),
            x.get("rrf_score", 0.0),
            x.get("rrf_hits", 0),
        ),
        reverse=True,
    )

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

    chat_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


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
            "raw_score": item.get("raw_score", None),
            "distance": item.get("distance", -1),
            "rrf_score": item.get("rrf_score", None),
            "rrf_hits": item.get("rrf_hits", None),
            "mmr_rank": item.get("mmr_rank", None),
            "mmr_score": item.get("mmr_score", None),
            "matched_query": item.get("matched_query", ""),
            "retrieval_source": item.get("retrieval_source", ""),
            "retrieval_sources": item.get("retrieval_sources", []),
            "chunk_type": item.get("chunk_type", ""),
            "query_intent": item.get("query_intent", ""),
            "chunk_type_adjust": item.get("chunk_type_adjust", None),
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
