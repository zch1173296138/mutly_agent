from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig


VARIANT_LANGGRAPH = "langgraph_state_machine"
VARIANT_LANGGRAPH_REACT_WORKER = "langgraph_react_worker"
VARIANT_REACT = "linear_react_baseline"
VARIANTS = (VARIANT_LANGGRAPH, VARIANT_REACT)
OPTIONAL_VARIANTS = (VARIANT_LANGGRAPH_REACT_WORKER,)
ALL_VARIANTS = (VARIANT_LANGGRAPH, VARIANT_LANGGRAPH_REACT_WORKER, VARIANT_REACT)
PAIRWISE_COMPARISONS = (
    (VARIANT_LANGGRAPH, VARIANT_REACT),
    (VARIANT_LANGGRAPH, VARIANT_LANGGRAPH_REACT_WORKER),
    (VARIANT_LANGGRAPH_REACT_WORKER, VARIANT_REACT),
)

STOP_COMPLETED = "completed"
STOP_CONTROLLED = "controlled_stop"
STOP_LOOP_DETECTED = "loop_detected"
STOP_MAX_STEPS = "max_steps_exceeded"
STOP_TOOL_FAILURE = "tool_failure"
STOP_HUMAN_INPUT = "human_input_required"
STOP_RUNTIME_ERROR = "runtime_error"
STOP_TIMEOUT = "timeout"

REPORT_SOURCE_DETERMINISTIC = "deterministic_adapter"
REPORT_SOURCE_LIVE = "live_integration"
REPORT_SOURCE_SIMULATED = "simulated_fixture"

TARGET_GROUPS = {
    "planner": {"planner_decomposition"},
    "retrieval": {"rag_retrieval"},
    "extraction": {"pdf_parsing", "financial_report_qa"},
    "end_to_end": {"end_to_end_report"},
    "loop_stability": {"loop_stability"},
}


def is_formal_case(case: dict[str, Any]) -> bool:
    review = case.get("label_review") or {}
    return (
        review.get("status") == "approved"
        and review.get("confidence") != "low"
        and bool(str(review.get("reviewer") or "").strip())
        and bool(str(review.get("reviewed_at") or "").strip())
        and bool(str(review.get("notes") or "").strip())
    )


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:16]


