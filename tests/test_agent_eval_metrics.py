import json
from pathlib import Path

import pytest

from app.evaluation.agent_ab import (
    REPORT_SOURCE_DETERMINISTIC,
    VARIANT_LANGGRAPH,
    VARIANT_REACT,
    DeterministicModelAdapter,
    DeterministicToolAdapter,
    LangGraphVariantRunner,
    LinearReActRunner,
    run_paired_evaluation,
)


DATASET_DIR = Path(__file__).resolve().parents[1] / "datasets" / "agent_eval"
CASES_PATH = DATASET_DIR / "eval_cases.jsonl"

TARGET_GROUPS = {
    "planner": {"planner_decomposition"},
    "retrieval": {"rag_retrieval"},
    "extraction": {"pdf_parsing", "financial_report_qa"},
    "end_to_end": {"end_to_end_report"},
    "loop_stability": {"loop_stability"},
}


def load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def source_texts(case: dict) -> dict[str, str]:
    repo_root = DATASET_DIR.parents[1]
    texts = {}
    for source in case["available_sources"]:
        if source["type"] == "none":
            texts[source["id"]] = ""
        else:
            texts[source["id"]] = (repo_root / source["path"]).read_text(encoding="utf-8")
    return texts


def evidence_hit_rate(case: dict) -> float:
    if not case["evidence"]:
        return 1.0
    texts = source_texts(case)
    hits = 0
    for item in case["evidence"]:
        if item["quote"] in texts[item["source_id"]]:
            hits += 1
    return hits / len(case["evidence"])


def calculation_is_valid(case: dict) -> bool:
    calculation = case.get("calculation")
    if not calculation:
        return True

    if calculation["type"] == "growth_rate":
        prior = calculation["inputs"]["prior"]
        current = calculation["inputs"]["current"]
        return round((current - prior) / prior * 100, 2) == calculation["result_percent"]

    if calculation["type"] == "gross_margin_rate":
        gross_margin = calculation["inputs"]["gross_margin"]
        revenue = calculation["inputs"]["revenue"]
        return round(gross_margin / revenue * 100, 2) == calculation["result_percent"]

    if calculation["type"] == "ratio_trend":
        ratios = [
            round(item["numerator"] / item["denominator"] * 100, 2)
            for item in calculation["inputs"]
        ]
        return (
            ratios == calculation["result_percentages"]
            and calculation["is_increasing"] == all(
                left < right for left, right in zip(ratios, ratios[1:])
            )
        )

    return False


def evaluate_case(case: dict) -> dict:
    evidence_rate = evidence_hit_rate(case)
    source_paths_exist = all(
        source["type"] == "none" or (DATASET_DIR.parents[1] / source["path"]).exists()
        for source in case["available_sources"]
    )
    loop_bounded = (
        case["loop_rules"]["max_total_steps"] <= case["max_steps"]
        and case["loop_rules"]["max_same_tool_calls"] <= 3
        and case["loop_rules"]["max_no_state_change_steps"] <= 3
    )
    expected_graph_shape = (
        "controller" in case["expected_nodes"]
        and case["max_steps"] > 0
        and bool(case["gold_behavior"].strip())
    )

    return {
        "id": case["id"],
        "category": case["category"],
        "source_paths_exist": source_paths_exist,
        "evidence_hit_rate": evidence_rate,
        "gold_answer_present": bool(case["gold_answer"]),
        "calculation_valid": calculation_is_valid(case),
        "loop_bounded": loop_bounded,
        "expected_graph_shape": expected_graph_shape,
        "passed": all(
            [
                source_paths_exist,
                evidence_rate == 1.0,
                bool(case["gold_answer"]),
                calculation_is_valid(case),
                loop_bounded,
                expected_graph_shape,
            ]
        ),
    }


def summarize_group(cases: list[dict], categories: set[str]) -> dict:
    selected = [case for case in cases if case["category"] in categories]
    results = [evaluate_case(case) for case in selected]
    passed = sum(1 for result in results if result["passed"])
    evidence_rates = [result["evidence_hit_rate"] for result in results]

    return {
        "case_count": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "evidence_hit_rate": round(sum(evidence_rates) / len(evidence_rates), 4)
        if evidence_rates
        else 0.0,
        "calculation_valid_rate": round(
            sum(1 for result in results if result["calculation_valid"]) / len(results),
            4,
        )
        if results
        else 0.0,
        "loop_bounded_rate": round(
            sum(1 for result in results if result["loop_bounded"]) / len(results),
            4,
        )
        if results
        else 0.0,
        "failed_ids": [result["id"] for result in results if not result["passed"]],
    }


def build_metrics_report() -> dict:
    cases = load_cases()
    groups = {
        name: summarize_group(cases, categories)
        for name, categories in TARGET_GROUPS.items()
    }
    evaluated_categories = set().union(*TARGET_GROUPS.values())
    evaluated_cases = [
        case for case in cases if case["category"] in evaluated_categories
    ]
    overall_results = [evaluate_case(case) for case in evaluated_cases]
    return {
        "overall": {
            "case_count": len(overall_results),
            "passed": sum(1 for result in overall_results if result["passed"]),
            "pass_rate": round(
                sum(1 for result in overall_results if result["passed"])
                / len(overall_results),
                4,
            ),
        },
        "groups": groups,
    }


def test_dataset_driven_eval_metrics():
    report = build_metrics_report()

    expected_count = sum(group["case_count"] for group in report["groups"].values())
    assert report["overall"]["case_count"] == expected_count
    assert report["overall"]["pass_rate"] == 1.0
    for group in report["groups"].values():
        assert group["case_count"] > 0
        assert group["pass_rate"] == 1.0
        assert group["evidence_hit_rate"] == 1.0
        assert group["loop_bounded_rate"] == 1.0


@pytest.mark.asyncio
async def test_ab_comparison_report_uses_executed_runner_results():
    case = next(case for case in load_cases() if case["id"] == "rag_001")
    repo_root = DATASET_DIR.parents[1]
    runners = {
        VARIANT_LANGGRAPH: LangGraphVariantRunner(
            DeterministicModelAdapter(
                script=[
                    {"content": '{"intent": "complex_research"}'},
                    {"content": '[{"task_id":"t1","description":"retrieve evidence","dependencies":[]}]'},
                    {"content": "worker direct answer"},
                ]
            ),
            DeterministicToolAdapter.from_case(case, repo_root),
        ),
        VARIANT_REACT: LinearReActRunner(
            DeterministicModelAdapter(script=[{"content": "react direct answer"}]),
            DeterministicToolAdapter.from_case(case, repo_root),
        ),
    }

    report = await run_paired_evaluation([case], runners, source=REPORT_SOURCE_DETERMINISTIC)
    graph = report.variant_results[VARIANT_LANGGRAPH]
    react = report.variant_results[VARIANT_REACT]

    assert report.evidentiary is True
    assert graph.case_count == react.case_count == 1
    assert graph.pass_rate == react.pass_rate == 1.0
    assert graph.loop_rate == react.loop_rate == 0.0
