import logging
import sys
import os
from typing import Dict, Any
from mcp.server.fastmcp import FastMCP
from openai import AsyncOpenAI
from app.rag.embedding_tool import step1_extract_layout, step2_generate_image_captions, \
    step3_merge_context, step4_chunk_and_embed
from app.rag.query_embedding import ask_paper_agent_core, retrieve_paper_context
from app.llm.wrapper import call_llm
import asyncio
PROXY_BYPASS = {"http": None, "https": None}

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
@mcp.tool()
async def run_ingestion_pipeline(pdf_path: str)-> dict[str, str | int | Any]:
    vlm_client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        base_url=os.getenv("OPENAI_BASE_URL") or None
    )

    extracted = await step1_extract_layout(pdf_path)
    captions = await step2_generate_image_captions(extracted["images"], vlm_client)
    merged_text = await step3_merge_context(extracted, captions)

    step4_result = await step4_chunk_and_embed(merged_text, extracted["images"])

    logger.info(
        f"Step 4 结果: chunks={step4_result['chunk_count']}, "
        f"embedded={step4_result['embedded_count']}, "
        f"vector_db={step4_result['vector_db']}, "
        f"chroma={step4_result['chroma']}"
    )

    logger.info("Ingestion Pipeline 执行完成！")
    return {
        "status": "ok",
        "pdf_path": pdf_path,
        "markdown_path": extracted.get("markdown_path", ""),
        "output_dir": extracted.get("output_dir", ""),
        "image_count": len(extracted.get("images", [])),
        "chunk_count": step4_result["chunk_count"],
        "embedded_count": step4_result["embedded_count"],
        "vector_db": step4_result["vector_db"],
        "chroma": step4_result["chroma"],
    }
@mcp.tool()
async def retrieve_paper_context_tool(
    query: str,
    top_k: int = 5,
    collection_name: str = "",
) -> Dict:
    """
    MCP Tool: 只做 query embedding + Chroma 检索，不生成最终回答。
    """
    return await retrieve_paper_context(
        query=query,
        top_k=top_k,
        collection_name=collection_name,
    )


@mcp.tool()
async def ask_paper_agent(
    query: str,
    top_k: int = 5,
    collection_name: str = "",
) -> Dict:
    """
    MCP Tool: 论文 RAG 问答。
    """
    return await ask_paper_agent_core(
        query=query,
        top_k=top_k,
        collection_name=collection_name,
    )
if __name__ == "__main__":
    logger.info("启动 Local RAG MCP Server...")
    mcp.run()
