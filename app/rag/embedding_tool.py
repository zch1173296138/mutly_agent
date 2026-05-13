import os
import json
import logging
import base64
import mimetypes
import random
import asyncio
import re
import shutil
import hashlib
import subprocess
from typing import List, Dict, Optional, Any
from pathlib import Path
from urllib.parse import urlparse
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from app.llm.prompt_manager import render
from app.llm.wrapper import call_llm
from app.rag.common import embed_texts, get_chroma_collection, project_root
from dotenv import load_dotenv
load_dotenv()
# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
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

def _get_chroma_collection(collection_name: str = "paper_chunks"):
    """
    获取 Chroma collection。
    数据会持久化到 storage/chroma。
    """
    return get_chroma_collection(collection_name)

def _upsert_chunks_to_chroma(
    chunk_records: List[Dict],
    collection_name: str = "paper_chunks",
) -> Dict:
    """
    将 Step4 生成的 chunk_records 直接写入 Chroma。
    不再生成 chunks_xxx.jsonl 文件。
    """
    collection, chroma_dir = _get_chroma_collection(collection_name)

    valid_records = [
        record for record in chunk_records
        if record.get("embedding") is not None
    ]

    if not valid_records:
        return {
            "status": "empty",
            "collection_name": collection_name,
            "persist_dir": str(chroma_dir),
            "upsert_count": 0,
        }

    ids = []
    documents = []
    embeddings = []
    metadatas = []

    for record in valid_records:
        chunk_id = record.get("chunk_id")

        if not chunk_id:
            continue

        ids.append(chunk_id)
        documents.append(record.get("text", ""))
        embeddings.append(record.get("embedding"))

        # Chroma metadata 建议只放简单类型。
        # list / dict 统一转成 JSON 字符串。
        metadatas.append({
            "chunk_id": chunk_id,
            "chunk_index": int(record.get("chunk_index", -1)),
            "section_index": int(record.get("section_index", -1)),
            "local_index": int(record.get("local_index", -1)),
            "section_title": record.get("section_title", ""),
            "char_count": int(record.get("char_count", 0)),
            "embedding_model": record.get("embedding_model", ""),
            "image_paths": json.dumps(record.get("image_paths", []), ensure_ascii=False),
            "images": json.dumps(record.get("images", []), ensure_ascii=False),
            "headers": json.dumps(record.get("headers", {}), ensure_ascii=False),
        })

    batch_size = int(os.getenv("CHROMA_UPSERT_BATCH_SIZE", "128"))

    for start in range(0, len(ids), batch_size):
        end = start + batch_size

        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            embeddings=embeddings[start:end],
            metadatas=metadatas[start:end],
        )

    return {
        "status": "ok",
        "collection_name": collection_name,
        "persist_dir": str(chroma_dir),
        "upsert_count": len(ids),
    }


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

        root = project_root()
        output_dir = root / "storage" / "parsed" / paper_id

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
            cwd=str(root),
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
    images: List[Dict]
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

        prompt = render(
            "vlm_caption",
            img_id=img.get("id", ""),
            relative_path = img.get("relative_path", "")
        )

        async with semaphore:
            last_error: Optional[Exception] = None

            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(
                        f"Step 2: 正在生成图片描述 "
                        f"({img_id}, attempt={attempt}/{max_retries}): {img_path}"
                    )

                    response = await call_llm(
                        model=model,
                        messages=[{"role": "user","content": [{"type": "text","text": prompt,},{"type": "image_url","image_url": {"url": data_url,},},],}],
                        temperature=0.2,
                        max_tokens=1200,
                    )

                    caption = response.get("content", "")

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

    目标：
    1. 在 MinerU 输出的 Markdown 文本中，找到图片占位符；
    2. 将图片占位符替换为结构化的图像信息块；
    3. 保留原图路径、相对路径、图片 ID、VLM caption；
    4. 如果找不到图片占位符，则把图像信息追加到文末，避免 caption 丢失。

    输入:
      extracted_data:
        {
          "text": markdown 文本,
          "images": [
            {
              "id": "figure_1",
              "path": "...",
              "relative_path": "images/xxx.png",
              "raw_match": "![](images/xxx.png)"
            }
          ],
          "markdown_path": "...",
          "output_dir": "..."
        }

      captions:
        {
          "图片绝对路径": "VLM 生成的 caption"
        }

    输出:
      merged_text: 合并图像描述后的 Markdown 文本
    """
    logger.info("Step 3: 正在组装数据，将图文描述拼接到原始文本流中...")

    text = extracted_data.get("text", "")
    images = extracted_data.get("images", [])

    if not text:
        logger.warning("Step 3: extracted_data 中 text 为空。")
        text = ""

    if not images:
        logger.info("Step 3: 未发现图片，直接返回原始文本。")
        return text

    def _normalize_path_for_markdown(path: str) -> str:
        """
        Markdown 中路径一般使用 /，Windows 路径需要转成 /。
        """
        return path.replace("\\", "/").strip()

    def _escape_regex_path(path: str) -> str:
        """
        对路径做 regex escape，同时兼容 Markdown 中可能出现的空格转义。
        """
        return re.escape(_normalize_path_for_markdown(path))

    def _build_image_context_block(img: Dict, caption: str) -> str:
        """
        构造可被后续 chunk / embedding / RAG 使用的结构化图像上下文块。
        """
        img_id = img.get("id", "")
        img_path = img.get("path", "")
        relative_path = img.get("relative_path", "")
        raw_match = img.get("raw_match", "")

        if not caption:
            caption = "图像描述缺失。"

        block = f"""

