from __future__ import annotations

from typing import Any

from evals.adapters.base import UNKNOWN_LICENSE
from evals.adapters.base import BaseDatasetAdapter


class GAIAAdapter(BaseDatasetAdapter):
    dataset_name = "GAIA"
    dataset_slug = "gaia"
    source_url = "https://huggingface.co/datasets/gaia-benchmark/GAIA"
    default_split = "validation_or_test_needs_review"
    default_tools = ("web_search", "local_file_read")

    def convert_record(
        self,
        record: dict[str, Any],
        *,
        index: int,
        split: str | None = None,
        license: str = UNKNOWN_LICENSE,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        original_id = str(record.get("task_id") or record.get("id") or index)
        question = record.get("Question") or record.get("question") or record.get("query")
        answer = record.get("Final answer") or record.get("final_answer") or record.get("answer")
        level = str(record.get("Level") or record.get("level") or "").strip()
        difficulty = {"1": "medium", "2": "hard", "3": "hard"}.get(level, "medium")
        tools = ["web_search"]
        if record.get("file_name") or record.get("file_path"):
            tools.append("local_file_read")
        row = self._case(
            case_id=f"gaia_{self._clean_split(split)}_{index:03d}",
            original_id=original_id,
            user_query=str(question or f"Answer GAIA task {original_id}."),
            transformation="gaia_local_record_to_multy_agent_schema:v1",
            raw_record={
                "keys": sorted(str(key) for key in record.keys()),
                "level": level,
                "has_file": bool(record.get("file_name") or record.get("file_path")),
            },
            split=split or self.default_split,
            license=license,
            source_url=source_url or self.source_url,
            gold_answer={"answer": answer} if answer not in (None, "") else {},
        )
        row["difficulty"] = difficulty
        row["category"] = "end_to_end_report"
        row["expected_tools"] = tools
        row["max_steps"] = 16
        row["loop_rules"] = {
            "max_total_steps": 16,
            "max_same_tool_calls": 2,
            "max_same_tool_same_args": 1,
            "max_no_state_change_steps": 3,
        }
        return row

    @staticmethod
    def _clean_split(split: str | None) -> str:
        return (split or "unknown").replace("-", "_").replace("/", "_")
