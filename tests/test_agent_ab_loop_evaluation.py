import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.evaluation.agent_ab import (
    REPORT_SOURCE_DETERMINISTIC,
    REPORT_SOURCE_SIMULATED,
    STOP_COMPLETED,
    STOP_CONTROLLED,
    STOP_HUMAN_INPUT,
    STOP_LOOP_DETECTED,
    STOP_MAX_STEPS,
    STOP_RUNTIME_ERROR,
    STOP_TOOL_FAILURE,
    VARIANT_LANGGRAPH_REACT_WORKER,
    VARIANT_LANGGRAPH,
    VARIANT_REACT,
    DeterministicModelAdapter,
    DeterministicToolAdapter,
    LangGraphReactWorkerRunner,
    LangGraphVariantRunner,
    LinearReActRunner,
    TraceEvent,
    build_ab_report,
    run_paired_evaluation,
    score_run,
)


DATASET_DIR = Path(__file__).resolve().parents[1] / "datasets" / "agent_eval"
CASES_PATH = DATASET_DIR / "eval_cases.jsonl"


def load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def case_by_id(case_id: str) -> dict:
    return next(case for case in load_cases() if case["id"] == case_id)


def make_adapters(case: dict, script: list[dict] | None = None):
    return (
        DeterministicModelAdapter(script=script or []),
        DeterministicToolAdapter.from_case(case, DATASET_DIR.parents[1]),
    )


def test_trace_event_records_execution_fields():
    event = TraceEvent(
        variant=VARIANT_REACT,
        step=1,
        action="tool",
        state_before="before",
        state_after="after",
        state_diff={"changed": ["messages"]},
        tool_name="rag_search",
        tool_arguments={"query": "q"},
        tool_output="answer",
    )

    assert event.variant == VARIANT_REACT
    assert event.step == 1
    assert event.tool_argument_hash
    assert event.tool_output_hash
    assert event.state_changed is True


def test_scorer_uses_actual_trace_thresholds():
    case = case_by_id("loop_001")
    trace = [
        TraceEvent(
            variant=VARIANT_REACT,
            step=step,
            action="tool",
            state_before="same",
            state_after="same",
            state_diff={},
            tool_name="rag_search",
            tool_arguments={"query": "Apple 2026 annual report"},
            stop_reason=STOP_LOOP_DETECTED if step == 4 else None,
        )
        for step in range(1, 5)
    ]

    score = score_run(case, trace, final_output="", stop_reason=STOP_LOOP_DETECTED)

    assert score.loop_triggered is True
    assert score.triggered_by_tool is True
    assert score.triggered_by_state is True
    assert score.max_same_tool_calls > case["loop_rules"]["max_same_tool_calls"]


def test_scorer_marks_total_step_and_no_state_change_loops():
    case = case_by_id("loop_002")
    trace = [
        TraceEvent(
            variant=VARIANT_LANGGRAPH,
            step=step,
            action="worker",
            state_before="same",
            state_after="same",
            state_diff={},
        )
        for step in range(1, case["loop_rules"]["max_total_steps"] + 2)
    ]

    score = score_run(case, trace, final_output="", stop_reason=STOP_MAX_STEPS)

    assert score.loop_triggered is True
    assert score.max_step_aborted is True
    assert score.triggered_by_steps is True
    assert score.triggered_by_state is True


def test_scorer_accepts_controlled_stop_without_loop():
    case = case_by_id("loop_003")
    trace = [
        TraceEvent(
            variant=VARIANT_LANGGRAPH,
            step=1,
            action="reviewer",
            state_before="a",
            state_after="b",
            state_diff={"changed": ["final_report"]},
            stop_reason=STOP_CONTROLLED,
        )
    ]

    score = score_run(case, trace, final_output="资料不足，停止。", stop_reason=STOP_CONTROLLED)

    assert score.passed is True
    assert score.loop_triggered is False


