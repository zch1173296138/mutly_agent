from __future__ import annotations

from typing import Any

from evals.evaluators.common import TERMINAL_STATUSES, trace_events


def evaluate_termination(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    status = run.get("status") or run.get("stop_reason")
    events = trace_events(run)
    if not status and events:
        status = events[-1].get("status") or events[-1].get("stop_reason")
    status = status or "running"
    passed = status in TERMINAL_STATUSES
    return {
        "name": "termination",
        "passed": passed,
        "status": status,
        "allowed_statuses": sorted(TERMINAL_STATUSES),
        "case_id": case.get("id"),
    }

