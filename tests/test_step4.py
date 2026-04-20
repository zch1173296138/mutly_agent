import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.infrastructure import image_to_text_ingestion


class FakeEmbeddingClient:
    """
    Mock AsyncOpenAI(...).embeddings.create(...)
    避免测试时真实调用 OpenAI / OneAPI / Qwen embedding 服务。
    """

    def __init__(self, *args, **kwargs):
        self.embeddings = self

    async def create(self, model: str, input: list[str]):
        fake_data = []

        for text in input:
            # 生成一个固定长度的假向量
            # 向量内容无所谓，只要结构像 OpenAI embedding 返回即可
            fake_data.append(
                SimpleNamespace(
                    embedding=[0.1, 0.2, 0.3, float(len(text) % 10)]
                )
            )

        return SimpleNamespace(data=fake_data)


@pytest.mark.asyncio
async def test_step4_chunk_and_embed_basic(monkeypatch):
    """
    测试 Step 4:
    1. 能生成 chunk；
    2. 能生成 embedding；
    3. 能识别 chunk 中关联的 image_paths；
    4. 能保存 jsonl 文件。
    """

    # 1. Mock AsyncOpenAI，避免真实网络请求
    monkeypatch.setattr(
        image_to_text_ingestion,
        "AsyncOpenAI",
        FakeEmbeddingClient,
    )

    # 2. 控制 chunk 参数，让测试结果稳定
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "fake-embedding-model")
    monkeypatch.setenv("CHUNK_SIZE", "200")
    monkeypatch.setenv("CHUNK_OVERLAP", "50")
    monkeypatch.setenv("EMBED_BATCH_SIZE", "2")

    # 3. 构造 Step 3 之后的 merged_text
    image_path = r"C:\project\storage\parsed\paper_x\images\figure_1.png"
    relative_path = "images/figure_1.png"

    merged_text = f"""
# 3 Method

## 3.1 Overview

本文提出了一种用于 CAD B-rep 几何特征识别的方法。模型首先从几何拓扑结构中提取面、边、点等局部特征，然后通过 Transformer 结构建模全局关系。

【图表信息补充开始】
图表ID: figure_1
原图路径: {image_path}
相对路径: {relative_path}
原始引用: ![]({relative_path})

图表描述:
【图像类型】
二维带圆孔几何区域的结构化四边形网格示意图。

【详细描述】
图中展示了一个顶部圆弧封闭的长条形二维区域，上部中心存在一个圆形孔洞。整个区域由结构化四边形单元组成，孔洞附近的网格线沿圆形边界弯曲，形成 boundary-conforming quad mesh。

【可用于检索的关键词】
structured quadrilateral mesh, quad mesh, circular hole, boundary-conforming mesh, mapped mesh, O-grid topology
【图表信息补充结束】

## 3.2 Feature Encoder

特征编码器将每个 B-rep 面片的几何属性、拓扑邻接关系和局部 UV 域信息编码为高维 token 表示。
""".strip()

    processed_images = [
        {
            "id": "figure_1",
            "path": image_path,
            "relative_path": relative_path,
            "raw_match": f"![]({relative_path})",
        }
    ]

    # 4. 执行 Step 4
    result = await image_to_text_ingestion.step4_chunk_and_embed(
        merged_text=merged_text,
        processed_images=processed_images,
    )

    # 5. 基础结构断言
    assert isinstance(result, dict)
    assert "chunks" in result
    assert "jsonl_path" in result
    assert "chunk_count" in result
    assert "embedded_count" in result

    assert result["chunk_count"] > 0
    assert result["embedded_count"] == result["chunk_count"]

    chunks = result["chunks"]
    assert isinstance(chunks, list)
    assert len(chunks) == result["chunk_count"]

    # 6. 检查 chunk 字段
    first_chunk = chunks[0]

    assert "chunk_id" in first_chunk
    assert "chunk_index" in first_chunk
    assert "section_title" in first_chunk
    assert "headers" in first_chunk
    assert "text" in first_chunk
    assert "image_paths" in first_chunk
    assert "images" in first_chunk
    assert "embedding" in first_chunk

    assert first_chunk["embedding"] is not None
    assert isinstance(first_chunk["embedding"], list)
    assert len(first_chunk["embedding"]) == 4

    # 7. 检查至少有一个 chunk 绑定了图片
    image_chunks = [
        chunk for chunk in chunks
        if image_path in chunk.get("image_paths", [])
    ]

    assert len(image_chunks) >= 1

    image_chunk = image_chunks[0]

    assert image_path in image_chunk["image_paths"]
    assert image_chunk["images"][0]["id"] == "figure_1"
    assert image_chunk["images"][0]["relative_path"] == relative_path

    # 8. 检查图表 caption 没有丢失
    assert "结构化四边形网格" in image_chunk["text"]
    assert "boundary-conforming quad mesh" in image_chunk["text"]
    assert "【图表信息补充开始】" in image_chunk["text"]

    # 9. 检查 JSONL 文件存在且内容可读
    jsonl_path = Path(result["jsonl_path"])

    assert jsonl_path.exists()
    assert jsonl_path.is_file()

    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == result["chunk_count"]

    loaded_first = json.loads(lines[0])

    assert "chunk_id" in loaded_first
    assert "text" in loaded_first
    assert "embedding" in loaded_first