@pytest.mark.asyncio
async def test_langgraph_runner_executes_case_and_captures_trace():
    case = case_by_id("rag_001")
    model, tools = make_adapters(
        case,
        script=[
            {"content": '{"intent": "complex_research"}'},
            {"content": '[{"task_id":"t1","description":"retrieve evidence","dependencies":[]}]'},
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "rag_search",
                            "arguments": json.dumps({"query": case["user_query"]}, ensure_ascii=False),
                        },
                    }
                ]
            },
            {"content": "final answer with evidence"},
        ],
    )
    runner = LangGraphVariantRunner(model_adapter=model, tool_adapter=tools)

    result = await runner.run_case(case)

    assert result.variant == VARIANT_LANGGRAPH
    assert result.executed is True
    assert result.trace
    assert any(event.action == "controller" for event in result.trace)
    assert any(event.tool_name == "rag_search" for event in result.trace)
    assert result.stop_reason == STOP_COMPLETED


@pytest.mark.asyncio
async def test_react_runner_executes_case_without_langgraph_nodes():
    case = case_by_id("rag_001")
    model, tools = make_adapters(
        case,
        script=[
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "rag_search",
                            "arguments": json.dumps({"query": case["user_query"]}, ensure_ascii=False),
                        },
                    }
                ]
            },
            {"content": "final answer with evidence"},
        ],
    )
    runner = LinearReActRunner(model_adapter=model, tool_adapter=tools)

    result = await runner.run_case(case)
    score = score_run(case, result.trace, result.final_output, result.stop_reason)

    assert result.variant == VARIANT_REACT
    assert result.executed is True
    assert result.stop_reason == STOP_COMPLETED
    assert {event.action for event in result.trace}.isdisjoint({"planner", "reviewer"})
    assert score.passed is True


@pytest.mark.asyncio
async def test_langgraph_react_worker_runner_executes_with_graph_shell_and_react_worker():
    case = case_by_id("rag_001")
    model, tools = make_adapters(
        case,
        script=[
            {"content": '{"intent": "complex_research"}'},
            {"content": '[{"task_id":"t1","description":"retrieve evidence","dependencies":[]}]'},
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "rag_search",
                            "arguments": json.dumps({"query": case["user_query"]}, ensure_ascii=False),
                        },
                    }
                ]
            },
            {"content": "react worker final answer"},
        ],
    )
    runner = LangGraphReactWorkerRunner(model, tools)

    result = await runner.run_case(case)
    actions = {event.action for event in result.trace}

    assert result.variant == VARIANT_LANGGRAPH_REACT_WORKER
    assert {"controller", "planner", "worker", "reviewer"}.issubset(actions)
    assert any(event.action == "worker_tool" and event.tool_name == "rag_search" for event in result.trace)
    assert result.stop_reason == STOP_COMPLETED


def test_ablation_runner_does_not_replace_production_worker_node():
    from app.graph.build_graph import worker_node as production_worker_node
    from app.graph.nodes.worker import worker_node

    assert production_worker_node is worker_node


@pytest.mark.asyncio
async def test_langgraph_react_worker_loop_is_scored_from_observed_trace():
    case = case_by_id("loop_001")
    repeated = case["loop_rules"]["max_same_tool_calls"] + 1
    model, tools = make_adapters(
        case,
        script=[
            {"content": '{"intent": "complex_research"}'},
            {"content": '[{"task_id":"t1","description":"loop pressure","dependencies":[]}]'},
            *[
                {
                    "tool_calls": [
                        {
                            "id": f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": "rag_search",
                                "arguments": json.dumps({"query": case["user_query"]}, ensure_ascii=False),
                            },
                        }
                    ]
                }
                for idx in range(repeated)
            ],
        ],
    )
    runner = LangGraphReactWorkerRunner(model, tools)

    result = await runner.run_case(case)
    score = score_run(case, result.trace, result.final_output, result.stop_reason)

    assert result.stop_reason == STOP_LOOP_DETECTED
    assert score.loop_triggered is True
    assert score.triggered_by_tool is True


