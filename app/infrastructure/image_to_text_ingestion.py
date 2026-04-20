import os
import json
import logging
import base64
import mimetypes
import random
import asyncio
import shutil
import subprocess
from typing import List, Dict, Optional
from openai import AsyncOpenAI
from pathlib import Path
from urllib.parse import urlparse
from mcp.server.fastmcp import FastMCP
from langchain_text_splitters import MarkdownHeaderTextSplitter

from dotenv import load_dotenv
load_dotenv()
# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
mcp = FastMCP("Local RAG Tool")
MINERU_API_KEY = os.getenv("MINERU_API_KEY", "").strip()

def _image_file_to_data_url(image_path: str) -> str:
    """
    将本地图片文件转换为 OpenAI-compatible VLM 可读的 data URL。
    """
    path = Path(image_path)

    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {image_path}")

    mime_type, _ = mimetypes.guess_type(str(path))
    if not mime_type:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime_type = "image/jpeg"
        elif suffix == ".png":
            mime_type = "image/png"
        elif suffix == ".webp":
            mime_type = "image/webp"
        else:
            mime_type = "application/octet-stream"

    with path.open("rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def _build_vlm_prompt(img: Dict) -> str:
    """
    为论文图像生成更稳定的 caption prompt。
    """
    img_id = img.get("id", "")
    relative_path = img.get("relative_path", "")

    return f"""
你是一个专业的学术论文图表理解助手，擅长计算机图形学、计算几何、CAD/B-rep、深度学习模型结构和实验图表分析。

请你根据输入图片生成一段高质量中文图表描述，用于论文 RAG 检索增强。

图片信息：
- image_id: {img_id}
- relative_path: {relative_path}

请严格遵守以下要求：

1. 不要泛泛描述“这是一张图片”，要尽量描述图中真实可见的信息。
2. 如果是模型架构图：
   - 说明输入、输出、模块名称、数据流向；
   - 描述 Transformer、CNN、MLP、attention、encoder、decoder、feature extractor 等模块关系；
   - 保留图中的英文模块名。
3. 如果是 CAD、B-rep、mesh 或几何结构图：
   - 描述点、边、面、环、拓扑连接关系；
   - 描述颜色、高亮区域、特征面、边界、角点、patch、face adjacency 等信息；
   - 如果图中有多个子图，请按从左到右、从上到下描述。
4. 如果是表格截图：
   - 总结表格主题；
   - 尽量提取行列含义、指标名称、对比对象和主要结论。
5. 如果是实验结果图：
   - 描述横纵轴、曲线/柱状图含义、趋势和对比结论。
6. 如果图片信息不足，请明确说明“图中可见信息有限”，不要编造不存在的模块或数值。
7. 输出格式固定为：

【图像类型】
一句话判断图片类型。

【详细描述】
详细描述图中内容。

【可用于检索的关键词】
列出 5-15 个关键词，用中文或英文均可，保留重要英文术语。
""".strip()




async def step1_extract_layout(pdf_path: str) -> Dict:
    """
    Step 1: 智能文档解析 Layout Analysis & Extraction

    使用 MinerU Open API CLI:
      mineru-open-api extract <pdf_path_or_url> -o <output_dir>

    返回:
      {
        "text": markdown文本,
        "images": [{"id", "path", "relative_path", "raw_match"}],
        "markdown_path": "...",
        "output_dir": "..."
      }
    """
    logger.info(f"Step 1: 使用 mineru-open-api 解析 PDF: {pdf_path}")

    def _is_url(path: str) -> bool:
        return path.startswith("http://") or path.startswith("https://")

    def _paper_id(path: str) -> str:
        if _is_url(path):
            return Path(urlparse(path).path).stem or "paper"
        return Path(path).stem or "paper"

    def _sync_extract() -> Dict:
        paper_id = _paper_id(pdf_path)

        project_root = Path(__file__).resolve().parents[2]
        output_dir = project_root / "storage" / "parsed" / paper_id

        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        mineru_cmd = os.getenv("MINERU_OPEN_API_CMD", "mineru-open-api")

        cmd = [
            mineru_cmd,
            "extract",
            pdf_path,
            "-o",
            str(output_dir),
        ]
        if MINERU_API_KEY:
            cmd.extend(["--token", MINERU_API_KEY])


        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )

        if proc.returncode != 0:
            raise RuntimeError(
                "mineru-open-api 解析失败\n"
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}"
            )

        md_files = list(output_dir.rglob("*.md"))
        if not md_files:
            raise FileNotFoundError(f"没有找到 Markdown 输出: {output_dir}")
        md_path = sorted(md_files, key=lambda p: len(str(p)))[0]

        text = md_path.read_text(encoding="utf-8", errors="ignore")

        image_exts = {".png", ".jpg", ".jpeg", ".webp"}
        images = []

        for img_path in sorted(output_dir.rglob("*")):
            if not img_path.is_file():
                continue
            if img_path.suffix.lower() not in image_exts:
                continue

            relative_path = img_path.relative_to(output_dir).as_posix()
            images.append({
                "id": f"figure_{len(images) + 1}",
                "path": str(img_path),
                "relative_path": relative_path,
                "raw_match": f"![]({relative_path})",
            })

        logger.info(
            f"Step 1 完成: markdown={md_path}, images={len(images)}, output_dir={output_dir}"
        )

        return {
            "text": text,
            "images": images,
            "markdown_path": str(md_path),
            "output_dir": str(output_dir),
        }

    return await asyncio.to_thread(_sync_extract)

