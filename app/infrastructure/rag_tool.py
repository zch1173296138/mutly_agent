import logging
import sys
import os
import httpx
from typing import List, Dict
from mcp.server.fastmcp import FastMCP
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer
import asyncio
# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# 创建 MCP Server 实例
mcp = FastMCP("Local RAG Tool")

logger.info("初始化 Local RAG MCP Server...")

# 全局的 OpenAI 客户端（用于大模型及 Embedding），可通过环境变量指向私有化部署接口
_openai_client = None

def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY", "dummy_key"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
    return _openai_client


async def _embed_query(query: str) -> List[float]:
    """使用 Hugging Face 模型将用户问题向量化"""
    


async def _vector_search(query_vector: List[float], top_k: int = 10) -> List[Dict]:
    """从向量数据库召回 Top-K 个文献 Chunk"""
    vector_db_url = os.getenv("VECTOR_DB_URL")
    if not vector_db_url:
        logger.warning("未配置 VECTOR_DB_URL，返回内部模拟文档。")
        # 降级：返回 Mock 本地文档数据，供无库情况下的测试
        return [
            {
                "content": "这是关于公司 Q3 内部核心产品的说明文档，涵盖性能提升与 AI Agent 整合的技术路线。",
                "metadata": {"source": "product_q3_roadmap.pdf", "page": 1}
            },
            {
                "content": "《大模型 RAG 架构设计规范》：详细规定了从 Embedding、Recall 到 Rerank 应该如何应对超大规模的数据检索，重排阶段使用 BGE 模型能够带来更好的区分度。",
                "metadata": {"source": "rag_design_guide.md", "page": 3}
            },
            {
                "content": "员工入职指南中指出：在使用内部知识库时，需保证权限合规，私有化部署的 LLM 能够有效避免数据泄露的风险。",
                "metadata": {"source": "onboarding_2026.docx", "page": 5}
            }
        ]
        
    try:
        # 模拟请求标准向量数据库（例如 Qdrant 的 REST API）
        async with httpx.AsyncClient() as client:
            payload = {"vector": query_vector, "limit": top_k, "with_payload": True}
            resp = await client.post(f"{vector_db_url}/collections/knowledge/points/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("result", []):
                results.append({
                    "content": item.get("payload", {}).get("content", ""),
                    "metadata": item.get("payload", {}).get("metadata", {})
                })
            return results
    except Exception as e:
        logger.error(f"向量召回遇到网络/请求错误: {e}")
        return []


async def _rerank_documents(query: str, docs: List[Dict], top_n: int = 3) -> List[Dict]:
    """使用 BGE-Reranker 等重排模型，对文献进行二次精确打分，保留前 3"""
    if not docs:
        return []
        
    rerank_api_url = os.getenv("RERANK_API_URL")
    if not rerank_api_url:
        logger.warning("未配置 RERANK_API_URL，跳过重排提升阶段。")
        return docs[:top_n]
        
    try:
        rerank_api_key = os.getenv("RERANK_API_KEY", "")
        headers = {
            "Authorization": f"Bearer {rerank_api_key}",
            "Content-Type": "application/json"
        }
        # 通用 Reranker 接口 (兼容 Cohere / SiliconFlow 等主流推断平台规范)
        payload = {
            "model": os.getenv("RERANK_MODEL", "bge-reranker-v2-m3"),
            "query": query,
            "texts": [d.get("content", "") for d in docs]
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(rerank_api_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            # 预期的结果为包含 index 和 relevance_score 的数组
            results = data.get("results", [])
            results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
            
            final_docs = []
            for res in results[:top_n]:
                idx = res.get("index")
                if idx is not None and 0 <= idx < len(docs):
                    final_docs.append(docs[idx])
            return final_docs
    except Exception as e:
        logger.error(f"文档重排失败，采用降级策略: {e}")
        return docs[:top_n]


@mcp.tool()
async def execute_local_rag(query: str) -> str:
    """
    RAG 工具：基于检索增强生成，从本地文献/知识库中查询并增强信息。
    当用户询问关于本地存储的专有数据、私有文档或特定业务文档内容时使用此工具。
    
    Args:
        query: 用户的搜索词或具体问题。
    """
    logger.debug(f"执行 RAG 工具, query={query}")
    try:
        # 1. 向量化
        query_vec = await _embed_query(query)

        # 2. 粗排召回 (Recall)
        retrieved_docs = await _vector_search(query_vec, top_k=10)

        if not retrieved_docs:
            return "本地文献库中未找到相关内容，请尝试修改搜索词或使用 tavily_search。"

        # 3. 精排 (Rerank)
        final_docs = await _rerank_documents(query, retrieved_docs, top_n=3)

        # 4. 组装上下文返回给 Worker
        context_parts = []
        for i, doc in enumerate(final_docs):
            source = doc.get("metadata", {}).get("source", "未知文献")
            page = doc.get("metadata", {}).get("page", "未知")
            content = doc.get("content", "")
            context_parts.append(f"[来源 {i + 1}: {source} 第{page}页]\n{content}\n")

        return "\n".join(context_parts)

    except Exception as e:
        logger.error(f"RAG 工具执行失败: {e}")
        return f"检索本地数据库时发生错误: {str(e)}"

if __name__ == "__main__":
    logger.info("启动 Local RAG MCP Server...")
    mcp.run()