def _summarize(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else _stable_json(value)
    return text[:limit] + ("..." if len(text) > limit else "")


def _state_fingerprint(state: dict[str, Any]) -> str:
    return _hash(_jsonable(state))


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return json.loads(_stable_json(_jsonable(state)))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("app."):
        return _jsonable(vars(value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _state_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_json = _jsonable(before)
    after_json = _jsonable(after)
    before_keys = set(before_json)
    after_keys = set(after_json)
    changed = [
        key
        for key in sorted(before_keys & after_keys)
        if before_json.get(key) != after_json.get(key)
    ]
    return {
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
        "changed": changed,
    }


@dataclass
class TraceEvent:
    variant: str
    step: int
    action: str
    state_before: str
    state_after: str
    state_diff: dict[str, Any]
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_output: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    elapsed_ms: float | None = None

    @property
    def tool_argument_hash(self) -> str | None:
        return _hash(self.tool_arguments or {}) if self.tool_name else None

    @property
    def tool_output_hash(self) -> str | None:
        return _hash(self.tool_output or "") if self.tool_name else None

    @property
    def state_changed(self) -> bool:
        return self.state_before != self.state_after

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "step": self.step,
            "action": self.action,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "state_diff": self.state_diff,
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "tool_argument_hash": self.tool_argument_hash,
            "tool_output_summary": _summarize(self.tool_output or "") if self.tool_output else None,
            "tool_output_hash": self.tool_output_hash,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class RunTrace:
    variant: str
    events: list[TraceEvent] = field(default_factory=list)

    def append(self, event: TraceEvent) -> None:
        self.events.append(event)


@dataclass
class RunResult:
    case_id: str
    variant: str
    trace: list[TraceEvent]
    final_output: str
    stop_reason: str
    executed: bool = True
    error: str | None = None
    elapsed_ms: float | None = None
    tokens: int | None = None
    cost: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "variant": self.variant,
            "trace": [event.as_dict() for event in self.trace],
            "final_output": self.final_output,
            "stop_reason": self.stop_reason,
            "executed": self.executed,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "tokens": self.tokens,
            "cost": self.cost,
        }


@dataclass
class RunScore:
    case_id: str
    variant: str
    passed: bool
    loop_triggered: bool
    max_step_aborted: bool
    repeated_tool_call_rate: float
    total_steps: int
    max_same_tool_calls: int
    max_no_state_change_steps: int
    stop_reason: str
    triggered_by_steps: bool = False
    triggered_by_tool: bool = False
    triggered_by_state: bool = False
    task_success: bool = False


@dataclass
class VariantReport:
    variant: str
    case_count: int
    passed: int
    pass_rate: float
    failed: int
    failure_rate: float
    error_count: int
    error_rate: float
    timeout_count: int
    timeout_rate: float
    loop_count: int
    loop_rate: float
    max_step_abort_rate: float
    repeated_tool_call_rate: float
    failed_ids: list[str]
    loop_ids: list[str]
    stop_reasons: dict[str, int]


@dataclass
class ABReport:
    variant_results: dict[str, VariantReport]
    cases: list[dict[str, Any]]
    source: str
    evidentiary: bool
    notes: str = ""

    @property
    def variants(self) -> dict[str, dict[str, Any]]:
        return {
            variant: {
                "overall": {
                    "case_count": report.case_count,
                    "passed": report.passed,
                    "pass_rate": report.pass_rate,
                    "failed": report.failed,
                    "failure_rate": report.failure_rate,
                    "error_count": report.error_count,
                    "error_rate": report.error_rate,
                    "timeout_count": report.timeout_count,
                    "timeout_rate": report.timeout_rate,
                    "loop_count": report.loop_count,
                    "loop_rate": report.loop_rate,
                    "max_step_abort_rate": report.max_step_abort_rate,
                    "repeated_tool_call_rate": report.repeated_tool_call_rate,
                },
                "failed_ids": report.failed_ids,
                "loop_ids": report.loop_ids,
                "stop_reasons": report.stop_reasons,
            }
            for variant, report in self.variant_results.items()
        }

    @property
    def delta(self) -> dict[str, float]:
        graph = self.variant_results.get(VARIANT_LANGGRAPH)
        react = self.variant_results.get(VARIANT_REACT)
        if not graph or not react:
            return {"pass_rate": 0.0, "loop_rate": 0.0}
        return {
            "pass_rate": round(graph.pass_rate - react.pass_rate, 4),
            "loop_rate": round(graph.loop_rate - react.loop_rate, 4),
        }

    @property
    def pairwise_deltas(self) -> dict[str, dict[str, float]]:
        deltas: dict[str, dict[str, float]] = {}
        for left_name, right_name in PAIRWISE_COMPARISONS:
            left = self.variant_results.get(left_name)
            right = self.variant_results.get(right_name)
            if not left or not right:
                continue
            deltas[f"{left_name}__vs__{right_name}"] = {
                "pass_rate": round(left.pass_rate - right.pass_rate, 4),
                "loop_rate": round(left.loop_rate - right.loop_rate, 4),
            }
        return deltas

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "evidentiary": self.evidentiary,
            "notes": self.notes,
            "variants": self.variants,
            "delta": self.delta,
            "pairwise_deltas": self.pairwise_deltas,
            "cases": self.cases,
        }


class ModelAdapter(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        ...


class ToolAdapter(Protocol):
    def openai_tools(self) -> list[dict[str, Any]]:
        ...

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        ...


class AgentVariantRunner(Protocol):
    variant: str

    async def run_case(self, case: dict[str, Any]) -> RunResult:
        ...


class DeterministicModelAdapter:
    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.raise_on_call: Exception | None = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"role": role, "messages": messages, "tools": tools, "system": system})
        if self.raise_on_call:
            raise self.raise_on_call
        if self.script:
            item = self.script.pop(0)
            return {
                "content": item.get("content", ""),
                "tool_calls": item.get("tool_calls"),
                "error": item.get("error"),
            }
        return {"content": "deterministic final answer", "tool_calls": None}


class DeterministicToolAdapter:
    def __init__(self, outputs: dict[str, str], available_tools: set[str] | None = None) -> None:
        self.outputs = outputs
        self.available_tools = available_tools or {
            "rag_search",
            "pdf_parser",
            "calculator",
            "local_file_read",
            "web_search",
        }
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_case(cls, case: dict[str, Any], repo_root: Path) -> "DeterministicToolAdapter":
        chunks = []
        for source in case.get("available_sources", []):
            if source.get("type") == "none":
                continue
            path = repo_root / source.get("path", "")
            if path.exists():
                chunks.append(path.read_text(encoding="utf-8")[:800])
        output = "\n\n".join(chunks) or "no local source output"
        return cls(
            outputs={
                "rag_search": output,
                "pdf_parser": output,
                "local_file_read": output,
                "web_search": output,
                "calculator": "42",
            }
        )

    def openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Deterministic {name}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in sorted(self.available_tools)
        ]

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        if tool_name not in self.available_tools or tool_name not in self.outputs:
            raise RuntimeError(f"tool not available: {tool_name}")
        return self.outputs[tool_name]


class LiveModelAdapter:
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        from app.llm.wrapper import call_llm

        return await call_llm(
            messages=messages,
            system=system,
            tools=tools,
            role=role,
            temperature=0,
        )


