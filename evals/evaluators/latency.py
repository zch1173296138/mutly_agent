from __future__ import annotations

from typing import Any


def evaluate_latency(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    max_wall_time = case.get("max_wall_time_sec")
    elapsed = run.get("wall_time_sec")
    if elapsed is None and run.get("elapsed_ms") is not None:
        elapsed = float(run["elapsed_ms"]) / 1000.0
    if max_wall_time is None:
        return {
            "name": "latency",
            "passed": True,
            "case_id": case.get("id"),
            "wall_time_sec": elapsed,
            "max_wall_time_sec": None,
        }
    elapsed = float(elapsed or 0.0)
    return {
        "name": "latency",
        "passed": elapsed <= float(max_wall_time),
        "case_id": case.get("id"),
        "wall_time_sec": elapsed,
        "max_wall_time_sec": float(max_wall_time),
    }

