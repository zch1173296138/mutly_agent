from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.evaluation.agent_ab import (  # noqa: E402
    REPORT_SOURCE_DETERMINISTIC,
    REPORT_SOURCE_LIVE,
    REPORT_SOURCE_SIMULATED,
    VARIANT_LANGGRAPH_REACT_WORKER,
    VARIANT_LANGGRAPH,
    VARIANT_REACT,
    DeterministicModelAdapter,
    DeterministicToolAdapter,
    LangGraphReactWorkerRunner,
    LangGraphVariantRunner,
    LinearReActRunner,
    build_ab_report,
)


DATASET_DIR = ROOT / "datasets" / "agent_eval"
CASES_PATH = DATASET_DIR / "eval_cases.jsonl"


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _tool_call(tool_name: str, query: str) -> dict[str, Any]:
    return {
        "id": f"call_{tool_name}",
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps({"query": query}, ensure_ascii=False),
        },
    }


def _first_tool(case: dict[str, Any], default: str = "rag_search") -> str:
    tools = case.get("expected_tools") or []
    return tools[0] if tools else default


def _langgraph_script(case: dict[str, Any]) -> list[dict[str, Any]]:
    simple_route = "simple_chat" in set(case.get("expected_nodes") or [])
    if simple_route and "planner" not in set(case.get("expected_nodes") or []):
        return [
            {"content": json.dumps({"intent": "simple_chat"})},
            {"content": f"完成：{case['user_query']}"},
        ]

    tool_name = _first_tool(case)
    task = [{"task_id": "t1", "description": case["user_query"], "dependencies": []}]
    script: list[dict[str, Any]] = [
        {"content": json.dumps({"intent": "complex_research"})},
        {"content": json.dumps(task, ensure_ascii=False)},
    ]

    if case["category"] == "hitl_safety":
        script.append(
            {
                "content": json.dumps(
                    {
                        "cannot_complete": True,
                        "reason": "需要人工确认后才能继续执行敏感操作。",
                    },
                    ensure_ascii=False,
                )
            }
        )
        return script

    if case["category"] == "tool_failure":
        script.append({"tool_calls": [_tool_call(tool_name, case["user_query"])]})
        return script

    if case.get("expected_tools"):
        script.extend(
            [
                {"tool_calls": [_tool_call(tool_name, case["user_query"])]},
                {"content": f"基于工具结果完成：{case['gold_behavior']}"},
            ]
        )
    else:
        script.append({"content": f"完成：{case['gold_behavior']}"})
    return script


def _react_script(case: dict[str, Any]) -> list[dict[str, Any]]:
    tool_name = _first_tool(case)
    if case["category"] == "loop_stability":
        repeated = case["loop_rules"]["max_total_steps"]
        return [
            {"tool_calls": [_tool_call(tool_name, case["user_query"])]}
            for _ in range(repeated)
        ]
    if case["category"] == "tool_failure":
        return [{"tool_calls": [_tool_call(tool_name, case["user_query"])]}]
    if case["category"] == "hitl_safety":
        return [
            {
                "content": json.dumps(
                    {"human_input_required": True, "reason": "需要人工确认。"},
                    ensure_ascii=False,
                )
            }
        ]
    if case.get("expected_tools"):
        return [
            {"tool_calls": [_tool_call(tool_name, case["user_query"])]},
            {"content": f"ReAct 基于工具结果完成：{case['gold_behavior']}"},
        ]
    return [{"content": f"ReAct 完成：{case['gold_behavior']}"}]


def make_runners(case: dict[str, Any], *, include_react_worker_ablation: bool = False) -> dict[str, Any]:
    repo_root = DATASET_DIR.parents[1]
    runners = {
        VARIANT_LANGGRAPH: LangGraphVariantRunner(
            DeterministicModelAdapter(_langgraph_script(case)),
            DeterministicToolAdapter.from_case(case, repo_root),
        ),
        VARIANT_REACT: LinearReActRunner(
            DeterministicModelAdapter(_react_script(case)),
            DeterministicToolAdapter.from_case(case, repo_root),
        ),
    }
    if include_react_worker_ablation:
        runners[VARIANT_LANGGRAPH_REACT_WORKER] = LangGraphReactWorkerRunner(
            DeterministicModelAdapter(_langgraph_script(case)),
            DeterministicToolAdapter.from_case(case, repo_root),
        )
    return runners


async def run_cases(
    cases: list[dict[str, Any]],
    source: str,
    *,
    include_react_worker_ablation: bool = False,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        runners = make_runners(case, include_react_worker_ablation=include_react_worker_ablation)
        for runner in runners.values():
            result = await runner.run_case(case)
            rows.append({"case": case, **result.as_dict()})
    return build_ab_report(rows, source=source).as_dict()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paired LangGraph/ReAct A/B evaluation.")
    parser.add_argument("--case-id", action="append", default=[], help="Run only this case ID. Repeatable.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of selected cases.")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON report to this path.")
    parser.add_argument(
        "--source",
        choices=[REPORT_SOURCE_DETERMINISTIC, REPORT_SOURCE_LIVE, REPORT_SOURCE_SIMULATED],
        default=REPORT_SOURCE_DETERMINISTIC,
        help="Report source label. simulated_fixture is marked non-evidentiary.",
    )
    parser.add_argument(
        "--include-react-worker-ablation",
        action="store_true",
        help="Include the evaluation-only langgraph_react_worker variant.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = load_cases()
    if args.case_id:
        selected = [case for case in cases if case["id"] in set(args.case_id)]
        missing = set(args.case_id) - {case["id"] for case in selected}
        if missing:
            raise SystemExit(f"Unknown case ID(s): {', '.join(sorted(missing))}")
    else:
        selected = cases
    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        raise SystemExit("No cases selected.")

    report = asyncio.run(
        run_cases(
            selected,
            args.source,
            include_react_worker_ablation=args.include_react_worker_ablation,
        )
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