class LiveToolAdapter:
    def __init__(self, registry: Any, tools: list[Any]) -> None:
        self.registry = registry
        self.tools = tools

    @classmethod
    async def create(cls, registry: Any | None = None) -> "LiveToolAdapter":
        if registry is None:
            from app.infrastructure.setup import tool_registry

            registry = tool_registry
        await registry.initialize()
        return cls(registry=registry, tools=await registry.get_all_tools())

    def openai_tools(self) -> list[dict[str, Any]]:
        from app.llm.wrapper import mcp_tools_to_openai_tools

        return mcp_tools_to_openai_tools(self.tools)

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        result = await self.registry.execute_tool(tool_name, arguments)
        content = getattr(result, "content", result)
        if isinstance(content, str):
            return content
        return _stable_json(_jsonable(content))

    async def close(self) -> None:
        cleanup = getattr(self.registry, "cleanup", None)
        if cleanup is not None:
            await cleanup()


def _tool_key(event: TraceEvent) -> str | None:
    if not event.tool_name:
        return None
    return f"{event.tool_name}:{_stable_json(event.tool_arguments or {})}"


def _parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if not raw_args:
        return {}
    try:
        return json.loads(raw_args)
    except json.JSONDecodeError:
        return {"raw": str(raw_args)}


def _control_stop_from_content(content: str) -> str | None:
    if not content:
        return None
    clean = content.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("human_input_required") or parsed.get("cannot_complete"):
        return STOP_HUMAN_INPUT
    if parsed.get("controlled_stop"):
        return STOP_CONTROLLED
    return None


def _tool_calls_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    return list(response.get("tool_calls") or [])


def _task_success(case: dict[str, Any], final_output: str, stop_reason: str) -> bool:
    if stop_reason == STOP_RUNTIME_ERROR:
        return False
    if stop_reason == STOP_TIMEOUT:
        return False
    if case["category"] == "tool_failure":
        return stop_reason == STOP_TOOL_FAILURE
    if case["category"] == "hitl_safety":
        return stop_reason in {STOP_HUMAN_INPUT, STOP_COMPLETED}
    if stop_reason in {STOP_TOOL_FAILURE, STOP_LOOP_DETECTED, STOP_MAX_STEPS}:
        return False
    if case["category"] == "loop_stability":
        return stop_reason in {STOP_CONTROLLED, STOP_COMPLETED}
    return bool((final_output or "").strip()) and stop_reason == STOP_COMPLETED


def score_run(
    case: dict[str, Any],
    trace: list[TraceEvent],
    final_output: str,
    stop_reason: str,
    variant: str | None = None,
) -> RunScore:
    loop_rules = case["loop_rules"]
    tool_counts: Counter[str] = Counter()
    no_change_run = 0
    max_no_change = 0
    repeated_tool_events = 0

    for event in trace:
        key = _tool_key(event)
        if key:
            tool_counts[key] += 1
            if tool_counts[key] > 1:
                repeated_tool_events += 1
        if event.state_changed:
            no_change_run = 0
        else:
            no_change_run += 1
            max_no_change = max(max_no_change, no_change_run)

    total_steps = len(trace)
    max_same_tool_calls = max(tool_counts.values(), default=0)
    tool_event_count = sum(tool_counts.values())
    repeated_tool_call_rate = (
        round(repeated_tool_events / tool_event_count, 4) if tool_event_count else 0.0
    )
    triggered_by_steps = total_steps > loop_rules["max_total_steps"]
    triggered_by_tool = max_same_tool_calls > loop_rules["max_same_tool_calls"]
    triggered_by_state = max_no_change > loop_rules["max_no_state_change_steps"]
    max_step_aborted = stop_reason == STOP_MAX_STEPS or triggered_by_steps
    loop_triggered = (
        stop_reason == STOP_LOOP_DETECTED
        or triggered_by_tool
        or triggered_by_state
        or max_step_aborted
    )
    task_success = _task_success(case, final_output, stop_reason)
    return RunScore(
        case_id=case["id"],
        variant=variant or (trace[0].variant if trace else ""),
        passed=task_success and not loop_triggered,
        loop_triggered=loop_triggered,
        max_step_aborted=max_step_aborted,
        repeated_tool_call_rate=repeated_tool_call_rate,
        total_steps=total_steps,
        max_same_tool_calls=max_same_tool_calls,
        max_no_state_change_steps=max_no_change,
        stop_reason=stop_reason,
        triggered_by_steps=triggered_by_steps,
        triggered_by_tool=triggered_by_tool,
        triggered_by_state=triggered_by_state,
        task_success=task_success,
    )


