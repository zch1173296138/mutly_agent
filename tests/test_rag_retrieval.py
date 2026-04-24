import pytest

from app.rag.query_embedding import retrieve_paper_context


@pytest.mark.asyncio
async def test_retrieve_paper_context_basic():
    result = await retrieve_paper_context(
        query="What is the main method of this paper?",
        top_k=6,
        fetch_k=30,
        fused_fetch_k=40,
        use_query_rewrite=True,
        use_rrf=True,
        use_mmr=True,
    )

    assert "results" in result

    results = result["results"]

    print("\nsearch_queries:", result.get("search_queries"))
    print("hit count:", len(results))

    for i, item in enumerate(results, start=1):
        print("=" * 80)
        print("rank:", i)
        print("chunk_id:", item.get("chunk_id"))
        print("chunk_index:", item.get("chunk_index"))
        print("section_title:", item.get("section_title"))
        print("score:", item.get("score"))
        print("rrf_score:", item.get("rrf_score"))
        print("rrf_hits:", item.get("rrf_hits"))
        print("mmr_rank:", item.get("mmr_rank"))
        print("mmr_score:", item.get("mmr_score"))
        print("matched_query:", item.get("matched_query"))
        print("text preview:", item.get("text", "")[:300])

    assert len(results) > 0
    assert len(results) <= 6