def test_langgraph_react_worker_no_progress_loop_is_scored_from_trace():
    case = case_by_id("loop_002")
    trace = [
        TraceEvent(
            variant=VARIANT_LANGGRAPH_REACT_WORKER,
            step=step,
            action="worker",
            state_before="same",
            state_after="same",
            state_diff={},
        )
        for step in range(1, case["loop_rules"]["max_no_state_change_steps"] + 2)
    ]

    score = score_run(case, trace, final_output="", stop_reason=STOP_LOOP_DETECTED)

    assert score.loop_triggered is True
    assert score.triggered_by_state is True


@pytest.mark.asyncio
async def test_paired_report_requires_both_variants_and_uses_observed_results():
    case = case_by_id("rag_001")
    lang_model, lang_tools = make_adapters(
        case,
        script=[
            {"content": '{"intent": "complex_research"}'},
            {"content": '[{"task_id":"t1","description":"retrieve evidence","dependencies":[]}]'},
            {"content": "worker direct answer"},
        ],
    )
    react_model, react_tools = make_adapters(case, script=[{"content": "react answer"}])
    runners = {
        VARIANT_LANGGRAPH: LangGraphVariantRunner(lang_model, lang_tools),
        VARIANT_REACT: LinearReActRunner(react_model, react_tools),
    }

    report = await run_paired_evaluation([case], runners, source=REPORT_SOURCE_DETERMINISTIC)

    assert set(report.variant_results) == {VARIANT_LANGGRAPH, VARIANT_REACT}
    assert report.source == REPORT_SOURCE_DETERMINISTIC
    assert report.evidentiary is True
    assert report.variant_results[VARIANT_LANGGRAPH].case_count == 1
    assert report.variant_results[VARIANT_REACT].case_count == 1


@pytest.mark.asyncio
async def test_three_variant_report_includes_optional_ablation_and_pairwise_deltas():
    case = case_by_id("rag_001")
    lang_model, lang_tools = make_adapters(
        case,
        script=[
            {"content": '{"intent": "complex_research"}'},
            {"content": '[{"task_id":"t1","description":"retrieve evidence","dependencies":[]}]'},
            {"content": "worker direct answer"},
        ],
    )
    ablation_model, ablation_tools = make_adapters(
        case,
        script=[
            {"content": '{"intent": "complex_research"}'},
            {"content": '[{"task_id":"t1","description":"retrieve evidence","dependencies":[]}]'},
            {"content": "react worker direct answer"},
        ],
    )
    react_model, react_tools = make_adapters(case, script=[{"content": "react answer"}])
    runners = {
        VARIANT_LANGGRAPH: LangGraphVariantRunner(lang_model, lang_tools),
        VARIANT_LANGGRAPH_REACT_WORKER: LangGraphReactWorkerRunner(ablation_model, ablation_tools),
        VARIANT_REACT: LinearReActRunner(react_model, react_tools),
    }

    report = await run_paired_evaluation([case], runners, source=REPORT_SOURCE_DETERMINISTIC)

    assert set(report.variant_results) == {VARIANT_LANGGRAPH, VARIANT_LANGGRAPH_REACT_WORKER, VARIANT_REACT}
    assert "pairwise_deltas" in report.as_dict()
    assert f"{VARIANT_LANGGRAPH}__vs__{VARIANT_LANGGRAPH_REACT_WORKER}" in report.as_dict()["pairwise_deltas"]


