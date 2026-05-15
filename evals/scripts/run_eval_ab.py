from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.evaluation.agent_ab import (
    VARIANT_LANGGRAPH,
    VARIANT_LANGGRAPH_REACT_WORKER,
    VARIANT_REACT,
)
from evals.scripts.run_eval_langgraph import (
    LLM_MODE_MOCK_LOOP,
    LLM_MODE_MOCK_STABLE,
    LLM_MODE_REAL,
    run_dataset,
)


AB_VARIANTS = (VARIANT_LANGGRAPH, VARIANT_REACT, VARIANT_LANGGRAPH_REACT_WORKER)
PAIRWISE_COMPARISONS = (
    (VARIANT_LANGGRAPH, VARIANT_REACT),
    (VARIANT_LANGGRAPH, VARIANT_LANGGRAPH_REACT_WORKER),
    (VARIANT_LANGGRAPH_REACT_WORKER, VARIANT_REACT),
)


def _numeric_delta(left: Any, right: Any) -> float | int:
    if isinstance(left, int) and isinstance(right, int):
        return left - right
    return round(float(left or 0.0) - float(right or 0.0), 4)


def pairwise_deltas(variant_reports: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    deltas: dict[str, dict[str, Any]] = {}
    for left_name, right_name in PAIRWISE_COMPARISONS:
        left = variant_reports.get(left_name, {}).get("summary")
        right = variant_reports.get(right_name, {}).get("summary")
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        common_keys = sorted(set(left) & set(right))
        deltas[f"{left_name}__vs__{right_name}"] = {
            key: _numeric_delta(left[key], right[key])
            for key in common_keys
            if isinstance(left.get(key), (int, float)) and isinstance(right.get(key), (int, float))
        }
    return deltas


async def run_ab(args: argparse.Namespace) -> dict[str, Any]:
    variants: dict[str, dict[str, Any]] = {}
    for variant in AB_VARIANTS:
        single_args = argparse.Namespace(
            dataset=args.dataset,
            output=None,
            variant=variant,
            mock_tools=args.mock_tools,
            llm_mode=args.llm_mode,
            timeout_sec=args.timeout_sec,
        )
        try:
            variants[variant] = await run_dataset(single_args)
        except Exception as exc:
            variants[variant] = {
                "error": str(exc) or exc.__class__.__name__,
                "summary": None,
                "cases": [],
            }

    return {
        "runner_mode": "ab",
        "mock_tools": args.mock_tools,
        "llm_mode": args.llm_mode,
        "evidentiary": args.llm_mode == LLM_MODE_REAL and not args.mock_tools,
        "variants": variants,
        "pairwise_deltas": pairwise_deltas(variants),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LangGraph/ReAct A/B eval over a JSONL dataset.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--mock-tools", action="store_true", help="Use deterministic in-process tools.")
    llm_group = parser.add_mutually_exclusive_group()
    llm_group.add_argument(
        "--mock-llm-stable",
        dest="llm_mode",
        action="store_const",
        const=LLM_MODE_MOCK_STABLE,
        help="Use a deterministic LLM that calls expected tools once, then returns gold_behavior.",
    )
    llm_group.add_argument(
        "--mock-llm-loop",
        dest="llm_mode",
        action="store_const",
        const=LLM_MODE_MOCK_LOOP,
        help="Use a deterministic LLM that repeats tool calls before stopping.",
    )
    llm_group.add_argument(
        "--real-llm",
        dest="llm_mode",
        action="store_const",
        const=LLM_MODE_REAL,
        help="Use the configured real LLM client. Requires API/model environment variables.",
    )
    parser.set_defaults(llm_mode=LLM_MODE_MOCK_STABLE)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_ab(args))
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
