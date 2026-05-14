from __future__ import annotations

from typing import Any

from evals.evaluators.common import event_node, trace_events


def evaluate_node_path(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    observed = [node for event in trace_events(run) if (node := event_node(event))]
    observed_set = set(observed)
    expected = list(case.get("expected_nodes") or [])
    forbidden = list(case.get("forbidden_nodes") or case.get("disallowed_nodes") or [])
    missing = [node for node in expected if node not in observed_set]
    unexpected = [node for node in forbidden if node in observed_set]
    return {
        "name": "node_path",
        "passed": not missing and not unexpected,
        "case_id": case.get("id"),
        "observed_nodes": observed,
        "missing_nodes": missing,
        "unexpected_nodes": unexpected,
    }

