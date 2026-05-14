from __future__ import annotations

from typing import Any

from evals.adapters.base import UNKNOWN_LICENSE
from evals.adapters.base import BaseDatasetAdapter


class ToolBenchAdapter(BaseDatasetAdapter):
    dataset_name = "ToolBench"
    dataset_slug = "toolbench"
    source_url = "https://github.com/OpenBMB/ToolBench"
    default_split = "tool_eval_needs_review"
    default_tools = ("api_tool",)

    def convert_record(
        self,
        record: dict[str, Any],
        *,
        index: int,
        split: str | None = None,
        license: str = UNKNOWN_LICENSE,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        original_id = str(
            record.get("query_id")
            or record.get("id")
            or record.get("task_id")
            or record.get("qid")
            or index
        )
        query = record.get("query") or record.get("instruction") or record.get("question")
        expected_tools = self._tool_names(record)
        row = self._case(
            case_id=f"toolbench_{self._clean_split(split)}_{index:03d}",
            original_id=original_id,
            user_query=str(query or f"Complete ToolBench query {original_id}."),
            transformation="toolbench_local_record_to_multy_agent_schema:v1",
            raw_record={
                "keys": sorted(str(key) for key in record.keys()),
                "tool_count": len(expected_tools),
            },
            split=split or self.default_split,
            license=license,
            source_url=source_url or self.source_url,
            gold_answer=self._gold_answer(record),
        )
        row["category"] = "loop_stability"
        row["difficulty"] = "hard" if len(expected_tools) > 2 else "medium"
        row["expected_tools"] = expected_tools or ["api_tool"]
        row["max_steps"] = 18
        row["loop_rules"] = {
            "max_total_steps": 18,
            "max_same_tool_calls": 2,
            "max_same_tool_same_args": 1,
            "max_no_state_change_steps": 3,
        }
        return row

    @staticmethod
    def _tool_names(record: dict[str, Any]) -> list[str]:
        tools: list[str] = []
        for item in record.get("api_list") or record.get("tools") or []:
            if isinstance(item, dict):
                tool_name = item.get("tool_name") or item.get("name")
                api_name = item.get("api_name")
                if tool_name and api_name:
                    name = f"{tool_name}.{api_name}"
                else:
                    name = tool_name or api_name
            else:
                name = str(item)
            if name:
                tools.append(str(name))
        return tools

    @staticmethod
    def _gold_answer(record: dict[str, Any]) -> dict[str, Any]:
        for key in ("answer", "gold_answer", "final_answer", "reference_answer"):
            if record.get(key) not in (None, ""):
                return {"answer": record[key]}
        return {}

    @staticmethod
    def _clean_split(split: str | None) -> str:
        return (split or "unknown").replace("-", "_").replace("/", "_")
