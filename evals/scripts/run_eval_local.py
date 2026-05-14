from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.evaluators.latency import evaluate_latency
from evals.evaluators.no_loop import evaluate_no_loop
from evals.evaluators.node_path import evaluate_node_path
from evals.evaluators.termination import evaluate_termination
from evals.evaluators.tool_repetition import summarize_tool_repetition


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expected_tools_for_case(case: dict[str, Any]) -> list[str]:
    if "expected_tools" in case:
        return list(case.get("expected_tools") or [])
    return list(case.get("expected_project_tools") or []) + list(case.get("expected_source_tools") or [])


def build_deterministic_run(case: dict[str, Any]) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    state_index = 0
    for node in case.get("expected_nodes") or []:
        trace.append(
            {
                "node": node,
                "action": node,
                "state_before": f"s{state_index}",
                "state_after": f"s{state_index + 1}",
            }
        )
        state_index += 1
    for index, tool_name in enumerate(expected_tools_for_case(case), 1):
        trace.append(
            {
                "node": "worker",
                "action": "worker_tool",
                "tool_name": tool_name,
                "tool_arguments": {"query": case.get("user_query", ""), "call_index": index},
                "state_before": f"s{state_index}",
                "state_after": f"s{state_index + 1}",
            }
        )
        state_index += 1
    return {
        "case_id": case["id"],
        "status": "completed",
        "stop_reason": "completed",
        "trace": trace,
        "final_output": case.get("gold_behavior", ""),
        "wall_time_sec": 0.0,
    }


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    run = build_deterministic_run(case)
    termination = evaluate_termination(case, run)
    no_loop = evaluate_no_loop(case, run)
    node_path = evaluate_node_path(case, run)
    latency = evaluate_latency(case, run)
    repetition = summarize_tool_repetition(run)
    passed = all(
        result["passed"]
        for result in (
            termination,
            no_loop,
            node_path,
            latency,
        )
    )
    return {
        "case_id": case["id"],
        "passed": passed,
        "termination": termination,
        "no_loop": no_loop,
        "node_path": node_path,
        "latency": latency,
        "tool_repetition": repetition,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(rows)
    if not case_count:
        return {
            "case_count": 0,
            "termination_rate": 0.0,
            "loop_rate": 0.0,
            "avg_tool_calls": 0.0,
            "duplicate_tool_call_ratio": 0.0,
            "max_step_violation_rate": 0.0,
            "stuck_running_count": 0,
        }
    terminated = sum(1 for row in rows if row["termination"]["passed"])
    loops = sum(1 for row in rows if not row["no_loop"]["passed"])
    total_tool_calls = sum(row["tool_repetition"]["tool_call_count"] for row in rows)
    duplicate_tool_calls = sum(row["tool_repetition"]["duplicate_tool_call_count"] for row in rows)
    max_step_violations = sum(
        1 for row in rows if row["no_loop"]["violations"]["max_total_steps"] is not None
    )
    stuck_running = sum(
        1 for row in rows if row["termination"]["status"] == "running"
    )
    return {
        "case_count": case_count,
        "termination_rate": round(terminated / case_count, 4),
        "loop_rate": round(loops / case_count, 4),
        "avg_tool_calls": round(total_tool_calls / case_count, 4),
        "duplicate_tool_call_ratio": (
            round(duplicate_tool_calls / total_tool_calls, 4) if total_tool_calls else 0.0
        ),
        "max_step_violation_rate": round(max_step_violations / case_count, 4),
        "stuck_running_count": stuck_running,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic local eval over a JSONL dataset.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    cases = load_jsonl(args.dataset)
    rows = [evaluate_case(case) for case in cases]
    report = {"summary": summarize(rows), "cases": rows}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