async def step2_generate_image_captions(
    images: List[Dict],
    vlm_client: AsyncOpenAI
) -> Dict[str, str]:
    """
    Step 2: 视觉大模型 (VLM) 描述生成 (Captioning)

    输入:
      images: step1_extract_layout 返回的 images 列表
      vlm_client: AsyncOpenAI 客户端，兼容 OpenAI / Qwen / OneAPI / vLLM 等 OpenAI API 格式服务

    输出:
      {
        "图片绝对路径": "VLM 生成的 caption"
      }

    环境变量:
      OPENAI_VLM_MODEL: 可选，默认 gpt-4o-mini
      VLM_CONCURRENCY: 可选，并发数，默认 3
      VLM_MAX_RETRIES: 可选，失败重试次数，默认 3
    """
    logger.info("Step 2: 正在调用 VLM 为提取的插图生成 Caption...")

    if not images:
        logger.info("Step 2: 未发现图片，跳过 Caption 生成。")
        return {}

    model = os.getenv("OPENAI_VLM_MODEL", "gpt-4o-mini")
    concurrency = int(os.getenv("VLM_CONCURRENCY", "3"))
    max_retries = int(os.getenv("VLM_MAX_RETRIES", "3"))

    semaphore = asyncio.Semaphore(concurrency)
    captions: Dict[str, str] = {}

    async def _caption_one(img: Dict) -> tuple[str, str]:
        img_path = img.get("path", "")
        img_id = img.get("id", img_path)

        if not img_path:
            logger.warning(f"Step 2: 图片缺少 path 字段，跳过: {img}")
            return "", ""

        try:
            data_url = await asyncio.to_thread(_image_file_to_data_url, img_path)
        except Exception as e:
            logger.exception(f"Step 2: 图片读取失败: {img_path}")
            return img_path, f"【图像描述生成失败】图片读取失败: {e}"

        prompt = _build_vlm_prompt(img)

        async with semaphore:
            last_error: Optional[Exception] = None

            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(
                        f"Step 2: 正在生成图片描述 "
                        f"({img_id}, attempt={attempt}/{max_retries}): {img_path}"
                    )

                    response = await vlm_client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user","content": [{"type": "text","text": prompt,},{"type": "image_url","image_url": {"url": data_url,},},],}],
                        temperature=0.2,
                        max_tokens=1200,
                    )

                    caption = response.choices[0].message.content

                    if not caption:
                        raise RuntimeError("VLM 返回空 caption")

                    caption = caption.strip()

                    logger.info(f"Step 2: 图片描述生成成功: {img_path}")
                    return img_path, caption

                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"Step 2: 图片描述生成失败 "
                        f"({img_id}, attempt={attempt}/{max_retries}): {e}"
                    )

                    if attempt < max_retries:
                        sleep_seconds = min(2 ** attempt + random.random(), 8)
                        await asyncio.sleep(sleep_seconds)

            logger.exception(f"Step 2: 图片最终生成失败: {img_path}")
            return img_path, f"【图像描述生成失败】{type(last_error).__name__}: {last_error}"

    tasks = [_caption_one(img) for img in images]
    results = await asyncio.gather(*tasks)

    for img_path, caption in results:
        if img_path:
            captions[img_path] = caption

    logger.info(f"Step 2 完成: 共处理图片 {len(images)} 张，成功返回 {len(captions)} 条 caption")
    return captions

