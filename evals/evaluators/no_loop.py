from __future__ import annotations

from collections import Counter
from typing import Any

from evals.evaluators.common import event_state_changed, stable_json, trace_events


def evaluate_no_loop(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    events = trace_events(run)
    rules = case.get("loop_rules") or {}
    max_total_steps = int(rules.get("max_total_steps", case.get("max_steps", 999999)))
    max_same_tool_calls = int(rules.get("max_same_tool_calls", 999999))
    max_same_tool_same_args = int(rules.get("max_same_tool_same_args", max_same_tool_calls))
    max_no_state_change_steps = int(rules.get("max_no_state_change_steps", 999999))

    tool_counts: Counter[str] = Counter()
    tool_arg_counts: Counter[str] = Counter()
    no_change_run = 0
    max_no_change_run = 0

    for event in events:
        tool_name = event.get("tool_name")
        if tool_name:
            tool_name = str(tool_name)
            tool_counts[tool_name] += 1
            tool_arg_counts[f"{tool_name}:{stable_json(event.get('tool_arguments') or {})}"] += 1
        if event_state_changed(event):
            no_change_run = 0
        else:
            no_change_run += 1
            max_no_change_run = max(max_no_change_run, no_change_run)

    same_tool_violations = {
        name: count for name, count in tool_counts.items() if count > max_same_tool_calls
    }
    same_args_violations = {
        key: count for key, count in tool_arg_counts.items() if count > max_same_tool_same_args
    }
    violations = {
        "max_total_steps": len(events) if len(events) > max_total_steps else None,
        "same_tool_calls": same_tool_violations,
        "same_tool_same_args": same_args_violations,
        "max_no_state_change_steps": (
            max_no_change_run if max_no_change_run > max_no_state_change_steps else None
        ),
    }
    passed = not any(
        [
            violations["max_total_steps"],
            violations["same_tool_calls"],
            violations["same_tool_same_args"],
            violations["max_no_state_change_steps"],
        ]
    )
    return {
        "name": "no_loop",
        "passed": passed,
        "case_id": case.get("id"),
        "total_steps": len(events),
        "max_no_state_change_run": max_no_change_run,
        "violations": violations,
    }

