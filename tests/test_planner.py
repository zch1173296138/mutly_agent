import asyncio

import pytest

from app.graph.nodes import planner as planner_module


def test_parse_tasks_accepts_plain_json_array():
    tasks = planner_module._parse_tasks(
        """[
          {"task_id": "t1", "description": "Collect sources", "dependencies": []}
        ]"""
    )

    assert list(tasks) == ["t1"]
    assert tasks["t1"].description == "Collect sources"


def test_planner_node_success(monkeypatch):
    async def fake_call_llm(**kwargs):
        return {
            "content": """[
                {"task_id": "t1", "description": "Collect reports", "dependencies": []},
                {"task_id": "t2", "description": "Compare results", "dependencies": ["t1"]}
            ]"""
        }

    monkeypatch.setattr(planner_module, "call_llm", fake_call_llm)
    result = asyncio.run(planner_module.planner_node({"user_input": "Split this research task"}))

    assert set(result["tasks"].keys()) == {"t1", "t2"}
    assert result["ready_tasks"] == ["t1"]
    assert result["running_tasks"] == []


def test_planner_node_empty_input_fallback():
    result = asyncio.run(planner_module.planner_node({"user_input": ""}))
    assert result["tasks"] == {"__clear__": True}
    assert result["planner_failure"] is True
    assert "planner_error" in result


def test_parse_tasks_accepts_markdown_json_array():
    tasks = planner_module._parse_tasks(
        """```json
        [
          {"task_id": "t1", "description": "Collect sources", "dependencies": []}
        ]
        ```"""
    )

    assert list(tasks) == ["t1"]
    assert tasks["t1"].description == "Collect sources"


def test_parse_tasks_extracts_json_array_from_mixed_text():
    tasks = planner_module._parse_tasks(
        """I will split this into tasks:
        [
          {"task_id": "t1", "description": "Collect sources", "dependencies": []}
        ]
        Please execute them."""
    )

    assert list(tasks) == ["t1"]


def test_parse_tasks_accepts_object_wrapped_tasks():
    tasks = planner_module._parse_tasks(
        """
        {
          "tasks": [
            {"task_id": "t1", "description": "Collect sources", "dependencies": []},
            {"task_id": "t2", "description": "Summarize", "dependencies": ["t1"]}
          ]
        }
        """
    )

    assert list(tasks) == ["t1", "t2"]
    assert tasks["t2"].dependencies == ["t1"]


def test_parse_tasks_rejects_missing_task_id():
    with pytest.raises(planner_module.PlannerParseError, match="task_id"):
        planner_module._parse_tasks(
            """[
              {"description": "Collect sources", "dependencies": []}
            ]"""
        )


def test_parse_tasks_rejects_missing_description():
    with pytest.raises(planner_module.PlannerParseError, match="description"):
        planner_module._parse_tasks(
            """[
              {"task_id": "t1", "dependencies": []}
            ]"""
        )


@pytest.mark.parametrize(
    "dependencies",
    [
        '"t1"',
        '[1]',
        '{"task": "t1"}',
    ],
)
def test_parse_tasks_rejects_dependencies_that_are_not_list_of_strings(dependencies):
    with pytest.raises(planner_module.PlannerParseError, match="dependencies"):
        planner_module._parse_tasks(
            f"""[
              {{"task_id": "t1", "description": "Collect sources", "dependencies": {dependencies}}}
            ]"""
        )


def test_parse_tasks_rejects_duplicate_task_id():
    with pytest.raises(planner_module.PlannerParseError, match="duplicate task_id"):
        planner_module._parse_tasks(
            """[
              {"task_id": "t1", "description": "Collect sources", "dependencies": []},
              {"task_id": "t1", "description": "Summarize", "dependencies": []}
            ]"""
        )


def test_parse_tasks_rejects_unknown_dependency_reference():
    with pytest.raises(planner_module.PlannerParseError, match="unknown task_id"):
        planner_module._parse_tasks(
            """[
              {"task_id": "t1", "description": "Collect sources", "dependencies": ["missing"]}
            ]"""
        )


def test_planner_repairs_invalid_json_once(monkeypatch):
    calls = []

    async def fake_call_llm(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"content": '[{"task_id": "t1", "description": "Collect sources", "dependencies": []'}
        return {
            "content": '[{"task_id": "t1", "description": "Collect sources", "dependencies": []}]'
        }

    monkeypatch.setattr(planner_module, "call_llm", fake_call_llm)

    result = asyncio.run(planner_module.planner_node({"user_input": "research task"}))

    assert list(result["tasks"]) == ["t1"]
    assert len(calls) == 2
    assert calls[1]["temperature"] == 0
    assert calls[1]["role"] == "planner"


def test_planner_repair_failure_returns_planner_failure(monkeypatch):
    async def fake_call_llm(**kwargs):
        return {"content": "not json"}

    monkeypatch.setattr(planner_module, "call_llm", fake_call_llm)

    result = asyncio.run(planner_module.planner_node({"user_input": "research task"}))

    assert result["tasks"] == {"__clear__": True}
    assert result["planner_failure"] is True
    assert "planner_error" in result
    assert "repair" in result["planner_error"]
    assert result["ready_tasks"] == []
