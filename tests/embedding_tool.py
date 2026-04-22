import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from app.rag.embedding_tool import (
    step1_extract_layout,
    step2_generate_image_captions,
    step3_merge_context,
    step4_chunk_and_embed,
)


async def main():
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        required=True,
        help="PDF 文件路径，例如 storage/papers/BRepFormer.pdf",
    )
    parser.add_argument(
        "--skip-step2",
        action="store_true",
        help="跳过图片 caption，只把正文入库",
    )
    parser.add_argument(
        "--parsed-dir",
        default="",
        help="已有 Step1 输出目录，例如 storage/parsed/Optimizing；如果提供，则跳过 Step1",
    )
    args = parser.parse_args()

    if args.parsed_dir:
        print("=" * 80)
        print("跳过 Step1: 使用已有解析目录")
        print("=" * 80)

        output_dir = Path(args.parsed_dir).resolve()
        extracted_json_path = output_dir / "extracted_data.json"

        if extracted_json_path.exists():
            extracted_data = json.loads(
                extracted_json_path.read_text(encoding="utf-8")
            )
            print(f"已读取 Step1 结果: {extracted_json_path}")
        else:
            md_files = sorted(output_dir.rglob("*.md"), key=lambda p: len(str(p)))
            if not md_files:
                raise FileNotFoundError(f"没有找到 Markdown 文件: {output_dir}")

            md_path = md_files[0]
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

            extracted_data = {
                "text": md_path.read_text(encoding="utf-8", errors="ignore"),
                "images": images,
                "markdown_path": str(md_path),
                "output_dir": str(output_dir),
            }

            extracted_json_path.write_text(
                json.dumps(extracted_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            print(f"已重新构造 Step1 结果: {extracted_json_path}")

    else:
        pdf_path = Path(args.pdf).resolve()

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 不存在: {pdf_path}")

        print("=" * 80)
        print("Step1: PDF 解析")
        print("=" * 80)

        extracted_data = await step1_extract_layout(str(pdf_path))

        print("markdown_path:", extracted_data.get("markdown_path"))
        print("output_dir:", extracted_data.get("output_dir"))
        print("image_count:", len(extracted_data.get("images", [])))

        output_dir = Path(extracted_data["output_dir"])
        extracted_json_path = output_dir / "extracted_data.json"
        extracted_json_path.write_text(
            json.dumps(extracted_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"Step1 结果已保存: {extracted_json_path}")

    print("=" * 80)
    print("Step2: 图片 Caption")
    print("=" * 80)

    captions_path = output_dir / "captions.json"

    if args.skip_step2:
        captions = {}
        print("跳过 Step2，captions 为空。")

    elif captions_path.exists():
        captions = json.loads(captions_path.read_text(encoding="utf-8"))
        print(f"复用 captions.json: {captions_path}")

    else:
        captions = await step2_generate_image_captions(
            extracted_data.get("images", [])
        )
        captions_path.write_text(
            json.dumps(captions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"Step2 结果已保存: {captions_path}")
    print("caption_count:", len(captions))

    print("=" * 80)
    print("Step3: 合并 Markdown + 图像描述")
    print("=" * 80)

    merged_text = await step3_merge_context(
        extracted_data=extracted_data,
        captions=captions,
    )

    merged_path = output_dir / "merged_with_captions.md"
    merged_path.write_text(merged_text, encoding="utf-8")

    print(f"Step3 结果已保存: {merged_path}")
    print("merged_text_length:", len(merged_text))

    print("=" * 80)
    print("Step4: Chunk + Embedding + Chroma 入库")
    print("=" * 80)

    result = await step4_chunk_and_embed(
        merged_text=merged_text,
        processed_images=extracted_data.get("images", []),
    )

    result_path = output_dir / "step4_result.json"
    safe_result = {
        "vector_db": result.get("vector_db"),
        "chunk_count": result.get("chunk_count"),
        "embedded_count": result.get("embedded_count"),
        "chroma": result.get("chroma"),
    }

    result_path.write_text(
        json.dumps(safe_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Step4 完成:")
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))
    print(f"Step4 结果已保存: {result_path}")

    print("=" * 80)
    print("入库完成")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())