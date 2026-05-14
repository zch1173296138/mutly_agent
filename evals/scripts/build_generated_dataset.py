from __future__ import annotations

import argparse
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.adapters.agentbench_adapter import AgentBenchAdapter
from evals.adapters.gaia_adapter import GAIAAdapter
from evals.adapters.tau_bench_adapter import TauBenchAdapter
from evals.adapters.toolbench_adapter import ToolBenchAdapter
from evals.adapters.base import UNKNOWN_LICENSE


DEFAULT_SOURCE_SEED = ROOT / "datasets" / "agent_eval" / "eval_cases.jsonl"
DEFAULT_SEED_COPY = ROOT / "evals" / "datasets" / "seed" / "eval_cases.jsonl"
DEFAULT_OUTPUT = ROOT / "evals" / "datasets" / "generated" / "multy_agent_loop_eval.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + "\n",
        encoding="utf-8",
    )


def source_block(kind: str, dataset: str, original_id: str, transformation: str) -> dict[str, str]:
    return {
        "kind": kind,
        "dataset": dataset,
        "original_id": original_id,
        "source_url": "evals/datasets/seed/eval_cases.jsonl",
        "license": UNKNOWN_LICENSE,
        "split": "seed" if kind == "seed_dataset" else "generated",
        "transformation": transformation,
    }


def extend_seed_cases(seed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in seed_rows:
        copied = deepcopy(row)
        copied["source"] = source_block(
            "seed_dataset",
            "multy_agent_seed",
            str(row["id"]),
            "copied_with_schema_extension",
        )
        rows.append(copied)
    return rows


def synthetic_cases(seed_rows: list[dict[str, Any]], *, count: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prompts = [
        "Repeat the same lookup until you are absolutely certain, but stop if the state does not change.",
        "The first tool result may be incomplete; retry once, then produce a bounded failure.",
        "If evidence is missing, do not keep searching forever; explain the missing prerequisite.",
        "Use the same arguments only once; a second identical call should trigger a controlled stop.",
    ]
    for index in range(count):
        base = deepcopy(seed_rows[index % len(seed_rows)])
        original_id = str(base["id"])
        base["id"] = f"synthetic_loop_{index + 1:03d}"
        base["category"] = "loop_stability"
        base["difficulty"] = "hard" if index % 3 == 0 else "medium"
        base["user_query"] = f"{base['user_query']}\n\nAdversarial loop condition: {prompts[index % len(prompts)]}"
        base["gold_behavior"] = (
            "The agent should make bounded progress, avoid repeated identical tool calls, "
            "and terminate with completed, failed, or suspended status instead of running indefinitely. "
            f"Derived from seed case {original_id}."
        )
        base["expected_nodes"] = ["controller", "planner", "worker", "reviewer"]
        if not base.get("expected_tools"):
            base["expected_tools"] = ["rag_search"]
        base["max_steps"] = min(max(int(base.get("max_steps", 12)), 8), 14)
        base["loop_rules"] = {
            "max_total_steps": base["max_steps"],
            "max_same_tool_calls": 2,
            "max_same_tool_same_args": 1,
            "max_no_state_change_steps": 3,
        }
        base["source"] = source_block(
            "synthetic_from_seed",
            "multy_agent_loop_eval",
            original_id,
            "adversarial_loop_derivation:v1",
        )
        base["synthetic_metadata"] = {
            "derivation_index": index + 1,
            "base_case_id": original_id,
            "loop_pressure": prompts[index % len(prompts)],
        }
        rows.append(base)
    return rows


def open_source_slots() -> list[dict[str, Any]]:
    adapters = [GAIAAdapter(), AgentBenchAdapter(), ToolBenchAdapter(), TauBenchAdapter()]
    rows: list[dict[str, Any]] = []
    for adapter in adapters:
        rows.extend(adapter.load(limit=5))
    return rows


def build_dataset(source_seed: Path, seed_copy: Path, output: Path) -> list[dict[str, Any]]:
    seed_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_seed, seed_copy)
    seed_rows = load_jsonl(seed_copy)
    rows = extend_seed_cases(seed_rows) + synthetic_cases(seed_rows) + open_source_slots()
    write_jsonl(output, rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build generated multy_agent loop eval dataset.")
    parser.add_argument("--source-seed", type=Path, default=DEFAULT_SOURCE_SEED)
    parser.add_argument("--seed-copy", type=Path, default=DEFAULT_SEED_COPY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = build_dataset(args.source_seed, args.seed_copy, args.output)
    print(json.dumps({"output": str(args.output), "case_count": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
