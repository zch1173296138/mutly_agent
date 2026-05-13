import json
from pathlib import Path


DATASET_DIR = Path(__file__).resolve().parents[1] / "datasets" / "agent_eval"
CASES_PATH = DATASET_DIR / "eval_cases.jsonl"
SCHEMA_PATH = DATASET_DIR / "schema.json"
README_PATH = DATASET_DIR / "README.md"

REQUIRED_CATEGORIES = {
    "controller_routing",
    "planner_decomposition",
    "rag_retrieval",
    "pdf_parsing",
    "financial_report_qa",
    "end_to_end_report",
    "tool_failure",
    "hitl_safety",
    "loop_stability",
}

REQUIRED_FIELDS = {
    "id",
    "category",
    "difficulty",
    "user_query",
    "available_sources",
    "gold_answer",
    "gold_behavior",
    "evidence",
    "expected_nodes",
    "expected_tools",
    "max_steps",
    "loop_rules",
    "scoring",
}

ANSWER_REQUIRED_CATEGORIES = {
    "rag_retrieval",
    "pdf_parsing",
    "financial_report_qa",
    "end_to_end_report",
}

REQUIRED_LOOP_RULES = {
    "max_total_steps",
    "max_same_tool_calls",
    "max_no_state_change_steps",
}

AB_VARIANTS = {
    "langgraph_state_machine",
    "langgraph_react_worker",
    "linear_react_baseline",
}
AB_STOP_REASONS = {
    "completed",
    "controlled_stop",
    "loop_detected",
    "max_steps_exceeded",
    "tool_failure",
    "human_input_required",
    "runtime_error",
}


def load_cases() -> list[dict]:
    assert CASES_PATH.exists(), f"missing dataset file: {CASES_PATH}"
    rows = []
    for line_no, line in enumerate(CASES_PATH.read_text(encoding="utf-8").splitlines(), 1):
        assert line.strip(), f"blank line at {CASES_PATH}:{line_no}"
        rows.append(json.loads(line))
    return rows


def test_dataset_files_exist():
    assert CASES_PATH.exists()
    assert SCHEMA_PATH.exists()
    assert README_PATH.exists()


def test_schema_declares_required_contract_fields():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    assert REQUIRED_FIELDS.issubset(set(schema["required"]))
    for field in REQUIRED_FIELDS:
        assert field in schema["properties"]
    assert "ab_test" in schema["properties"]
    assert "ab_test" not in schema["required"]


def test_cases_are_unique_and_structurally_valid():
    cases = load_cases()
    assert len(cases) >= 24

    seen_ids = set()
    for case in cases:
        assert REQUIRED_FIELDS.issubset(case.keys()), case.get("id", "<missing id>")
        assert case["id"] not in seen_ids
        seen_ids.add(case["id"])
        assert case["category"] in REQUIRED_CATEGORIES
        assert case["difficulty"] in {"easy", "medium", "hard"}
        assert case["user_query"].strip()
        assert isinstance(case["available_sources"], list)
        assert isinstance(case["gold_answer"], dict)
        assert case["gold_behavior"].strip()
        assert isinstance(case["evidence"], list)
        assert isinstance(case["expected_nodes"], list)
        assert "controller" in case["expected_nodes"]
        assert isinstance(case["expected_tools"], list)
        assert isinstance(case["max_steps"], int)
        assert case["max_steps"] > 0
        assert REQUIRED_LOOP_RULES.issubset(case["loop_rules"].keys())
        assert all(isinstance(case["loop_rules"][key], int) for key in REQUIRED_LOOP_RULES)
        if "ab_test" in case:
            ab_test = case["ab_test"]
            assert ab_test["experiment"] == "langgraph_vs_react_loop"
            assert ab_test["primary_metric"] == "loop_rate"
            assert ab_test["hypothesis_only"] is True
            assert set(ab_test["variants"]) == AB_VARIANTS
            for hypothesis in ab_test["variants"].values():
                assert isinstance(hypothesis["hypothesis_pass"], bool)
                assert isinstance(hypothesis["hypothesis_loop"], bool)
                assert hypothesis["hypothesis_stop_reason"] in AB_STOP_REASONS
                assert hypothesis["hypothesis_notes"].strip()
        assert abs(sum(case["scoring"].values()) - 1.0) < 1e-9
        assert all(value >= 0 for value in case["scoring"].values())


def test_sources_and_evidence_are_real_and_traceable():
    repo_root = DATASET_DIR.parents[1]

    for case in load_cases():
        sources = {source["id"]: source for source in case["available_sources"]}
        for source in sources.values():
            assert source["id"].strip()
            assert "占位" not in source["note"]
            if source["type"] != "none":
                source_path = repo_root / source["path"]
                assert source_path.exists(), f"{case['id']} references missing source: {source_path}"

        if case["category"] in ANSWER_REQUIRED_CATEGORIES:
            assert case["gold_answer"], f"{case['id']} needs a gold_answer"
            assert case["evidence"], f"{case['id']} needs evidence"

        for item in case["evidence"]:
            source_id = item["source_id"]
            assert source_id in sources, f"{case['id']} evidence source is not declared: {source_id}"
            source = sources[source_id]
            if source["type"] == "none":
                continue
            text = (repo_root / source["path"]).read_text(encoding="utf-8")
            assert item["quote"] in text, f"{case['id']} quote not found in {source['path']}"


def test_financial_calculations_are_recomputable():
    finance_cases = [case for case in load_cases() if case["category"] == "financial_report_qa"]
    assert finance_cases

    for case in finance_cases:
        calculation = case.get("calculation")
        assert calculation, f"{case['id']} needs calculation metadata"
        if calculation["type"] == "growth_rate":
            prior = calculation["inputs"]["prior"]
            current = calculation["inputs"]["current"]
            expected = round((current - prior) / prior * 100, 2)
            assert calculation["result_percent"] == expected
        elif calculation["type"] == "gross_margin_rate":
            gross_margin = calculation["inputs"]["gross_margin"]
            revenue = calculation["inputs"]["revenue"]
            expected = round(gross_margin / revenue * 100, 2)
            assert calculation["result_percent"] == expected
        elif calculation["type"] == "ratio_trend":
            ratios = [
                round(item["numerator"] / item["denominator"] * 100, 2)
                for item in calculation["inputs"]
            ]
            assert ratios == calculation["result_percentages"]
            assert calculation["is_increasing"] == all(
                left < right for left, right in zip(ratios, ratios[1:])
            )
        else:
            raise AssertionError(f"unknown calculation type: {calculation['type']}")


def test_required_category_coverage():
    categories = {case["category"] for case in load_cases()}
    assert REQUIRED_CATEGORIES.issubset(categories)


def test_loop_stability_cases_have_explicit_stop_behavior():
    loop_cases = [case for case in load_cases() if case["category"] == "loop_stability"]
    assert len(loop_cases) >= 4

    for case in loop_cases:
        behavior = case["gold_behavior"]
        assert any(keyword in behavior for keyword in ["停止", "终止", "挂起", "说明缺失"])
        assert case["loop_rules"]["max_total_steps"] <= case["max_steps"]
        assert case["loop_rules"]["max_same_tool_calls"] <= 3
        assert case["loop_rules"]["max_no_state_change_steps"] <= 3
