import argparse
import asyncio
import json
import subprocess
import sys
import sys as _sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORMAL_DATASET = ROOT / "evals" / "datasets" / "formal" / "formal_loop_minimal.jsonl"
SUMMARY_FIELDS = {
    "termination_rate",
    "loop_rate",
    "avg_tool_calls",
    "duplicate_tool_call_ratio",
    "max_step_violation_rate",
    "stuck_running_count",
}


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_json(args: list[str]) -> dict:
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _formal_case_count() -> int:
    return len(_load_jsonl(FORMAL_DATASET))


def test_run_eval_langgraph_mock_stable_outputs_report_contract():
    report = _run_json(
        [
            "evals/scripts/run_eval_langgraph.py",
            "--dataset",
            str(FORMAL_DATASET),
            "--mock-tools",
            "--mock-llm-stable",
            "--timeout-sec",
            "30",
        ]
    )

    assert {"summary", "cases", "runner_mode", "llm_mode", "variant", "evidentiary"}.issubset(report)
    assert report["runner_mode"] == "single_variant"
    assert report["llm_mode"] == "mock_llm_stable"
    assert report["variant"] == "langgraph_state_machine"
    assert report["evidentiary"] is False
    assert SUMMARY_FIELDS.issubset(report["summary"])
    assert report["summary"]["stuck_running_count"] == 0
    assert len(report["cases"]) == _formal_case_count()


def test_run_eval_langgraph_negative_control_loop_mode_reports_loop_signal():
    report = _run_json(
        [
            "evals/scripts/run_eval_langgraph.py",
            "--dataset",
            str(FORMAL_DATASET),
            "--mock-tools",
            "--mock-llm-loop",
            "--timeout-sec",
            "30",
        ]
    )

    has_no_loop_failure = any(case["no_loop"]["passed"] is False for case in report["cases"])
    assert report["llm_mode"] == "mock_llm_loop"
    assert report["summary"]["loop_rate"] > 0.0 or has_no_loop_failure
    assert report["evidentiary"] is False


def test_run_eval_langgraph_exposes_linear_react_variant():
    report = _run_json(
        [
            "evals/scripts/run_eval_langgraph.py",
            "--dataset",
            str(FORMAL_DATASET),
            "--variant",
            "linear_react_baseline",
            "--mock-tools",
            "--timeout-sec",
            "30",
        ]
    )

    assert report["variant"] == "linear_react_baseline"
    assert report["llm_mode"] == "mock_llm_stable"
    assert len(report["cases"]) == _formal_case_count()


def test_run_eval_langgraph_rejects_conflicting_llm_modes():
    completed = subprocess.run(
        [
            sys.executable,
            "evals/scripts/run_eval_langgraph.py",
            "--dataset",
            str(FORMAL_DATASET),
            "--mock-tools",
            "--mock-llm-loop",
            "--real-llm",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    assert "not allowed with argument" in completed.stderr


def test_mock_tool_registry_context_restores_sys_modules():
    from evals.scripts.run_eval_langgraph import build_mock_tool_adapter, mock_tool_registry_module

    case = _load_jsonl(FORMAL_DATASET)[0]
    adapter = build_mock_tool_adapter(case)
    original_present = "app.infrastructure.setup" in _sys.modules
    original = _sys.modules.get("app.infrastructure.setup")

    with mock_tool_registry_module(adapter):
        assert _sys.modules["app.infrastructure.setup"] is not original

    if original_present:
        assert _sys.modules["app.infrastructure.setup"] is original
    else:
        assert "app.infrastructure.setup" not in _sys.modules


def test_run_eval_ab_outputs_variant_summaries_and_pairwise_deltas():
    report = _run_json(
        [
            "evals/scripts/run_eval_ab.py",
            "--dataset",
            str(FORMAL_DATASET),
            "--mock-tools",
            "--mock-llm-stable",
            "--timeout-sec",
            "30",
        ]
    )

    expected_variants = {
        "langgraph_state_machine",
        "linear_react_baseline",
        "langgraph_react_worker",
    }
    assert report["runner_mode"] == "ab"
    assert report["llm_mode"] == "mock_llm_stable"
    assert report["mock_tools"] is True
    assert report["evidentiary"] is False
    assert set(report["variants"]) == expected_variants
    assert all(SUMMARY_FIELDS.issubset(row["summary"]) for row in report["variants"].values())
    assert "langgraph_state_machine__vs__linear_react_baseline" in report["pairwise_deltas"]


def test_run_eval_ab_records_failed_variant_and_skips_its_pairwise_deltas(monkeypatch):
    from evals.scripts import run_eval_ab

    async def fake_run_dataset(args: argparse.Namespace) -> dict:
        if args.variant == "linear_react_baseline":
            raise RuntimeError("variant failed")
        return {
            "summary": {"termination_rate": 1.0, "loop_rate": 0.0},
            "cases": [{"case_id": args.variant}],
        }

    monkeypatch.setattr(run_eval_ab, "run_dataset", fake_run_dataset)
    args = argparse.Namespace(
        dataset=FORMAL_DATASET,
        mock_tools=True,
        llm_mode="mock_llm_stable",
        timeout_sec=30,
    )

    report = asyncio.run(run_eval_ab.run_ab(args))

    assert report["variants"]["linear_react_baseline"] == {
        "error": "variant failed",
        "summary": None,
        "cases": [],
    }
    assert "langgraph_state_machine__vs__langgraph_react_worker" in report["pairwise_deltas"]
    assert "langgraph_state_machine__vs__linear_react_baseline" not in report["pairwise_deltas"]
    assert "langgraph_react_worker__vs__linear_react_baseline" not in report["pairwise_deltas"]
