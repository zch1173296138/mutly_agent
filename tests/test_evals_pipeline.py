import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATED_DATASET = ROOT / "evals" / "datasets" / "generated" / "multy_agent_loop_eval.jsonl"
OPEN_SOURCE_DATASET = ROOT / "evals" / "datasets" / "generated" / "open_source_agent_eval.jsonl"
FORMAL_DATASET = ROOT / "evals" / "datasets" / "formal" / "formal_loop_minimal.jsonl"
DEV_DATASET = ROOT / "evals" / "datasets" / "dev" / "dev_loop_eval.jsonl"
STAGING_DATASET = ROOT / "evals" / "datasets" / "staging" / "open_source_agent_eval.jsonl"
TEMPLATES_DATASET = ROOT / "evals" / "datasets" / "templates" / "open_source_slots.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_generated_dataset_is_valid_and_preserves_seed_cases():
    seed_rows = _load_jsonl(ROOT / "datasets" / "agent_eval" / "eval_cases.jsonl")
    generated_rows = _load_jsonl(GENERATED_DATASET)

    completed = subprocess.run(
        [sys.executable, "evals/scripts/validate_dataset.py", str(GENERATED_DATASET)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "valid" in completed.stdout
    assert len(generated_rows) == 65
    assert [row["id"] for row in generated_rows[: len(seed_rows)]] == [row["id"] for row in seed_rows]
    for original, copied in zip(seed_rows, generated_rows[: len(seed_rows)]):
        for key, value in original.items():
            assert copied[key] == value
        assert copied["source"]["kind"] == "seed_dataset"


def test_open_source_adapters_default_to_dry_run_and_unknown_license():
    from evals.adapters.gaia_adapter import GAIAAdapter
    from evals.adapters.agentbench_adapter import AgentBenchAdapter
    from evals.adapters.toolbench_adapter import ToolBenchAdapter
    from evals.adapters.tau_bench_adapter import TauBenchAdapter

    for adapter_cls in (GAIAAdapter, AgentBenchAdapter, ToolBenchAdapter, TauBenchAdapter):
        rows = adapter_cls().load(limit=2)
        assert rows
        assert len(rows) <= 2
        assert all(row["source"]["kind"] == "open_source_dataset" for row in rows)
        assert all(row["source"]["license"] == "UNKNOWN_NEEDS_REVIEW" for row in rows)


def test_evaluators_detect_duplicate_tool_calls_and_missing_nodes():
    from evals.evaluators.no_loop import evaluate_no_loop
    from evals.evaluators.node_path import evaluate_node_path
    from evals.evaluators.tool_repetition import summarize_tool_repetition

    case = {
        "id": "case_001",
        "expected_nodes": ["controller", "planner", "reviewer"],
        "expected_tools": ["rag_search"],
        "max_steps": 6,
        "loop_rules": {
            "max_total_steps": 6,
            "max_same_tool_calls": 2,
            "max_same_tool_same_args": 1,
            "max_no_state_change_steps": 2,
        },
    }
    run = {
        "case_id": "case_001",
        "status": "running",
        "trace": [
            {"node": "controller", "state_before": "a", "state_after": "b"},
            {"node": "planner", "state_before": "b", "state_after": "c"},
            {"tool_name": "rag_search", "tool_arguments": {"query": "same"}, "state_before": "c", "state_after": "c"},
            {"tool_name": "rag_search", "tool_arguments": {"query": "same"}, "state_before": "c", "state_after": "c"},
            {"tool_name": "rag_search", "tool_arguments": {"query": "same"}, "state_before": "c", "state_after": "c"},
        ],
    }

    no_loop = evaluate_no_loop(case, run)
    repetition = summarize_tool_repetition(run)
    node_path = evaluate_node_path(case, run)

    assert no_loop["passed"] is False
    assert no_loop["violations"]["same_tool_calls"]["rag_search"] == 3
    assert no_loop["violations"]["same_tool_same_args"]
    assert repetition["tool_call_count"] == 3
    assert repetition["duplicate_tool_call_ratio"] == 2 / 3
    assert node_path["passed"] is False
    assert node_path["missing_nodes"] == ["reviewer"]


def test_run_eval_local_outputs_required_summary_metrics():
    completed = subprocess.run(
        [sys.executable, "evals/scripts/run_eval_local.py", "--dataset", str(GENERATED_DATASET)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(completed.stdout)

    assert report["summary"]["case_count"] == 65
    for metric in {
        "termination_rate",
        "loop_rate",
        "avg_tool_calls",
        "duplicate_tool_call_ratio",
        "max_step_violation_rate",
        "stuck_running_count",
    }:
        assert metric in report["summary"]
    assert len(report["cases"]) == 65
    assert all("passed" in row for row in report["cases"])


def test_langsmith_example_conversion_contract():
    from evals.scripts.sync_langsmith import example_from_case, expected_tools_for_metadata

    case = _load_jsonl(GENERATED_DATASET)[0]
    example = example_from_case(case)

    assert example["inputs"] == {"user_query": case["user_query"]}
    assert example["reference_outputs"] == {
        "gold_behavior": case["gold_behavior"],
        "gold_answer": case.get("gold_answer"),
    }
    assert example["metadata"]["category"] == case["category"]
    assert example["metadata"]["source"] == case["source"]

    split_case = _load_jsonl(FORMAL_DATASET)[0]
    split_example = example_from_case(split_case)
    assert split_example["metadata"]["expected_tool_categories"] == split_case["expected_tool_categories"]
    assert split_example["metadata"]["expected_project_tools"] == split_case["expected_project_tools"]
    assert split_example["metadata"]["expected_source_tools"] == split_case["expected_source_tools"]
    assert split_example["metadata"]["expected_tools"] == expected_tools_for_metadata(split_case)
    assert expected_tools_for_metadata(
        {"expected_tools": [], "expected_project_tools": ["project"], "expected_source_tools": ["source"]}
    ) == ["project", "source"]


def test_open_source_adapter_imports_real_local_sample_with_review_metadata(tmp_path):
    from evals.adapters.gaia_adapter import GAIAAdapter

    sample_path = tmp_path / "gaia_sample.jsonl"
    sample_path.write_text(
        json.dumps(
            {
                "task_id": "gaia-validation-1",
                "Question": "Find the answer using web evidence.",
                "Final answer": "42",
                "Level": "1",
                "file_name": "artifact.pdf",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = GAIAAdapter().load(
        input_path=sample_path,
        split="validation",
        limit=1,
        dry_run=False,
        license="CC-BY-4.0",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "gaia_validation_001"
    assert row["source"]["original_id"] == "gaia-validation-1"
    assert row["source"]["license"] == "CC-BY-4.0"
    assert row["source"]["split"] == "validation"
    assert row["gold_answer"] == {"answer": "42"}
    assert row["label_review"]["status"] == "needs_review"
    assert row["expected_tools"] == ["web_search", "local_file_read"]


def test_open_source_adapter_blocks_unknown_license_for_formal_test_split(tmp_path):
    from evals.adapters.agentbench_adapter import AgentBenchAdapter

    sample_path = tmp_path / "agentbench.json"
    sample_path.write_text(
        json.dumps([{"id": "ab-1", "instruction": "Use a browser to answer."}], ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        AgentBenchAdapter().load(
            input_path=sample_path,
            split="test",
            limit=1,
            dry_run=False,
            license="UNKNOWN_NEEDS_REVIEW",
        )
    except ValueError as exc:
        assert "license" in str(exc)
    else:
        raise AssertionError("expected unknown-license test split import to fail")


def test_import_open_source_script_outputs_jsonl(tmp_path):
    gaia = tmp_path / "gaia.jsonl"
    agentbench = tmp_path / "agentbench.jsonl"
    toolbench = tmp_path / "toolbench.jsonl"
    output = tmp_path / "open_source_agent_eval.jsonl"
    gaia.write_text(
        json.dumps({"task_id": "g1", "Question": "Q?", "Final answer": "A"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    agentbench.write_text(
        json.dumps({"id": "a1", "instruction": "Do a web task"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    toolbench.write_text(
        json.dumps(
            {
                "query_id": "t1",
                "query": "Call a weather API.",
                "api_list": [{"name": "weather_api"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "evals/scripts/import_open_source_datasets.py",
            "--gaia-input",
            str(gaia),
            "--gaia-license",
            "CC-BY-4.0",
            "--agentbench-input",
            str(agentbench),
            "--agentbench-license",
            "Apache-2.0",
            "--toolbench-input",
            str(toolbench),
            "--toolbench-license",
            "Apache-2.0",
            "--split",
            "validation",
            "--limit-per-source",
            "1",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    rows = _load_jsonl(output)
    assert len(rows) == 3
    assert {row["source"]["dataset"] for row in rows} == {"GAIA", "AgentBench", "ToolBench"}
    assert all(row["label_review"]["status"] == "needs_review" for row in rows)


def test_downloaded_open_source_dataset_contains_real_public_samples():
    rows = _load_jsonl(OPEN_SOURCE_DATASET)

    assert len(rows) == 15
    assert {row["source"]["dataset"] for row in rows} == {"GAIA", "AgentBench", "ToolBench"}
    assert {
        row["source"]["license"] for row in rows if row["source"]["dataset"] == "GAIA"
    } == {"UNKNOWN_NEEDS_REVIEW"}
    assert {
        row["source"]["license"] for row in rows if row["source"]["dataset"] != "GAIA"
    } == {"Apache-2.0"}
    assert all(row["source"]["split"] == "validation" for row in rows)
    assert all(row["source"]["original_id"] for row in rows)
    assert all(row["label_review"]["status"] == "needs_review" for row in rows)
    assert (ROOT / "evals" / "datasets" / "source_samples" / "gaia_2023_level1_validation.jsonl").exists()
    assert (ROOT / "evals" / "datasets" / "source_samples" / "agentbench_knowledgegraph_dev.jsonl").exists()
    assert (ROOT / "evals" / "datasets" / "source_samples" / "toolbench_g1_query.jsonl").exists()


def test_downloader_skips_gated_gaia_without_token(tmp_path, monkeypatch):
    from evals.scripts.download_open_source_samples import try_download_gaia

    monkeypatch.delenv("HF_TOKEN", raising=False)
    path, note = try_download_gaia(tmp_path / "gaia.jsonl", limit=1, split="test")

    assert path is None
    assert "HF_TOKEN" in note


def test_split_builder_outputs_formal_dev_staging_templates():
    subprocess.run(
        [sys.executable, "evals/scripts/build_dataset_splits.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    formal = _load_jsonl(FORMAL_DATASET)
    dev = _load_jsonl(DEV_DATASET)
    staging = _load_jsonl(STAGING_DATASET)
    templates = _load_jsonl(TEMPLATES_DATASET)

    assert formal
    assert len(dev) == 60
    assert len(staging) == 15
    assert len(templates) == 20

    for row in formal:
        assert row["active"] is True
        assert row["split"] == "formal"
        assert row["label_review"]["status"] == "approved"
        assert row["source"]["license"] != "UNKNOWN_NEEDS_REVIEW"
        assert not row.get("placeholder", False)
        assert not row.get("adapter_metadata", {}).get("dry_run", False)

    assert not any(row["id"].endswith("_slot_001") or "_slot_" in row["id"] for row in dev)
    assert all(row["split"] == "dev" for row in dev)
    assert all(row["source"]["kind"] != "open_source_dataset" or "_slot_" not in row["id"] for row in dev)

    assert {row["source"]["kind"] for row in staging} == {"open_source_dataset"}
    assert all(row["split"] == "staging" for row in staging)
    assert any(row["source"]["license"] == "UNKNOWN_NEEDS_REVIEW" for row in staging)
    assert all(row["label_review"]["status"] == "needs_review" for row in staging)

    assert all(row["split"] == "templates" for row in templates)
    assert all(row.get("placeholder") is True for row in templates)
    assert all(row.get("adapter_metadata", {}).get("dry_run") is True for row in templates)

    for row in formal + dev + staging + templates:
        assert row["active"] is True
        assert "source" in row
        assert "max_same_tool_same_args" in row["loop_rules"]
        assert row["scoring_mode"] == "weighted_static"
        assert "expected_tool_categories" in row
        assert "expected_project_tools" in row
        assert "expected_source_tools" in row
        assert "expected_tools" not in row


def test_validate_dataset_rejects_invalid_formal_rows(tmp_path):
    invalid = {
        "id": "bad_formal_001",
        "category": "loop_stability",
        "difficulty": "medium",
        "user_query": "q",
        "gold_behavior": "b",
        "expected_nodes": ["controller"],
        "expected_tool_categories": [],
        "expected_project_tools": [],
        "expected_source_tools": [],
        "max_steps": 4,
        "loop_rules": {
            "max_total_steps": 4,
            "max_same_tool_calls": 1,
            "max_same_tool_same_args": 1,
            "max_no_state_change_steps": 2,
        },
        "scoring": {"task_success": 1.0},
        "scoring_mode": "weighted_static",
        "active": True,
        "split": "formal",
        "source": {
            "kind": "open_source_dataset",
            "dataset": "GAIA",
            "original_id": "x",
            "source_url": "u",
            "license": "UNKNOWN_NEEDS_REVIEW",
            "split": "validation",
            "transformation": "t",
        },
        "label_review": {
            "status": "needs_review",
            "reviewer": None,
            "reviewed_at": None,
            "confidence": "low",
            "notes": "n",
        },
    }
    path = tmp_path / "formal.jsonl"
    path.write_text(json.dumps(invalid, ensure_ascii=False) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "evals/scripts/validate_dataset.py", str(path), "--split", "formal"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    assert "UNKNOWN_NEEDS_REVIEW" in completed.stderr
    assert "needs_review" in completed.stderr
