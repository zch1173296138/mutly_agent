from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import types
from collections import Counter
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.evaluation.agent_ab import (
    DeterministicToolAdapter,
    LangGraphReactWorkerRunner,
    LangGraphVariantRunner,
    LinearReActRunner,
    LiveModelAdapter,
    LiveToolAdapter,
    STOP_COMPLETED,
    STOP_CONTROLLED,
    STOP_HUMAN_INPUT,
    STOP_LOOP_DETECTED,
    STOP_MAX_STEPS,
    STOP_PLANNER_FAILURE,
    STOP_RUNTIME_ERROR,
    STOP_TIMEOUT,
    STOP_TOOL_FAILURE,
    VARIANT_LANGGRAPH,
    VARIANT_LANGGRAPH_REACT_WORKER,
    VARIANT_REACT,
)
from evals.evaluators.common import event_node, trace_events
from evals.evaluators.latency import evaluate_latency
from evals.evaluators.no_loop import evaluate_no_loop
from evals.evaluators.node_path import evaluate_node_path
from evals.evaluators.termination import evaluate_termination
from evals.evaluators.tool_repetition import summarize_tool_repetition


COMMON_MOCK_TOOLS = {"rag_search", "pdf_parser", "calculator", "local_file_read", "web_search"}
LLM_MODE_MOCK_STABLE = "mock_llm_stable"
LLM_MODE_MOCK_LOOP = "mock_llm_loop"
LLM_MODE_REAL = "real_llm"
RUNNER_MODE_SINGLE_VARIANT = "single_variant"
RUNNER_VARIANTS = {
    VARIANT_LANGGRAPH: LangGraphVariantRunner,
    VARIANT_LANGGRAPH_REACT_WORKER: LangGraphReactWorkerRunner,
    VARIANT_REACT: LinearReActRunner,
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expected_tools_for_case(case: dict[str, Any]) -> list[str]:
    if "expected_tools" in case:
        return list(case.get("expected_tools") or [])
    return list(case.get("expected_project_tools") or []) + list(case.get("expected_source_tools") or [])


def _tool_arguments_for_case(case: dict[str, Any], tool_name: str) -> dict[str, Any]:
    source_path = ""
    for source in case.get("available_sources", []):
        if source.get("path"):
            source_path = str(source["path"])
            break

    if tool_name == "calculator":
        return {"expression": "1 + 1"}
    if tool_name in {"local_file_read", "pdf_parser"} and source_path:
        return {"path": source_path, "query": case.get("user_query", "")}
    return {"query": case.get("user_query", "")}


def _tool_calls_for_case(case: dict[str, Any], tool_names: list[str], call_index: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"mock_call_{call_index}_{index}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(
                    _tool_arguments_for_case(case, tool_name),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        }
        for index, tool_name in enumerate(tool_names, 1)
    ]


class CaseAwareMockModelAdapter:
    def __init__(self, case: dict[str, Any], *, loop_mode: bool) -> None:
        self.case = case
        self.loop_mode = loop_mode
        self.calls_by_role: Counter[str] = Counter()
        self.expected_tools = expected_tools_for_case(case)
        if self.loop_mode and not self.expected_tools:
            self.expected_tools = ["rag_search"]
        self.research_mode = "planner" in set(case.get("expected_nodes") or []) or bool(self.expected_tools)
        max_same_tool_calls = int((case.get("loop_rules") or {}).get("max_same_tool_calls", 2))
        self.loop_tool_rounds = max(2, min(4, max_same_tool_calls + 1))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        self.calls_by_role[role] += 1
        call_index = self.calls_by_role[role]

        if role == "controller":
            intent = "complex_research" if self.research_mode else "simple_chat"
            return {"content": json.dumps({"intent": intent}, ensure_ascii=False), "tool_calls": None}

        if role == "planner":
            task = {
                "task_id": "t1",
                "description": self.case.get("gold_behavior") or self.case.get("user_query", ""),
                "dependencies": [],
            }
            return {"content": json.dumps([task], ensure_ascii=False), "tool_calls": None}

        if role in {"worker", "react", "react_worker"}:
            if self.expected_tools:
                if self.loop_mode and call_index <= self.loop_tool_rounds:
                    return {
                        "content": "",
                        "tool_calls": _tool_calls_for_case(self.case, self.expected_tools, call_index),
                    }
                if not self.loop_mode and call_index == 1:
                    return {
                        "content": "",
                        "tool_calls": _tool_calls_for_case(self.case, self.expected_tools, call_index),
                    }
            return {"content": self.case.get("gold_behavior", "mock final answer"), "tool_calls": None}

        if role in {"reviewer", "simple_chat"}:
            return {"content": self.case.get("gold_behavior", "mock final answer"), "tool_calls": None}

        return {"content": self.case.get("gold_behavior", "mock final answer"), "tool_calls": None}


