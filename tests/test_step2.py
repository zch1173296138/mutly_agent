import os
import pytest
from openai import AsyncOpenAI

from app.infrastructure.image_to_text_ingestion import step2_generate_image_captions


@pytest.mark.asyncio
async def test_step2_generate_image_captions():
    image_path = r"E:\python\sweepFormer\docs\Optimizing\images\0a42f4a28c938602f421ba8994b669193f9959f152ff79ca4a1b244c6c72ce28.jpg"

    images = [
        {
            "id": "figure_1",
            "path": image_path,
            "relative_path": "images/figure_1.png",
            "raw_match": "![](images/figure_1.png)",
        }
    ]

    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )

    captions = await step2_generate_image_captions(images, client)

    assert image_path in captions
    assert len(captions[image_path]) > 20

    print(captions[image_path])