import pytest
import os
from app.infrastructure.image_to_text_ingestion import step1_extract_layout

# 直接在这里设置你要测试的真实 PDF 路径
PDF_PATH = r"C:\Users\123\Downloads\Optimizing.pdf"

@pytest.mark.asyncio
async def test_step1_real_pdf_parsing():
    """
    使用真实的 PDF 测试 MinerU 解析 (Step 1)
    """
    # if not os.path.exists(PDF_PATH):
    #     pytest.skip(f"文件不存在，跳过测试: {PDF_PATH}")
        
    print(f"\n开始使用 MinerU 解析 PDF: {PDF_PATH}")
    print("这可能需要一些时间，请耐心等待...")
    
    # 执行 step1 解析
    result = await step1_extract_layout(PDF_PATH)
    
    # 基础断言：检查返回字典是否含有所需字段
    assert "text" in result, "结果中应包含文本内容"
    assert "images" in result, "结果中应含提取到的图片信息"
    
    text = result["text"]
    images = result["images"]
    
    # 打印一些统计信息，在 pytest -s 模式下可以被看到
    print(f"\n📄 文本总长度: {len(text)} 字符")
    print(f"📸 共提取到 {len(images)} 张图片")
    
    for idx, img in enumerate(images, 1):
        print(f"  {idx}. ID: {img.get('id')} | 原始占位: {img.get('raw_match')}")
        
    # 断言有解析出实质性文本
    assert len(text) > 0, "提取出的文本不能为空"