def build_mock_tool_adapter(case: dict[str, Any]) -> DeterministicToolAdapter:
    adapter = DeterministicToolAdapter.from_case(case, ROOT)
    tool_names = COMMON_MOCK_TOOLS | set(expected_tools_for_case(case))
    fallback_output = case.get("gold_behavior") or "mock tool output"
    for tool_name in tool_names:
        adapter.available_tools.add(tool_name)
        adapter.outputs.setdefault(tool_name, adapter.outputs.get("rag_search", fallback_output))
    return adapter


@contextmanager
def mock_tool_registry_module(tool_adapter: DeterministicToolAdapter):
    class _MockToolRegistry:
        async def get_all_tools(self) -> list[Any]:
            tools = []
            for item in tool_adapter.openai_tools():
                function = item.get("function", {})
                tools.append(
                    types.SimpleNamespace(
                        name=function.get("name", ""),
                        description=function.get("description", ""),
                        inputSchema=function.get("parameters") or {"type": "object", "properties": {}},
                    )
                )
            return tools

        async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            return types.SimpleNamespace(content=await tool_adapter.execute(tool_name, arguments))

    module = types.ModuleType("app.infrastructure.setup")
    module.tool_registry = _MockToolRegistry()
    sentinel = object()
    original = sys.modules.get("app.infrastructure.setup", sentinel)
    sys.modules["app.infrastructure.setup"] = module
    try:
        yield
    finally:
        if original is sentinel:
            sys.modules.pop("app.infrastructure.setup", None)
        else:
            sys.modules["app.infrastructure.setup"] = original


def status_from_stop_reason(stop_reason: str | None) -> str:
    if stop_reason in {STOP_COMPLETED, STOP_CONTROLLED, "END"}:
        return "completed"
    if stop_reason == STOP_HUMAN_INPUT:
        return "suspended"
    if stop_reason == STOP_TIMEOUT:
        return "running"
    if stop_reason in {
        STOP_LOOP_DETECTED,
        STOP_MAX_STEPS,
        STOP_RUNTIME_ERROR,
        STOP_TOOL_FAILURE,
        STOP_PLANNER_FAILURE,
    }:
        return "failed"
    return "running" if not stop_reason else "failed"


def run_dict_from_result(result: Any, *, status: str, wall_time_sec: float) -> dict[str, Any]:
    trace = [event.as_dict() if hasattr(event, "as_dict") else dict(event) for event in result.trace]
    return {
        "case_id": result.case_id,
        "status": status,
        "task_status": status,
        "stop_reason": result.stop_reason,
        "trace": trace,
        "final_output": result.final_output,
        "wall_time_sec": wall_time_sec,
        "error": result.error,
        "planner_parse_error": result.stop_reason == STOP_PLANNER_FAILURE
        or any(event.error for event in result.trace if event.action == "planner"),
        "empty_tasks": result.stop_reason == STOP_PLANNER_FAILURE and not any(
            event.action == "worker" for event in result.trace
        ),
    }


def timeout_run(case: dict[str, Any], wall_time_sec: float) -> dict[str, Any]:
    return {
        "case_id": case["id"],
        "status": "running",
        "task_status": "running",
        "stop_reason": STOP_TIMEOUT,
        "trace": [],
        "final_output": "",
        "wall_time_sec": wall_time_sec,
        "error": f"case timed out after {wall_time_sec:.3f}s",
        "planner_parse_error": False,
        "empty_tasks": False,
    }


