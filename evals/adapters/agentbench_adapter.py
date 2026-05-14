from __future__ import annotations

from typing import Any

from evals.adapters.base import UNKNOWN_LICENSE
from evals.adapters.base import BaseDatasetAdapter


class AgentBenchAdapter(BaseDatasetAdapter):
    dataset_name = "AgentBench"
    dataset_slug = "agentbench"
    source_url = "https://github.com/THUDM/AgentBench"
    default_split = "dev_or_test_needs_review"
    default_tools = ("web_search",)

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
            record.get("id")
            or record.get("task_id")
            or record.get("instance_id")
            or record.get("qid")
            or index
        )
        instruction = (
            record.get("instruction")
            or record.get("query")
            or record.get("question")
            or record.get("task")
            or f"Run AgentBench task {original_id}."
        )
        environment = str(record.get("environment") or record.get("env") or "").lower()
        expected_tools = self._tools_for_environment(environment, str(instruction).lower())
        row = self._case(
            case_id=f"agentbench_{self._clean_split(split)}_{index:03d}",
            original_id=original_id,
            user_query=str(instruction),
            transformation="agentbench_local_record_to_multy_agent_schema:v1",
            raw_record={
                "keys": sorted(str(key) for key in record.keys()),
                "environment": environment,
            },
            split=split or self.default_split,
            license=license,
            source_url=source_url or self.source_url,
            gold_answer=self._gold_answer(record),
        )
        row["category"] = "tool_failure" if "error" in environment else "loop_stability"
        row["difficulty"] = "medium"
        row["expected_tools"] = expected_tools
        row["max_steps"] = 14
        row["loop_rules"] = {
            "max_total_steps": 14,
            "max_same_tool_calls": 2,
            "max_same_tool_same_args": 1,
            "max_no_state_change_steps": 3,
        }
        return row

    @staticmethod
    def _tools_for_environment(environment: str, instruction: str) -> list[str]:
        if "db" in environment or "sql" in instruction:
            return ["database_query"]
        if "os" in environment or "terminal" in instruction or "shell" in instruction:
            return ["local_file_read"]
        if "web" in environment or "browser" in instruction:
            return ["web_search"]
        return ["web_search"]

    @staticmethod
    def _gold_answer(record: dict[str, Any]) -> dict[str, Any]:
        for key in ("answer", "gold_answer", "reference_answer", "expected_answer"):
            if record.get(key) not in (None, ""):
                return {"answer": record[key]}
        if record.get("evaluation") not in (None, ""):
            return {"evaluation": record["evaluation"]}
        return {}

    @staticmethod
    def _clean_split(split: str | None) -> str:
        return (split or "unknown").replace("-", "_").replace("/", "_")