【图表信息补充开始】
图表ID: {img_id}
原图路径: {img_path}
相对路径: {relative_path}
原始引用: {raw_match}

图表描述:
{caption}
【图表信息补充结束】

"""
        return block

    def _replace_first(text_value: str, pattern: str, replacement: str) -> tuple[str, bool]:
        """
        使用正则只替换第一个匹配项。

        注意：
        replacement 里可能包含 Windows 路径，例如 E:\\python\\xxx。
        如果直接传给 re.subn，反斜杠会被当成正则替换模板解析，
        可能触发 bad escape \\p 之类的错误。
        所以这里用 lambda 返回原始 replacement 字符串。
        """
        new_text, count = re.subn(
            pattern,
            lambda _: replacement,
            text_value,
            count=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        return new_text, count > 0

    unmatched_blocks = []

    for img in images:
        img_id = img.get("id", "")
        img_path = img.get("path", "")
        relative_path = _normalize_path_for_markdown(img.get("relative_path", ""))
        raw_match = img.get("raw_match", "")

        caption = captions.get(img_path, "")
        merge_block = _build_image_context_block(img, caption)

        replaced = False

        # 1. 优先使用 Step 1 保存的 raw_match 精确替换
        if raw_match and raw_match in text:
            text = text.replace(raw_match, merge_block, 1)
            replaced = True
            logger.info(f"Step 3: 已通过 raw_match 替换图片占位符: {img_id}")

        # 2. 兼容 Markdown 图片语法：![](relative_path) / ![xxx](relative_path)
        if not replaced and relative_path:
            escaped_rel = _escape_regex_path(relative_path)

            markdown_img_pattern = rf"!\[[^\]]*\]\(\s*{escaped_rel}\s*\)"
            text, replaced = _replace_first(text, markdown_img_pattern, merge_block)

            if replaced:
                logger.info(f"Step 3: 已通过 Markdown 图片语法替换: {img_id}")

        # 3. 兼容 Markdown 图片路径带 ./ 前缀
        if not replaced and relative_path:
            escaped_rel = _escape_regex_path(relative_path)

            markdown_img_pattern_with_dot = rf"!\[[^\]]*\]\(\s*\./{escaped_rel}\s*\)"
            text, replaced = _replace_first(text, markdown_img_pattern_with_dot, merge_block)

            if replaced:
                logger.info(f"Step 3: 已通过 ./ Markdown 图片语法替换: {img_id}")

        # 4. 兼容 HTML img 标签：<img src="relative_path">
        if not replaced and relative_path:
            escaped_rel = _escape_regex_path(relative_path)

            html_img_pattern = (
                rf"<img[^>]+src=[\"']\s*(?:\./)?{escaped_rel}\s*[\"'][^>]*>"
            )
            text, replaced = _replace_first(text, html_img_pattern, merge_block)

            if replaced:
                logger.info(f"Step 3: 已通过 HTML img 标签替换: {img_id}")

        # 5. 兼容只出现文件名的情况
        if not replaced and relative_path:
            filename = Path(relative_path).name
            escaped_filename = re.escape(filename)

            filename_markdown_pattern = rf"!\[[^\]]*\]\(\s*[^)]*{escaped_filename}\s*\)"
            text, replaced = _replace_first(text, filename_markdown_pattern, merge_block)

            if replaced:
                logger.info(f"Step 3: 已通过文件名模糊匹配替换: {img_id}")

        # 6. 如果完全找不到占位符，不丢弃，最后追加到文末
        if not replaced:
            logger.warning(
                f"Step 3: 未在 Markdown 中找到图片占位符，将追加到文末: "
                f"id={img_id}, relative_path={relative_path}, path={img_path}"
            )
            unmatched_blocks.append(merge_block)

    # if unmatched_blocks:
    #     text += "\n\n# 未匹配到原始位置的图表信息\n"
    #     text += "\n".join(unmatched_blocks)

    logger.info(
        f"Step 3 完成: images={len(images)}, unmatched={len(unmatched_blocks)}, "
        f"merged_text_length={len(text)}"
    )

    return text

async def step4_chunk_and_embed(merged_text: str, processed_images: List[Dict]) -> Dict:
    """
    Step 4: 切片 (Chunking) 与向量化入库

    目标：
    1. 按 Markdown 标题结构切分文本；
    2. 再按 chunk_size / chunk_overlap 做二次切分；
    3. 尽量保证图表描述块与附近正文处于同一个 chunk；
    4. 为每个 chunk 提取相关 image_paths；
    5. 调用 embedding 模型生成向量；
    6. 将 chunks + embeddings 保存为 JSONL，后续可接入 FAISS / Chroma / Milvus / PostgreSQL pgvector。

    返回:
      {
        "chunks": [...],
        "jsonl_path": "...",
        "chunk_count": 10,
        "embedded_count": 10
      }

    环境变量:
      OPENAI_API_KEY
      OPENAI_BASE_URL
      OPENAI_EMBEDDING_MODEL，默认 text-embedding-3-small
      CHUNK_SIZE，默认 1800
      CHUNK_OVERLAP，默认 250
    """
    logger.info("Step 4: 正在进行文本切片 (Chunking) 并执行向量化入库...")

    if not merged_text or not merged_text.strip():
        logger.warning("Step 4: merged_text 为空，跳过切片和向量化。")
        return {
            "chunks": [],
            "jsonl_path": "",
            "chunk_count": 0,
            "embedded_count": 0,
        }

    chunk_size = int(os.getenv("CHUNK_SIZE", "1800"))
    chunk_overlap = int(os.getenv("CHUNK_OVERLAP", "250"))
    embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")



    def _stable_chunk_id(text: str, index: int) -> str:
        raw = f"{index}-{text}".encode("utf-8", errors="ignore")
        return hashlib.sha256(raw).hexdigest()[:16]

    def _normalize_path(path: str) -> str:
        return path.replace("\\", "/").strip()

    def _extract_image_refs(chunk_text: str, images: List[Dict]) -> List[Dict]:
        """
        根据 chunk 文本内容，提取该 chunk 中涉及的图片。

        Step 3 的 merge block 中通常包含：
        - 图表ID
        - 原图路径
        - 相对路径
        - 原始引用

        所以这里同时匹配 path / relative_path / id。
        """
        refs = []

        normalized_chunk = _normalize_path(chunk_text)

        for img in images:
            img_id = img.get("id", "")
            img_path = img.get("path", "")
            relative_path = img.get("relative_path", "")

            candidates = [
                img_id,
                img_path,
                _normalize_path(img_path),
                relative_path,
                _normalize_path(relative_path),
            ]

            matched = any(c and c in normalized_chunk for c in candidates)

            if matched:
                refs.append({
                    "id": img_id,
                    "path": img_path,
                    "relative_path": relative_path,
                })

        return refs

    def _extract_section_title(metadata: Dict) -> str:
        """
        从 MarkdownHeaderTextSplitter 的 metadata 中恢复章节路径。
        """
        titles = []

        for key in ["Header 1", "Header 2", "Header 3", "Header 4"]:
            value = metadata.get(key)
            if value:
                titles.append(value)

        return " / ".join(titles)

    def _protect_image_blocks(text: str) -> str:
        """
        给图表信息块前后增加明确分隔符，让 RecursiveCharacterTextSplitter
        更倾向于把它作为独立语义块处理。
        """
        text = text.replace(
            "【图表信息补充开始】",
            "\n\n---\n\n【图表信息补充开始】"
        )
        text = text.replace(
            "【图表信息补充结束】",
            "【图表信息补充结束】\n\n---\n\n"
        )
        return text

    def _protect_equation_blocks(text: str) -> str:
        """
        将 LaTeX 公式包装成不可切分块，避免 RecursiveCharacterTextSplitter
        把公式中间切断。

        支持：
        1. \\begin{equation} ... \\end{equation}
        2. \\begin{align} ... \\end{align}
        3. $$ ... $$
        4. \\[ ... \\]
        5. 独立成行的 LaTeX 公式
        """
        if not text:
            return text

        protected_blocks = []

        def _stash(match: re.Match) -> str:
            raw = match.group(0).strip()
            if not raw:
                return match.group(0)

            idx = len(protected_blocks)
            protected_blocks.append(raw)
            return f"\n\n@@EQUATION_BLOCK_{idx}@@\n\n"

        patterns = [
            (
                r"\\begin\{(?:equation|equation\*|align|align\*|aligned|gather|gather\*|multline|multline\*)\}"
                r".*?"
                r"\\end\{(?:equation|equation\*|align|align\*|aligned|gather|gather\*|multline|multline\*)\}",
                re.DOTALL,
            ),
            (
                r"\$\$.*?\$\$",
                re.DOTALL,
            ),
            (
                r"\\\[.*?\\\]",
                re.DOTALL,
            ),
            (
                r"(?m)^[ \t]*"
                r"(?=.{8,800}$)"
                r"(?=.*(?:=|\\tag|\\frac|\\sum|\\left|\\right))"
                r"(?=.*\\(?:tag|frac|left|right|overline|underline|alpha|beta|gamma|theta|pi|lfloor|rfloor|sum|prod|sqrt|int|bar))"
                r"[^\n]+$",
                0,
            ),
        ]

        for pattern, flags in patterns:
            text = re.sub(pattern, _stash, text, flags=flags)

        for idx, raw in enumerate(protected_blocks):
            block = f"""
    ---
    【公式信息开始】
    LaTeX:
    {raw}
    【公式信息结束】
    ---
    """.strip()

            text = text.replace(f"@@EQUATION_BLOCK_{idx}@@", block)

        logger.info(f"Step 4: 检测并保护公式块数量={len(protected_blocks)}")

        return text

    def _split_preserve_image_blocks(text: str) -> List[str]:
        """
        将文本切成普通文本块 + 图表信息块。
        图表信息块作为不可切分原子单元保留。
        """
        pattern = (
            r"【图表信息补充开始】.*?【图表信息补充结束】"
            r"|"
            r"【公式信息开始】.*?【公式信息结束】"
        )
        parts = []
        last_end = 0

        for match in re.finditer(pattern, text, flags=re.DOTALL):
            before = text[last_end:match.start()]
            image_block = match.group(0)

            if before.strip():
                parts.extend(_split_plain_text(before))

            if image_block.strip():
                parts.append(image_block.strip())

            last_end = match.end()

        tail = text[last_end:]
        if tail.strip():
            parts.extend(_split_plain_text(tail))

        return parts

    def _split_plain_text(text: str) -> List[str]:
        """
        只切普通文本，不处理图表块。
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=[
                "\n\n",
                "\n",
                "。",
                "；",
                "，",
                " ",
                "",
            ],
        )
        return [x.strip() for x in splitter.split_text(text) if x.strip()]

    def _pack_units_to_chunks(units: List[str]) -> List[str]:
        """
        将普通文本单元和特殊原子块组合成 chunk。

        公式块 / 图表块不会被切断。
        如果单个特殊块超过 chunk_size，则允许该 chunk 超长。
        """
        chunks = []
        current = ""

        def _is_special_block(unit: str) -> bool:
            return (
                    (
                            "【图表信息补充开始】" in unit
                            and "【图表信息补充结束】" in unit
                    )
                    or (
                            "【公式信息开始】" in unit
                            and "【公式信息结束】" in unit
                    )
            )

        for unit in units:
            unit = unit.strip()
            if not unit:
                continue

            is_special_block = _is_special_block(unit)

            if not current:
                current = unit
                continue

            candidate = current + "\n\n" + unit

            if len(candidate) <= chunk_size:
                current = candidate
            else:
                chunks.append(current.strip())

                # 特殊块即使超过 chunk_size，也单独保留，不切断
                current = unit

        if current.strip():
            chunks.append(current.strip())

        return chunks

    protected_text = _protect_equation_blocks(merged_text)
    protected_text = _protect_image_blocks(protected_text)

    # 1. 先按 Markdown 标题切分，保留章节信息
    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
        ("####", "Header 4"),
    ]

    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,
    )

    try:
        section_docs = markdown_splitter.split_text(protected_text)
    except Exception as e:
        logger.warning(f"Step 4: Markdown 标题切分失败，退化为全文切分: {e}")
        section_docs = []

    if not section_docs:
        section_docs = [{
            "page_content": protected_text,
            "metadata": {},
        }]

    # 兼容 LangChain Document 和 dict
    normalized_docs = []
    for doc in section_docs:
        if isinstance(doc, dict):
            normalized_docs.append(doc)
        else:
            normalized_docs.append({
                "page_content": doc.page_content,
                "metadata": doc.metadata,
            })

    # 2. 再按长度切分

    chunk_records = []

    for section_index, doc in enumerate(normalized_docs):
        page_content = doc.get("page_content", "")
        section_metadata = doc.get("metadata", {})

        if not page_content.strip():
            continue

        units = _split_preserve_image_blocks(page_content)
        split_texts = _pack_units_to_chunks(units)

        section_title = _extract_section_title(section_metadata)

        for local_index, chunk_text in enumerate(split_texts):
            chunk_text = chunk_text.strip()

            if not chunk_text:
                continue

            image_refs = _extract_image_refs(chunk_text, processed_images)

            global_index = len(chunk_records)
            chunk_id = _stable_chunk_id(chunk_text, global_index)

            record = {
                "chunk_id": chunk_id,
                "chunk_index": global_index,
                "section_index": section_index,
                "local_index": local_index,
                "section_title": section_title,
                "headers": section_metadata,
                "text": chunk_text,
                "char_count": len(chunk_text),
                "image_paths": [item["path"] for item in image_refs],
                "images": image_refs,
                "embedding_model": embedding_model,
                "embedding": None,
            }

            chunk_records.append(record)

    if not chunk_records:
        logger.warning("Step 4: 没有生成任何 chunk。")
        return {
            "chunks": [],
            "vector_db": "chroma",
            "chroma": {},
            "chunk_count": 0,
            "embedded_count": 0,
        }

    logger.info(f"Step 4: 切片完成，共生成 {len(chunk_records)} 个 chunk。")

    # 3. 调用 embedding API
    batch_size = int(os.getenv("EMBED_BATCH_SIZE", "8"))
    batch_size = max(1, min(batch_size, 10))
    embedded_count = 0

    try:
        for start in range(0, len(chunk_records), batch_size):
            end = start + batch_size
            batch = chunk_records[start:end]
            batch_texts = [item["text"] for item in batch]

            logger.info(
                f"Step 4: 正在向量化 batch {start // batch_size + 1}, "
                f"range=[{start}, {min(end, len(chunk_records))})"
            )

            embeddings = await embed_texts(batch_texts)

            for record, embedding in zip(batch, embeddings):
                record["embedding"] = embedding
                embedded_count += 1

    except Exception as e:
        logger.exception(
            "Step 4: embedding 调用失败。将保存未向量化 chunk，"
            "你可以后续重新执行 embedding。"
        )

    # 4. 直接写入 Chroma
    chroma_result = _upsert_chunks_to_chroma(
        chunk_records=chunk_records,
        collection_name=os.getenv("CHROMA_COLLECTION", "paper_chunks"),
    )

    logger.info(f"Chroma 入库结果: {json.dumps(chroma_result, ensure_ascii=False)}")

    # 5. 打印预览
    for record in chunk_records[:3]:
        preview = record["text"][:300].replace("\n", " ")
        logger.info(
            "Chunk 预览: "
            f"chunk_id={record['chunk_id']}, "
            f"section={record['section_title']}, "
            f"images={record['image_paths']}, "
            f"preview={preview}"
        )

    logger.info(
        f"Step 4 完成: chunk_count={len(chunk_records)}, "
        f"embedded_count={embedded_count}"
    )

    return {
        "chunks": chunk_records,
        "vector_db": "chroma",
        "chroma": chroma_result,
        "chunk_count": len(chunk_records),
        "embedded_count": embedded_count,
    }
