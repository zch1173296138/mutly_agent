import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any

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


async def embed_query(query: str) -> List[float]:
    """
    对用户 query 做 embedding。
    必须和 step4_chunk_and_embed 使用同一个 embedding model。
    """
    embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )

    response = await client.embeddings.create(
        model=embedding_model,
        input=[query],
    )

    return response.data[0].embedding


async def retrieve_paper_context(
    query: str,
    top_k: int = 5,
    collection_name: str = "",
) -> Dict[str, Any]:
    """
    Query Embedding + Chroma 检索。

    输入:
      query: 用户问题
      top_k: 返回前几个 chunk
      collection_name: Chroma collection 名称，默认读 env CHROMA_COLLECTION

    输出:
      {
        "query": "...",
        "collection_name": "paper_chunks",
        "results": [...]
      }
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
        f"开始 Chroma 检索论文上下文: "
        f"query={query}, top_k={top_k}, collection_name={collection_name}"
    )

    query_embedding = await embed_query(query)
    collection = _get_chroma_collection(collection_name)

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    results: List[Dict[str, Any]] = []

    for i in range(len(ids)):
        metadata = metadatas[i] or {}
        distance = distances[i]

        image_paths = _safe_json_loads(metadata.get("image_paths"), [])
        images = _safe_json_loads(metadata.get("images"), [])
        headers = _safe_json_loads(metadata.get("headers"), {})

        # hnsw:space=cosine 时，distance 越小越相关
        # 这里转成一个粗略 score，方便展示
        score = 1.0 - float(distance)

        results.append({
            "score": score,
            "distance": float(distance),
            "chunk_id": metadata.get("chunk_id", ids[i]),
            "chunk_index": metadata.get("chunk_index", -1),
            "section_title": metadata.get("section_title", ""),
            "headers": headers,
            "text": documents[i],
            "image_paths": image_paths,
            "images": images,
            "char_count": metadata.get("char_count", 0),
        })

    logger.info(f"Chroma 检索完成: hit={len(results)}")

    return {
        "query": query,
        "collection_name": collection_name,
        "top_k": top_k,
        "results": results,
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

    if not answer:
        return "模型没有返回有效回答。"

    return answer.strip()


async def ask_paper_agent_core(
    query: str,
    top_k: int = 5,
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
            collection_name=collection_name,
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