async def step3_merge_context(extracted_data: Dict, captions: Dict[str, str]) -> str:
    """
    Step 3: 数据组装与上下文对齐 (Context Merging)
    在原文文本流中，将图片占位符替换为包含 VLM 描述的格式化文本块。
    """
    logger.info("Step 3: 正在组装数据，将图文描述拼接到原始文本流中...")
    text = extracted_data["text"]
    
    for img in extracted_data["images"]:
        img_id = img["id"]
        img_path = img["path"]
        caption = captions.get(img_path, "")
        
        # 生成特殊的标记描述块
        merge_block = (
            "\n【图表信息补充开始】\n"
            f"[原图路径: {img_path}]\n"
            f"[图表描述: {caption}]\n"
            "【图表信息补充结束】\n"
        )
        
        # 优先使用在 step1 中完整匹配的图片占位字符串进行替换，兼容旧版占位符
        img_placeholder = img.get("raw_match", f"<img src='{img_id}'>")
        text = text.replace(img_placeholder, merge_block)
        
    return text

async def step4_chunk_and_embed(merged_text: str, processed_images: List[Dict]):
    """
    Step 4: 切片 (Chunking) 与向量化入库
    确保图表描述与上下文在同一 Chunk，并在 Metadata 中挂载图片路径以便前端展示原图。
    """
    logger.info("Step 4: 正在进行文本切片 (Chunking) 并执行向量化入库...")
    
    # 模拟 Chunking策略：确保图表描述与其前后正文在同一个Chunk中
    chunks = [merged_text] 
    
    for i, chunk in enumerate(chunks):
        # 高阶技巧：Metadata 挂载
        # 将被切入此 Chunk 的相关图片路径提取出来保存，在检索召回给用户时能顺带返回图片
        metadata = {
            "chunk_id": i,
            "source": "paper_x.pdf",
            "image_paths": [img["path"] for img in processed_images if img["path"] in chunk]
        }
        
        logger.info(f"入库 Chunk Metadata: {json.dumps(metadata, ensure_ascii=False)}")
        logger.info(f"入库 Chunk 文本预览:\n{chunk}")

@mcp.tool()
async def run_ingestion_pipeline(pdf_path: str):
    vlm_client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "dummy"), 
        base_url=os.getenv("OPENAI_BASE_URL", None)
    )
    
    # 执行流水线 4 步走
    extracted = await step1_extract_layout(pdf_path)
    captions = await step2_generate_image_captions(extracted["images"], vlm_client)
    merged_text = await step3_merge_context(extracted, captions)
    await step4_chunk_and_embed(merged_text, extracted["images"])
    
    logger.info("Ingestion Pipeline 执行完成！")

if __name__ == "__main__":
    logger.info("启动 Local RAG MCP Server...")
    mcp.run()