from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expected_tools_for_metadata(case: dict[str, Any]) -> list[str]:
    legacy_tools = list(case.get("expected_tools") or [])
    if legacy_tools:
        return legacy_tools
    return list(case.get("expected_project_tools") or []) + list(case.get("expected_source_tools") or [])


def example_from_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "inputs": {"user_query": case["user_query"]},
        "reference_outputs": {
            "gold_behavior": case["gold_behavior"],
            "gold_answer": case.get("gold_answer"),
        },
        "metadata": {
            "category": case["category"],
            "difficulty": case["difficulty"],
            "source": case.get("source"),
            "loop_rules": case.get("loop_rules"),
            "expected_nodes": case.get("expected_nodes"),
            "expected_tool_categories": list(case.get("expected_tool_categories") or []),
            "expected_project_tools": list(case.get("expected_project_tools") or []),
            "expected_source_tools": list(case.get("expected_source_tools") or []),
            "expected_tools": expected_tools_for_metadata(case),
        },
    }


def sync_examples(dataset_path: Path, dataset_name: str, *, dry_run: bool) -> list[dict[str, Any]]:
    examples = [example_from_case(case) for case in load_jsonl(dataset_path)]
    if dry_run:
        return examples

    from langsmith import Client

    client = Client()
    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
    except Exception:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="multy_agent loop evaluation dataset",
        )

    for example in examples:
        client.create_example(
            inputs=example["inputs"],
            outputs=example["reference_outputs"],
            metadata=example["metadata"],
            dataset_id=dataset.id,
        )
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync eval JSONL cases to a LangSmith dataset.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    examples = sync_examples(args.dataset, args.dataset_name, dry_run=args.dry_run)
    print(json.dumps({"dataset_name": args.dataset_name, "examples": examples}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