def evaluate_run(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    termination = evaluate_termination(case, run)
    no_loop = evaluate_no_loop(case, run)
    node_path = evaluate_node_path(case, run)
    latency = evaluate_latency(case, run)
    repetition = summarize_tool_repetition(run)
    passed = all(result["passed"] for result in (termination, no_loop, node_path, latency))
    tool_events = [event for event in trace_events(run) if event.get("tool_name")]
    return {
        "case_id": case["id"],
        "passed": passed,
        "status": run["status"],
        "task_status": run["task_status"],
        "stop_reason": run["stop_reason"],
        "node_path": node_path,
        "node_path_observed": [node for event in trace_events(run) if (node := event_node(event))],
        "tool_calls": [
            {
                "step": event.get("step"),
                "node": event_node(event),
                "tool_name": event.get("tool_name"),
                "tool_arguments": event.get("tool_arguments") or {},
            }
            for event in tool_events
        ],
        "tool_arguments": [event.get("tool_arguments") or {} for event in tool_events],
        "final_output": run.get("final_output", ""),
        "wall_time_sec": run.get("wall_time_sec"),
        "trace": run.get("trace") or [],
        "error": run.get("error"),
        "planner_parse_error": bool(run.get("planner_parse_error")),
        "empty_tasks": bool(run.get("empty_tasks")),
        "termination": termination,
        "no_loop": no_loop,
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
            "planner_parse_error_count": 0,
            "empty_tasks_count": 0,
        }
    terminated = sum(1 for row in rows if row["termination"]["passed"])
    loops = sum(1 for row in rows if not row["no_loop"]["passed"])
    total_tool_calls = sum(row["tool_repetition"]["tool_call_count"] for row in rows)
    duplicate_tool_calls = sum(row["tool_repetition"]["duplicate_tool_call_count"] for row in rows)
    max_step_violations = sum(
        1 for row in rows if row["no_loop"]["violations"]["max_total_steps"] is not None
    )
    stuck_running = sum(1 for row in rows if row["termination"]["status"] == "running")
    planner_parse_errors = sum(1 for row in rows if row.get("planner_parse_error"))
    empty_tasks = sum(1 for row in rows if row.get("empty_tasks"))
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
        "planner_parse_error_count": planner_parse_errors,
        "empty_tasks_count": empty_tasks,
    }


async def run_case(
    case: dict[str, Any],
    *,
    mock_tools: bool,
    llm_mode: str,
    variant: str,
    timeout_sec: float,
    live_tool_adapter: LiveToolAdapter | None,
) -> dict[str, Any]:
    if llm_mode == LLM_MODE_REAL:
        model_adapter = LiveModelAdapter()
    else:
        model_adapter = CaseAwareMockModelAdapter(case, loop_mode=llm_mode == LLM_MODE_MOCK_LOOP)

    tool_adapter = build_mock_tool_adapter(case) if mock_tools else live_tool_adapter
    if tool_adapter is None:
        tool_adapter = await LiveToolAdapter.create()

    runner_cls = RUNNER_VARIANTS[variant]
    runner = runner_cls(model_adapter=model_adapter, tool_adapter=tool_adapter)
    started = time.perf_counter()
    try:
        registry_context = mock_tool_registry_module(tool_adapter) if mock_tools else nullcontext()
        with registry_context:
            result = await asyncio.wait_for(runner.run_case(case), timeout=timeout_sec)
    except asyncio.TimeoutError:
        return evaluate_run(case, timeout_run(case, round(time.perf_counter() - started, 3)))

    wall_time_sec = round((time.perf_counter() - started), 3)
    status = status_from_stop_reason(result.stop_reason)
    return evaluate_run(case, run_dict_from_result(result, status=status, wall_time_sec=wall_time_sec))


async def run_dataset(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.dataset)
    live_tool_adapter: LiveToolAdapter | None = None
    if not args.mock_tools:
        live_tool_adapter = await LiveToolAdapter.create()

    try:
        rows = [
            await run_case(
                case,
                mock_tools=args.mock_tools,
                llm_mode=args.llm_mode,
                variant=args.variant,
                timeout_sec=args.timeout_sec,
                live_tool_adapter=live_tool_adapter,
            )
            for case in cases
        ]
    finally:
        if live_tool_adapter is not None:
            await live_tool_adapter.close()

    return {"summary": summarize(rows), "cases": rows}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real LangGraph eval over a JSONL dataset.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--variant", choices=sorted(RUNNER_VARIANTS), default=VARIANT_LANGGRAPH)
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
    report = asyncio.run(run_dataset(args))
    report = {
        "runner_mode": RUNNER_MODE_SINGLE_VARIANT,
        "mock_tools": args.mock_tools,
        "llm_mode": args.llm_mode,
        "variant": args.variant,
        "evidentiary": args.llm_mode == LLM_MODE_REAL and not args.mock_tools,
        **report,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
