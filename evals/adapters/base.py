from __future__ import annotations

import json
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


UNKNOWN_LICENSE = "UNKNOWN_NEEDS_REVIEW"
FORMAL_TEST_SPLITS = {"test", "official_test", "formal_test"}

SCORING_TEMPLATE = {
    "task_success": 0.35,
    "evidence_correctness": 0.15,
    "tool_use_correctness": 0.2,
    "no_loop": 0.2,
    "final_report_quality": 0.1,
}


@dataclass(frozen=True)
class SourceInfo:
    kind: str
    dataset: str
    original_id: str
    source_url: str
    license: str
    split: str
    transformation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "dataset": self.dataset,
            "original_id": self.original_id,
            "source_url": self.source_url,
            "license": self.license,
            "split": self.split,
            "transformation": self.transformation,
        }


class BaseDatasetAdapter(ABC):
    """Normalize external agent benchmarks into the local JSONL case schema.

    Adapters are dry-run first. They never download data by default; callers must
    pass a local input path and set dry_run=False to convert real records.
    """

    dataset_name: str = "unknown"
    dataset_slug: str = "unknown"
    source_url: str = "UNKNOWN_NEEDS_REVIEW"
    default_split: str = "review_queue"
    default_category: str = "loop_stability"
    default_tools: tuple[str, ...] = ()

    def load(
        self,
        input_path: str | Path | None = None,
        *,
        limit: int | None = None,
        dry_run: bool = True,
        split: str | None = None,
        license: str = UNKNOWN_LICENSE,
        source_url: str | None = None,
    ) -> list[dict[str, Any]]:
        if dry_run:
            return self.placeholder_cases(count=limit or 5)
        split = split or self.default_split
        self._validate_license_for_split(license=license, split=split)
        if input_path is None:
            raise ValueError("input_path is required when dry_run=False")
        rows = list(self._read_json_records(Path(input_path)))
        if limit is not None:
            rows = rows[:limit]
        return [
            self.convert_record(
                record,
                index=index + 1,
                split=split,
                license=license,
                source_url=source_url or self.source_url,
            )
            for index, record in enumerate(rows)
        ]

    def placeholder_cases(self, *, count: int = 5) -> list[dict[str, Any]]:
        return [self._placeholder_case(index) for index in range(1, count + 1)]

    def convert_record(
        self,
        record: dict[str, Any],
        *,
        index: int,
        split: str | None = None,
        license: str = UNKNOWN_LICENSE,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        original_id = str(record.get("id") or record.get("task_id") or index)
        user_query = (
            record.get("question")
            or record.get("query")
            or record.get("instruction")
            or record.get("task")
            or f"Review {self.dataset_name} record {original_id} for agent loop behavior."
        )
        return self._case(
            case_id=f"{self.dataset_slug}_{index:03d}",
            original_id=original_id,
            user_query=str(user_query),
            transformation="normalized_from_local_adapter_input",
            raw_record={"adapter_input_keys": sorted(str(key) for key in record.keys())},
            split=split or self.default_split,
            license=license,
            source_url=source_url or self.source_url,
        )

    def _case(
        self,
        *,
        case_id: str,
        original_id: str,
        user_query: str,
        transformation: str,
        raw_record: dict[str, Any] | None = None,
        split: str | None = None,
        license: str = UNKNOWN_LICENSE,
        source_url: str | None = None,
        gold_answer: dict[str, Any] | None = None,
        label_review_status: str = "needs_review",
    ) -> dict[str, Any]:
        split = split or self.default_split
        license = license or UNKNOWN_LICENSE
        source_url = source_url or self.source_url
        return {
            "id": case_id,
            "category": self.default_category,
            "difficulty": "medium",
            "user_query": user_query,
            "available_sources": [],
            "gold_answer": gold_answer or {},
            "gold_behavior": (
                "Adapter-normalized case must terminate with a bounded answer or a controlled "
                "failure; repeated identical tool calls should be marked as a loop."
            ),
            "evidence": [],
            "expected_nodes": ["controller", "planner", "worker", "reviewer"],
            "expected_tools": list(self.default_tools),
            "max_steps": 12,
            "loop_rules": {
                "max_total_steps": 12,
                "max_same_tool_calls": 2,
                "max_same_tool_same_args": 1,
                "max_no_state_change_steps": 3,
            },
            "scoring": dict(SCORING_TEMPLATE),
            "source": SourceInfo(
                kind="open_source_dataset",
                dataset=self.dataset_name,
                original_id=original_id,
                source_url=source_url,
                license=license,
                split=split,
                transformation=transformation,
            ).as_dict(),
            "label_review": {
                "status": label_review_status,
                "reviewer": None,
                "reviewed_at": None,
                "confidence": "low",
                "notes": "Open-source adapter import requires human label and license review before formal use.",
            },
            "adapter_metadata": {
                "dry_run": raw_record is None,
                "license_review_required": license == UNKNOWN_LICENSE,
                "raw_record": raw_record or {},
            },
        }

    def _placeholder_case(self, index: int) -> dict[str, Any]:
        return self._case(
            case_id=f"{self.dataset_slug}_slot_{index:03d}",
            original_id=f"slot-{index:03d}",
            user_query=(
                f"Reserved {self.dataset_name} adapter input slot {index}. "
                "Populate from a locally reviewed dataset record before formal use."
            ),
            transformation="reserved_adapter_slot_no_dataset_content",
        )

    @staticmethod
    def _validate_license_for_split(*, license: str, split: str) -> None:
        if split in FORMAL_TEST_SPLITS and license == UNKNOWN_LICENSE:
            raise ValueError(
                "license must be explicitly reviewed before importing samples into formal test split"
            )

    @staticmethod
    def _read_json_records(path: Path) -> Iterable[dict[str, Any]]:
        if path.suffix == ".jsonl":
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if line.strip():
                    yield json.loads(line)
            return
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            yield from data
        elif isinstance(data, dict) and isinstance(data.get("examples"), list):
            yield from data["examples"]
        else:
            raise ValueError(f"unsupported adapter input shape: {path}")
