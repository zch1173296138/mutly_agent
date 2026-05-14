from __future__ import annotations

import json
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "suspended", "END"}


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def trace_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    return list(run.get("trace") or run.get("events") or [])


def event_node(event: dict[str, Any]) -> str | None:
    node = event.get("node")
    if node:
        return str(node)
    action = event.get("action")
    if not action:
        return None
    action = str(action)
    if action.endswith("_tool"):
        return action.removesuffix("_tool")
    return action


def event_state_changed(event: dict[str, Any]) -> bool:
    if "state_changed" in event:
        return bool(event["state_changed"])
    if "state_before" in event and "state_after" in event:
        return event["state_before"] != event["state_after"]
    diff = event.get("state_diff")
    if isinstance(diff, dict):
        return bool(diff.get("added") or diff.get("removed") or diff.get("changed"))
    return True