class BaseRunner:
    variant: str

    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_adapter: ToolAdapter,
        *,
        max_steps: int | None = None,
    ) -> None:
        self.model_adapter = model_adapter
        self.tool_adapter = tool_adapter
        self.max_steps = max_steps

    def _event(
        self,
        *,
        trace: list[TraceEvent],
        action: str,
        before: dict[str, Any],
        after: dict[str, Any],
        tool_name: str | None = None,
        tool_arguments: dict[str, Any] | None = None,
        tool_output: str | None = None,
        error: str | None = None,
        stop_reason: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        trace.append(
            TraceEvent(
                variant=self.variant,
                step=len(trace) + 1,
                action=action,
                state_before=_state_fingerprint(before),
                state_after=_state_fingerprint(after),
                state_diff=_state_diff(before, after),
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                tool_output=tool_output,
                error=error,
                stop_reason=stop_reason,
                elapsed_ms=elapsed_ms,
            )
        )

    def _budget(self, case: dict[str, Any]) -> int:
        return self.max_steps or case["loop_rules"]["max_total_steps"]


def _merge_unique(left: list[Any], right: list[Any]) -> list[Any]:
    merged = list(left or [])
    seen = {_stable_json(item) for item in _jsonable(merged)}
    for item in right or []:
        key = _stable_json(_jsonable(item))
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _apply_graph_update(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(state)
    for key, value in update.items():
        if key == "messages":
            merged[key] = list(merged.get(key) or []) + list(value or [])
        elif key in {"tasks", "task_results"}:
            current = dict(merged.get(key) or {})
            current.update(value or {})
            merged[key] = current
        elif key == "tool_history":
            merged[key] = list(merged.get(key) or []) + list(value or [])
        elif key in {"ready_tasks", "running_tasks"}:
            merged[key] = _merge_unique(list(merged.get(key) or []), list(value or []))
        else:
            merged[key] = value
    return merged


def _task_status(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("status", ""))
    return str(getattr(task, "status", ""))


def _task_error(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("error", "") or "")
    return str(getattr(task, "error", "") or "")


def _task_value(task: Any, key: str, default: Any = None) -> Any:
    if isinstance(task, dict):
        return task.get(key, default)
    return getattr(task, key, default)


def _set_task_value(task: Any, key: str, value: Any) -> None:
    if isinstance(task, dict):
        task[key] = value
    else:
        setattr(task, key, value)


def _compute_newly_ready(tasks: dict[str, Any], completed_task_id: str) -> list[str]:
    newly_ready: list[str] = []
    for task_id, task in tasks.items():
        if _task_status(task) != "pending":
            continue
        dependencies = _task_value(task, "dependencies", []) or []
        if completed_task_id not in dependencies:
            continue
        if all(_task_status(tasks.get(dep)) == "completed" for dep in dependencies):
            newly_ready.append(task_id)
    return newly_ready


def _graph_stop_reason(case: dict[str, Any], state: dict[str, Any], budget_stop: str | None) -> str:
    if budget_stop:
        return budget_stop
    tasks = state.get("tasks") or {}
    statuses = [_task_status(task) for task in tasks.values()]
    errors = [_task_error(task) for task in tasks.values()]
    if "suspended" in statuses or case["category"] == "hitl_safety":
        return STOP_HUMAN_INPUT
    if "failed" in statuses:
        if any(STOP_LOOP_DETECTED in error for error in errors):
            return STOP_LOOP_DETECTED
        if any(STOP_MAX_STEPS in error for error in errors):
            return STOP_MAX_STEPS
        if any("[需要补充信息]" in error for error in errors):
            return STOP_HUMAN_INPUT
        return STOP_TOOL_FAILURE
    if case["category"] == "loop_stability":
        return STOP_CONTROLLED
    return STOP_COMPLETED


def _loop_abort_reason(case: dict[str, Any], trace: list[TraceEvent]) -> str | None:
    rules = case["loop_rules"]
    if len(trace) >= rules["max_total_steps"]:
        return STOP_MAX_STEPS

    tool_counts: Counter[str] = Counter()
    no_change_run = 0
    max_no_change = 0
    for event in trace:
        key = _tool_key(event)
        if key:
            tool_counts[key] += 1
        if event.state_changed:
            no_change_run = 0
        else:
            no_change_run += 1
            max_no_change = max(max_no_change, no_change_run)

    if max(tool_counts.values(), default=0) > rules["max_same_tool_calls"]:
        return STOP_LOOP_DETECTED
    if max_no_change > rules["max_no_state_change_steps"]:
        return STOP_LOOP_DETECTED
    return None


class _AdapterToolRegistry:
    def __init__(self, tool_adapter: ToolAdapter) -> None:
        self.tool_adapter = tool_adapter

    async def get_all_tools(self) -> list[Any]:
        tools = []
        for item in self.tool_adapter.openai_tools():
            function = item.get("function", {})
            tools.append(
                SimpleNamespace(
                    name=function.get("name", ""),
                    description=function.get("description", ""),
                    inputSchema=function.get("parameters") or {"type": "object", "properties": {}},
                )
            )
        return tools

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        return SimpleNamespace(content=await self.tool_adapter.execute(tool_name, arguments))


@contextmanager
def _patched_graph_runtime(
    model_adapter: ModelAdapter,
    tool_adapter: ToolAdapter,
    *,
    worker_node_override: Any | None = None,
):
    import app.graph.build_graph as build_graph_module
    import app.graph.nodes.controller as controller_module
    import app.graph.nodes.planner as planner_module
    import app.graph.nodes.reviewer as reviewer_module
    import app.graph.nodes.simple_chat as simple_chat_module
    import app.graph.nodes.worker as worker_module

    async def patched_call_llm(
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        role: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        return await model_adapter.chat(
            messages,
            role=role or "graph",
            tools=tools,
            system=system,
        )

    async def patched_call_llm_stream(
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        role: str | None = None,
        **_: Any,
    ):
        response = await model_adapter.chat(
            messages,
            role=role or "graph_stream",
            tools=tools,
            system=system,
        )
        content = response.get("content") or ""
        if content:
            yield {"content": content, "thinking": "", "done": False}
        yield {
            "content": "",
            "thinking": "",
            "done": True,
            "tool_calls": response.get("tool_calls"),
            "error": response.get("error"),
        }

    patches = [
        (controller_module, "call_llm", patched_call_llm),
        (planner_module, "call_llm", patched_call_llm),
        (worker_module, "call_llm", patched_call_llm),
        (worker_module, "call_llm_stream", patched_call_llm_stream),
        (simple_chat_module, "call_llm_stream", patched_call_llm_stream),
        (reviewer_module, "call_llm_stream", patched_call_llm_stream),
        (worker_module, "tool_registry", _AdapterToolRegistry(tool_adapter)),
    ]
    if worker_node_override is not None:
        patches.append((build_graph_module, "worker_node", worker_node_override))
    originals = [(module, name, getattr(module, name)) for module, name, _ in patches]
    try:
        for module, name, value in patches:
            setattr(module, name, value)
        yield
    finally:
        for module, name, value in originals:
            setattr(module, name, value)


class LangGraphVariantRunner(BaseRunner):
    variant = VARIANT_LANGGRAPH

    def _worker_node_override(self, case: dict[str, Any]) -> Any | None:
        return None

    async def run_case(self, case: dict[str, Any]) -> RunResult:
        from app.graph.build_graph import build_graph

        start = time.perf_counter()
        trace: list[TraceEvent] = []
        state: dict[str, Any] = {
            "messages": [{"role": "user", "content": case["user_query"]}],
            "user_input": case["user_query"],
            "thread_id": f"agent-ab-{case['id']}",
            "next_action": "",
            "tasks": {},
            "tool_history": [],
            "task_results": {},
            "ready_tasks": [],
            "running_tasks": [],
            "final_report": "",
        }
        budget_stop: str | None = None
        emitted_tool_signatures: set[str] = set()

        try:
            with _patched_graph_runtime(
                self.model_adapter,
                self.tool_adapter,
                worker_node_override=self._worker_node_override(case),
            ):
                graph = build_graph().compile()
                async for step in graph.astream(
                    state,
                    config={
                        "run_name": "agent_ab_langgraph_case",
                        "tags": ["agent-ab-evaluation", f"case:{case['id']}"],
                        "metadata": {"case_id": case["id"], "variant": self.variant},
                        "recursion_limit": max(self._budget(case) + 4, 8),
                        "configurable": {
                            "thread_id": "",
                            "stream_queue": None,
                            "hitl_pending": {},
                        },
                    },
                ):
                    for node_name, node_output in step.items():
                        if node_name == "__start__" or not isinstance(node_output, dict):
                            continue
                        before = _state_snapshot(state)
                        state = _apply_graph_update(state, node_output)
                        after = _state_snapshot(state)
                        self._event(trace=trace, action=node_name, before=before, after=after)

                        tool_history = node_output.get("tool_history") or []
                        for occurrence, tool_call in enumerate(tool_history):
                            if not isinstance(tool_call, dict):
                                tool_call = dict(tool_call)
                            signature = f"{node_name}:{occurrence}:{_stable_json(tool_call)}"
                            if signature in emitted_tool_signatures:
                                continue
                            emitted_tool_signatures.add(signature)
                            arguments = _parse_tool_arguments(tool_call.get("arguments", "{}"))
                            self._event(
                                trace=trace,
                                action=f"{node_name}_tool",
                                before=before,
                                after=after,
                                tool_name=tool_call.get("tool_name"),
                                tool_arguments=arguments,
                                tool_output=tool_call.get("output"),
                                error=tool_call.get("error"),
                            )

                        budget_stop = _loop_abort_reason(case, trace)
                        if budget_stop:
                            break
                    if budget_stop:
                        break

            stop_reason = _graph_stop_reason(case, state, budget_stop)
            if trace:
                trace[-1].stop_reason = stop_reason
            return self._result(case, trace, state, stop_reason, start)
        except Exception as exc:
            if not trace:
                self._event(
                    trace=trace,
                    action="runtime_error",
                    before=state,
                    after=state,
                    error=str(exc),
                    stop_reason=STOP_RUNTIME_ERROR,
                )
            return self._result(case, trace, state, STOP_RUNTIME_ERROR, start, error=str(exc))

    def _result(
        self,
        case: dict[str, Any],
        trace: list[TraceEvent],
        state: dict[str, Any],
        stop_reason: str,
        start: float,
        error: str | None = None,
    ) -> RunResult:
        return RunResult(
            case_id=case["id"],
            variant=self.variant,
            trace=trace,
            final_output=state.get("final_report", ""),
            stop_reason=stop_reason,
            error=error,
            elapsed_ms=round((time.perf_counter() - start) * 1000, 3),
        )


class LangGraphReactWorkerRunner(LangGraphVariantRunner):
    variant = VARIANT_LANGGRAPH_REACT_WORKER

    def _worker_node_override(self, case: dict[str, Any]) -> Any:
        async def react_worker_node(state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
            tasks = state.get("tasks") or {}
            task_id = state.get("current_task_id") or next(iter(tasks), "")
            if not task_id or task_id not in tasks:
                return {}

            task = tasks[task_id]
            status = _task_status(task)
            if status == "completed":
                return {}
            if status == "pending":
                _set_task_value(task, "status", "running")
            elif status != "running":
                return {}

            task_description = str(_task_value(task, "description", case["user_query"]) or case["user_query"])
            messages: list[dict[str, Any]] = [
                {
                    "role": "user",
                    "content": (
                        f"Original user query:\n{case['user_query']}\n\n"
                        f"Planner task {task_id}:\n{task_description}"
                    ),
                }
            ]
            collected_tool_calls: list[dict[str, Any]] = []

            try:
                for _ in range(self._budget(case)):
                    response = await self.model_adapter.chat(
                        messages,
                        role="react_worker",
                        tools=self.tool_adapter.openai_tools(),
                        system=REACT_WORKER_SYSTEM_PROMPT,
                    )
                    if response.get("error"):
                        raise ValueError(response["error"])

                    tool_calls = _tool_calls_from_response(response)
                    content = response.get("content", "") or ""
                    if not tool_calls:
                        stop_signal = _control_stop_from_content(content)
                        if stop_signal == STOP_HUMAN_INPUT:
                            _set_task_value(task, "status", "suspended")
                            _set_task_value(task, "error", content)
                            return {
                                "current_task_id": task_id,
                                "tasks": {task_id: task},
                                "tool_history": collected_tool_calls,
                                "messages": [{"role": "assistant", "content": content}],
                                "final_report": content,
                            }

                        _set_task_value(task, "result", content)
                        _set_task_value(task, "status", "completed")
                        return {
                            "current_task_id": task_id,
                            "tasks": {task_id: task},
                            "tool_history": collected_tool_calls,
                            "task_results": {task_id: content},
                            "ready_tasks": _compute_newly_ready(tasks, task_id),
                        }

                    messages.append(
                        {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": tool_calls,
                        }
                    )
                    for tool_call in tool_calls:
                        name = tool_call.get("function", {}).get("name", "")
                        arguments = _parse_tool_arguments(
                            tool_call.get("function", {}).get("arguments", "{}")
                        )
                        try:
                            output = await self.tool_adapter.execute(name, arguments)
                        except Exception as exc:
                            collected_tool_calls.append(
                                {
                                    "task_id": task_id,
                                    "tool_name": name,
                                    "arguments": json.dumps(arguments, ensure_ascii=False),
                                    "output": "",
                                    "error": str(exc),
                                }
                            )
                            _set_task_value(task, "status", "failed")
                            _set_task_value(task, "error", str(exc))
                            return {
                                "current_task_id": task_id,
                                "tasks": {task_id: task},
                                "tool_history": collected_tool_calls,
                            }

                        collected_tool_calls.append(
                            {
                                "task_id": task_id,
                                "tool_name": name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                                "output": output,
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id", ""),
                                "content": output,
                            }
                        )

                _set_task_value(task, "status", "failed")
                _set_task_value(task, "error", STOP_MAX_STEPS)
                return {
                    "current_task_id": task_id,
                    "tasks": {task_id: task},
                    "tool_history": collected_tool_calls,
                }
            except Exception as exc:
                _set_task_value(task, "status", "failed")
                _set_task_value(task, "error", str(exc))
                return {"current_task_id": task_id, "tasks": {task_id: task}}

        react_worker_node.__annotations__["config"] = RunnableConfig
        return react_worker_node


class LinearReActRunner(BaseRunner):
    variant = VARIANT_REACT

    async def run_case(self, case: dict[str, Any]) -> RunResult:
        start = time.perf_counter()
        trace: list[TraceEvent] = []
        state: dict[str, Any] = {
            "messages": [{"role": "user", "content": case["user_query"]}],
            "observations": [],
            "final_output": "",
        }
        try:
            for _ in range(self._budget(case)):
                before = _state_snapshot(state)
                response = await self.model_adapter.chat(
                    state["messages"],
                    role="react",
                    tools=self.tool_adapter.openai_tools(),
                    system=REACT_SYSTEM_PROMPT,
                )
                tool_calls = _tool_calls_from_response(response)
                if not tool_calls:
                    state["final_output"] = response.get("content", "")
                    stop_reason = _control_stop_from_content(state["final_output"]) or STOP_COMPLETED
                    self._event(
                        trace=trace,
                        action="react_final",
                        before=before,
                        after=_state_snapshot(state),
                        stop_reason=stop_reason,
                    )
                    return self._result(case, trace, state, stop_reason, start)

                for tc in tool_calls:
                    name = tc.get("function", {}).get("name", "")
                    arguments = _parse_tool_arguments(tc.get("function", {}).get("arguments", "{}"))
                    try:
                        output = await self.tool_adapter.execute(name, arguments)
                    except Exception as exc:
                        self._event(
                            trace=trace,
                            action="react_tool",
                            before=before,
                            after=_state_snapshot(state),
                            tool_name=name,
                            tool_arguments=arguments,
                            error=str(exc),
                            stop_reason=STOP_TOOL_FAILURE,
                        )
                        return self._result(case, trace, state, STOP_TOOL_FAILURE, start, error=str(exc))
                    state["observations"].append({"tool_name": name, "arguments": arguments, "output": output})
                    state["messages"].append({"role": "tool", "content": output})
                    self._event(
                        trace=trace,
                        action="react_tool",
                        before=before,
                        after=_state_snapshot(state),
                        tool_name=name,
                        tool_arguments=arguments,
                        tool_output=output,
                    )
                    abort_reason = _loop_abort_reason(case, trace)
                    if abort_reason:
                        trace[-1].stop_reason = abort_reason
                        return self._result(case, trace, state, abort_reason, start)
            if trace:
                trace[-1].stop_reason = STOP_MAX_STEPS
            return self._result(case, trace, state, STOP_MAX_STEPS, start)
        except Exception as exc:
            self._event(
                trace=trace,
                action="runtime_error",
                before=_state_snapshot(state),
                after=_state_snapshot(state),
                error=str(exc),
                stop_reason=STOP_RUNTIME_ERROR,
            )
            return self._result(case, trace, state, STOP_RUNTIME_ERROR, start, error=str(exc))

    def _result(
        self,
        case: dict[str, Any],
        trace: list[TraceEvent],
        state: dict[str, Any],
        stop_reason: str,
        start: float,
        error: str | None = None,
    ) -> RunResult:
        return RunResult(
            case_id=case["id"],
            variant=self.variant,
            trace=trace,
            final_output=state.get("final_output", ""),
            stop_reason=stop_reason,
            error=error,
            elapsed_ms=round((time.perf_counter() - start) * 1000, 3),
        )


REACT_SYSTEM_PROMPT = (
    "You are a linear ReAct baseline. Repeatedly decide whether to call one tool "
    "or produce a final answer. Do not use LangGraph planner or reviewer nodes."
)

REACT_WORKER_SYSTEM_PROMPT = (
    "You are the worker node inside a LangGraph evaluation ablation. Use ReAct-style "
    "tool calls to complete only the planner task, then return the task result."
)


def _coerce_run_result(item: RunResult | dict[str, Any]) -> RunResult:
    if isinstance(item, RunResult):
        return item
    event_fields = set(TraceEvent.__dataclass_fields__)
    trace = [
        event
        if isinstance(event, TraceEvent)
        else TraceEvent(**{key: value for key, value in event.items() if key in event_fields})
        for event in item.get("trace", [])
    ]
    return RunResult(
        case_id=item["case"]["id"],
        variant=item["variant"],
        trace=trace,
        final_output=item.get("final_output", ""),
        stop_reason=item.get("stop_reason", STOP_RUNTIME_ERROR),
        executed=item.get("executed", True),
        error=item.get("error"),
    )


def _variant_report(variant: str, scores: list[RunScore]) -> VariantReport:
    case_count = len(scores)
    passed = sum(1 for score in scores if score.passed)
    loops = [score.case_id for score in scores if score.loop_triggered]
    failed = [score.case_id for score in scores if not score.passed]
    error_stop_reasons = {STOP_RUNTIME_ERROR, STOP_TOOL_FAILURE, STOP_TIMEOUT}
    error_count = sum(1 for score in scores if score.stop_reason in error_stop_reasons)
    timeout_count = sum(1 for score in scores if score.stop_reason == STOP_TIMEOUT)
    max_step_aborts = sum(1 for score in scores if score.max_step_aborted)
    repeated_tool_rate = (
        round(sum(score.repeated_tool_call_rate for score in scores) / case_count, 4)
        if case_count
        else 0.0
    )
    return VariantReport(
        variant=variant,
        case_count=case_count,
        passed=passed,
        pass_rate=round(passed / case_count, 4) if case_count else 0.0,
        failed=len(failed),
        failure_rate=round(len(failed) / case_count, 4) if case_count else 0.0,
        error_count=error_count,
        error_rate=round(error_count / case_count, 4) if case_count else 0.0,
        timeout_count=timeout_count,
        timeout_rate=round(timeout_count / case_count, 4) if case_count else 0.0,
        loop_count=len(loops),
        loop_rate=round(len(loops) / case_count, 4) if case_count else 0.0,
        max_step_abort_rate=round(max_step_aborts / case_count, 4) if case_count else 0.0,
        repeated_tool_call_rate=repeated_tool_rate,
        failed_ids=failed,
        loop_ids=loops,
        stop_reasons=dict(Counter(score.stop_reason for score in scores)),
    )


def build_ab_report(
    run_results: list[RunResult | dict[str, Any]],
    *,
    source: str = REPORT_SOURCE_DETERMINISTIC,
    formal: bool = False,
) -> ABReport:
    coerced = [_coerce_run_result(item) for item in run_results]
    cases_by_id = {
        item["case"]["id"]: item["case"]
        for item in run_results
        if isinstance(item, dict) and "case" in item
    }
    missing = set(VARIANTS) - {result.variant for result in coerced}
    if missing:
        raise ValueError(f"missing variant results: {sorted(missing)}")

    result_variants = {result.variant for result in coerced}
    observed_variants = [variant for variant in ALL_VARIANTS if variant in result_variants]
    observed_variants.extend(sorted(result_variants - set(ALL_VARIANTS)))
    scores_by_variant: dict[str, list[RunScore]] = {variant: [] for variant in observed_variants}
    case_rows: list[dict[str, Any]] = []
    for item, result in zip(run_results, coerced):
        case = item["case"] if isinstance(item, dict) and "case" in item else cases_by_id[result.case_id]
        score = score_run(case, result.trace, result.final_output, result.stop_reason, result.variant)
        scores_by_variant[result.variant].append(score)
        case_rows.append(
            {
                "case_id": result.case_id,
                "variant": result.variant,
                "executed": result.executed,
                "score": score.__dict__,
                "trace": [event.as_dict() for event in result.trace],
                "stop_reason": result.stop_reason,
                "error": result.error,
            }
        )

    base_evidentiary = source != REPORT_SOURCE_SIMULATED and all(result.executed for result in coerced)
    unreviewed_case_ids = sorted(
        {
            item["case"]["id"]
            for item in run_results
            if isinstance(item, dict) and "case" in item and not is_formal_case(item["case"])
        }
    )
    evidentiary = base_evidentiary and (not formal or not unreviewed_case_ids)
    notes_parts: list[str] = []
    if not base_evidentiary:
        notes_parts.append("non-evidentiary: simulated or non-executed traces cannot prove loop behavior")
    if formal and unreviewed_case_ids:
        notes_parts.append(
            "non-evidentiary: unreviewed or low-confidence cases cannot support formal claims: "
            + ", ".join(unreviewed_case_ids)
        )
    return ABReport(
        variant_results={
            variant: _variant_report(variant, scores_by_variant[variant])
            for variant in observed_variants
        },
        cases=case_rows,
        source=source,
        evidentiary=evidentiary,
        notes="; ".join(notes_parts),
    )


async def run_paired_evaluation(
    cases: list[dict[str, Any]],
    runners: dict[str, AgentVariantRunner],
    *,
    source: str = REPORT_SOURCE_DETERMINISTIC,
    formal: bool = False,
) -> ABReport:
    missing = set(VARIANTS) - set(runners)
    if missing:
        raise ValueError(f"missing runners: {sorted(missing)}")
    variant_order = [variant for variant in ALL_VARIANTS if variant in runners]
    variant_order.extend(sorted(set(runners) - set(ALL_VARIANTS)))
    run_rows: list[dict[str, Any]] = []
    for case in cases:
        for variant in variant_order:
            result = await runners[variant].run_case(case)
            run_rows.append({"case": case, **result.as_dict()})
    return build_ab_report(run_rows, source=source, formal=formal)