def test_report_marks_simulated_source_as_non_evidentiary():
    case = case_by_id("rag_001")
    report = build_ab_report(
        [
            {
                "case": case,
                "variant": VARIANT_REACT,
                "trace": [
                    TraceEvent(
                        variant=VARIANT_REACT,
                        step=1,
                        action="react_final",
                        state_before="a",
                        state_after="b",
                        state_diff={"changed": ["final_output"]},
                        stop_reason=STOP_COMPLETED,
                    )
                ],
                "final_output": "ok",
                "stop_reason": STOP_COMPLETED,
                "executed": False,
            },
            {
                "case": case,
                "variant": VARIANT_LANGGRAPH,
                "trace": [
                    TraceEvent(
                        variant=VARIANT_LANGGRAPH,
                        step=1,
                        action="controller",
                        state_before="a",
                        state_after="b",
                        state_diff={"changed": ["next_action"]},
                        stop_reason=STOP_COMPLETED,
                    )
                ],
                "final_output": "ok",
                "stop_reason": STOP_COMPLETED,
                "executed": False,
            },
        ],
        source=REPORT_SOURCE_SIMULATED,
    )

    assert report.evidentiary is False
    assert "non-evidentiary" in report.notes


def test_report_requires_executed_variants_for_evidence():
    case = case_by_id("rag_001")
    report = build_ab_report(
        [
            {
                "case": case,
                "variant": VARIANT_REACT,
                "trace": [
                    TraceEvent(
                        variant=VARIANT_REACT,
                        step=1,
                        action="react_final",
                        state_before="a",
                        state_after="b",
                        state_diff={"changed": ["final_output"]},
                        stop_reason=STOP_COMPLETED,
                    )
                ],
                "final_output": "ok",
                "stop_reason": STOP_COMPLETED,
                "executed": True,
            },
            {
                "case": case,
                "variant": VARIANT_LANGGRAPH,
                "trace": [],
                "final_output": "fixture",
                "stop_reason": STOP_COMPLETED,
                "executed": False,
            },
        ],
        source=REPORT_SOURCE_DETERMINISTIC,
    )

    assert report.evidentiary is False


@pytest.mark.asyncio
async def test_runtime_errors_are_reported_as_stop_reasons():
    case = case_by_id("rag_001")
    model, tools = make_adapters(case, script=[])
    model.raise_on_call = RuntimeError("boom")
    runner = LinearReActRunner(model, tools)

    result = await runner.run_case(case)

    assert result.stop_reason == STOP_RUNTIME_ERROR
    assert result.error == "boom"


@pytest.mark.asyncio
async def test_tool_failures_are_reported_as_stop_reasons():
    case = case_by_id("tool_001")
    model, tools = make_adapters(
        case,
        script=[
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "send_email",
                            "arguments": json.dumps({"to": "customer@example.com"}),
                        },
                    }
                ]
            }
        ],
    )
    runner = LinearReActRunner(model, tools)

    result = await runner.run_case(case)

    assert result.stop_reason == STOP_TOOL_FAILURE


@pytest.mark.asyncio
async def test_react_reports_human_input_required_control_signal():
    case = case_by_id("hitl_001")
    model, tools = make_adapters(
        case,
        script=[
            {
                "content": json.dumps(
                    {"human_input_required": True, "reason": "需要人工确认。"},
                    ensure_ascii=False,
                )
            }
        ],
    )
    runner = LinearReActRunner(model, tools)

    result = await runner.run_case(case)

    assert result.stop_reason == STOP_HUMAN_INPUT


def test_cli_can_include_react_worker_ablation_variant():
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_agent_ab_eval.py",
            "--case-id",
            "rag_001",
            "--include-react-worker-ablation",
        ],
        cwd=DATASET_DIR.parents[1],
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    report = json.loads(completed.stdout)

    assert VARIANT_LANGGRAPH_REACT_WORKER in report["variants"]
    assert "pairwise_deltas" in report


def test_cli_defaults_to_two_variants_without_ablation():
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_agent_ab_eval.py",
            "--case-id",
            "rag_001",
        ],
        cwd=DATASET_DIR.parents[1],
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    report = json.loads(completed.stdout)

    assert set(report["variants"]) == {VARIANT_LANGGRAPH, VARIANT_REACT}
    assert VARIANT_LANGGRAPH_REACT_WORKER not in report["variants"]
