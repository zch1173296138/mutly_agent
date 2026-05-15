import json
import logging
import re
from typing import Any, Dict

from app.graph.state import AgentState, TaskNode
from app.llm.prompt_manager import render
from app.llm.wrapper import call_llm

logger = logging.getLogger(__name__)


class PlannerParseError(ValueError):
    pass


def _strip_markdown_code_block(content: str) -> str:
    clean = content.strip()
    match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", clean, flags=re.IGNORECASE)
    return match.group(1).strip() if match else clean


def _extract_first_json_value(content: str) -> Any:
    clean = _strip_markdown_code_block(content)
    decoder = json.JSONDecoder()
    first_error: json.JSONDecodeError | None = None
    for index, char in enumerate(clean):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(clean[index:])
            return value
        except json.JSONDecodeError as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise PlannerParseError(str(first_error)) from first_error
    raise PlannerParseError("no JSON array or object found in planner response")


def _coerce_task_list(value: Any) -> list[Any]:
    if isinstance(value, dict):
        value = value.get("tasks")
    if not isinstance(value, list):
        raise PlannerParseError("planner response must be a JSON array or an object with a tasks array")
    return value


def _validate_task(item: Any, index: int) -> TaskNode:
    if not isinstance(item, dict):
        raise PlannerParseError(f"task at index {index} must be an object")

    task_id = item.get("task_id")
    description = item.get("description")
    dependencies = item.get("dependencies", [])

    if not isinstance(task_id, str) or not task_id.strip():
        raise PlannerParseError(f"task at index {index} has invalid task_id")
    if not isinstance(description, str) or not description.strip():
        raise PlannerParseError(f"task {task_id} has invalid description")
    if dependencies is None:
        dependencies = []
    if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies):
        raise PlannerParseError(f"task {task_id} dependencies must be a list of strings")

    status = item.get("status", "pending")
    if not isinstance(status, str) or not status.strip():
        status = "pending"

    return TaskNode(
        task_id=task_id.strip(),
        description=description.strip(),
        dependencies=dependencies,
        status=status,
    )


def _parse_tasks(content: str) -> Dict[str, TaskNode]:
    """Parse planner output into task_id -> TaskNode."""
    task_list = _coerce_task_list(_extract_first_json_value(content))
    tasks: Dict[str, TaskNode] = {}
    for index, item in enumerate(task_list):
        task = _validate_task(item, index)
        if task.task_id in tasks:
            raise PlannerParseError(f"duplicate task_id: {task.task_id}")
        tasks[task.task_id] = task

    if not tasks:
        raise PlannerParseError("planner response contains no tasks")

    missing_dependencies = sorted(
        {dep for task in tasks.values() for dep in task.dependencies if dep not in tasks}
    )
    if missing_dependencies:
        raise PlannerParseError(f"dependencies reference unknown task_id(s): {missing_dependencies}")
    return tasks


async def _repair_tasks_json(raw_content: str, parse_error: Exception) -> Dict[str, TaskNode]:
    repair_prompt = (
        "The planner response below failed to parse as the required task JSON.\n"
        "Return only a JSON array. Do not include markdown, prose, or code fences.\n"
        "Each task must have task_id, description, and dependencies fields.\n\n"
        f"Parse error: {parse_error}\n\n"
        f"Planner response:\n{raw_content}"
    )
    repaired = await call_llm(
        messages=[{"role": "user", "content": repair_prompt}],
        system="You repair malformed planner JSON. Return only a JSON array.",
        role="planner",
        temperature=0,
    )
    if repaired.get("error"):
        raise PlannerParseError(f"repair failed: {repaired['error']}")
    try:
        return _parse_tasks(repaired.get("content", "") or "")
    except Exception as exc:
        raise PlannerParseError(f"repair failed: {exc}") from exc


def _planner_failure(error: Exception | str) -> dict:
    return {
        "tasks": {"__clear__": True},
        "ready_tasks": [],
        "running_tasks": [],
        "planner_error": f"planner_parse_error: {error}",
        "planner_failure": True,
    }


async def planner_node(state: AgentState) -> dict:
    try:
        user_input = state.get("user_input", "")
        if not user_input:
            raise ValueError("user_input is empty; cannot create planner tasks")

        raw_messages = list(state.get("messages") or [])

        def _to_dict(message):
            if isinstance(message, dict):
                return message
            role = {"human": "user", "ai": "assistant"}.get(getattr(message, "type", ""), "user")
            return {"role": role, "content": getattr(message, "content", "")}

        history = [_to_dict(message) for message in raw_messages]
        planner_messages = history[-10:] if history else [{"role": "user", "content": user_input}]

        response = await call_llm(
            messages=planner_messages,
            system=render("planner"),
            role="planner",
            temperature=0.2,
        )
        if response.get("error"):
            raise ValueError(response["error"])

        content = response.get("content", "") or ""
        logger.info("LLM returned planner task list: %s", content)
        try:
            tasks = _parse_tasks(content)
        except Exception as parse_error:
            logger.warning("[Planner] initial task JSON parse failed, attempting repair: %s", parse_error)
            tasks = await _repair_tasks_json(content, parse_error)

        ready_tasks = [task_id for task_id, task in tasks.items() if not task.dependencies]
        logger.info("Parsed %s planner tasks: %s", len(tasks), list(tasks.keys()))
        return {
            "tasks": tasks,
            "ready_tasks": ready_tasks,
            "running_tasks": [],
            "planner_error": None,
            "planner_failure": False,
        }

    except Exception as exc:
        logger.error("[Planner] failed to parse generated task list: %s", exc)
        return _planner_failure(exc)
