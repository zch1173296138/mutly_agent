import json
import logging
import os
from pathlib import Path
from typing import Any, List

import chromadb
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_chroma_collection(collection_name: str = "paper_chunks"):
    chroma_dir = project_root() / "storage" / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    return collection, chroma_dir


def safe_json_loads(value: Any, default: Any):
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


def safe_preview(text: str, max_chars: int = 500) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


async def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    batch_size = int(os.getenv("EMBED_BATCH_SIZE", "8"))
    batch_size = max(1, min(batch_size, 10))

    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )

    embeddings: List[List[float]] = []
    for start in range(0, len(texts), batch_size):
        end = start + batch_size
        batch = texts[start:end]

        logger.info(
            "Embedding batch: range=[%s, %s), batch_size=%s, model=%s",
            start,
            end,
            len(batch),
            embedding_model,
        )

        response = await client.embeddings.create(
            model=embedding_model,
            input=batch,
        )
        embeddings.extend([item.embedding for item in response.data])

    return embeddings
