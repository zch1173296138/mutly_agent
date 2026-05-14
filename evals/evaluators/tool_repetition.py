from __future__ import annotations

from collections import Counter
from typing import Any

from evals.evaluators.common import stable_json, trace_events


def summarize_tool_repetition(run: dict[str, Any]) -> dict[str, Any]:
    keys: list[str] = []
    tools: list[str] = []
    for event in trace_events(run):
        tool_name = event.get("tool_name")
        if not tool_name:
            continue
        tool_name = str(tool_name)
        tools.append(tool_name)
        keys.append(f"{tool_name}:{stable_json(event.get('tool_arguments') or {})}")

    counts = Counter(keys)
    duplicate_events = sum(max(0, count - 1) for count in counts.values())
    tool_call_count = len(keys)
    return {
        "name": "tool_repetition",
        "tool_call_count": tool_call_count,
        "tool_counts": dict(Counter(tools)),
        "duplicate_tool_call_count": duplicate_events,
        "duplicate_tool_call_ratio": (
            duplicate_events / tool_call_count if tool_call_count else 0.0
        ),
    }

