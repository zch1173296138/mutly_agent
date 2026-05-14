from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.scripts.validate_dataset import validate_dataset


SEED_INPUT = ROOT / "evals" / "datasets" / "seed" / "eval_cases.jsonl"
GENERATED_INPUT = ROOT / "evals" / "datasets" / "generated" / "multy_agent_loop_eval.jsonl"
OPEN_SOURCE_INPUT = ROOT / "evals" / "datasets" / "generated" / "open_source_agent_eval.jsonl"

FORMAL_OUTPUT = ROOT / "evals" / "datasets" / "formal" / "formal_loop_minimal.jsonl"
DEV_OUTPUT = ROOT / "evals" / "datasets" / "dev" / "dev_loop_eval.jsonl"
STAGING_OUTPUT = ROOT / "evals" / "datasets" / "staging" / "open_source_agent_eval.jsonl"
TEMPLATES_OUTPUT = ROOT / "evals" / "datasets" / "templates" / "open_source_slots.jsonl"

PROJECT_INTERNAL_LICENSE = "project_internal"
UNKNOWN_LICENSE = "UNKNOWN_NEEDS_REVIEW"

PROJECT_TOOLS = {
    "rag_search",
    "pdf_parser",
    "calculator",
    "local_file_read",
    "web_search",
    "send_email",
}

TOOL_CATEGORY_MAP = {
    "rag_search": "retrieval",
    "pdf_parser": "document_parsing",
    "calculator": "calculation",
    "local_file_read": "filesystem",
    "web_search": "web",
    "database_query": "database",
    "api_tool": "api",
    "domain_tool": "domain_api",
    "send_email": "side_effect",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )


def source_for_internal(row: dict[str, Any], *, kind: str) -> dict[str, str]:
    return {
        "kind": kind,
        "dataset": "multy_agent",
        "original_id": str(row["id"]),
        "source_url": "evals/datasets/seed/eval_cases.jsonl",
        "license": PROJECT_INTERNAL_LICENSE,
        "split": "seed" if kind == "seed_dataset" else "generated",
        "transformation": "normalized_for_split:v1",
    }


def is_placeholder(row: dict[str, Any]) -> bool:
    return "_slot_" in str(row.get("id", "")) or row.get("source", {}).get("transformation") == "reserved_adapter_slot_no_dataset_content"


def is_dry_run(row: dict[str, Any]) -> bool:
    return bool((row.get("adapter_metadata") or {}).get("dry_run"))


def normalize_source(row: dict[str, Any]) -> dict[str, Any]:
    source = deepcopy(row.get("source") or {})
    if not source:
        return source_for_internal(row, kind="seed_dataset")
    if source.get("kind") in {"seed_dataset", "synthetic_from_seed"}:
        source["license"] = PROJECT_INTERNAL_LICENSE
        source.setdefault("dataset", "multy_agent")
    source.setdefault("kind", "unknown")
    source.setdefault("dataset", "unknown")
    source.setdefault("original_id", str(row.get("id", "")))
    source.setdefault("source_url", "")
    source.setdefault("license", UNKNOWN_LICENSE)
    source.setdefault("split", "unknown")
    source.setdefault("transformation", "normalized_for_split:v1")
    return source


def split_tools(row: dict[str, Any]) -> dict[str, list[str]]:
    tools = list(row.get("expected_tools") or [])
    categories: list[str] = []
    project_tools: list[str] = []
    source_tools: list[str] = []
    for tool in tools:
        if tool in PROJECT_TOOLS:
            project_tools.append(tool)
        else:
            source_tools.append(tool)
        categories.append(TOOL_CATEGORY_MAP.get(tool, "source_tool" if tool not in PROJECT_TOOLS else "project_tool"))
    return {
        "expected_tool_categories": sorted(set(categories)),
        "expected_project_tools": project_tools,
        "expected_source_tools": source_tools,
    }


def normalize_case(row: dict[str, Any], *, split: str) -> dict[str, Any]:
    normalized = deepcopy(row)
    normalized["active"] = bool(normalized.get("active", True))
    normalized["split"] = split
    normalized["source"] = normalize_source(normalized)
    normalized["placeholder"] = is_placeholder(normalized)
    normalized["loop_rules"] = dict(normalized.get("loop_rules") or {})
    normalized["loop_rules"].setdefault(
        "max_same_tool_same_args",
        normalized["loop_rules"].get("max_same_tool_calls", 1),
    )
    normalized["scoring_mode"] = normalized.get("scoring_mode", "weighted_static")
    normalized.update(split_tools(normalized))
    normalized.pop("expected_tools", None)
    normalized.setdefault(
        "label_review",
        {
            "status": "needs_review",
            "reviewer": None,
            "reviewed_at": None,
            "confidence": "low",
            "notes": "Pending review.",
        },
    )
    return normalized


def dedupe_by_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        case_id = str(row["id"])
        if case_id not in seen:
            order.append(case_id)
        seen[case_id] = row
    return [seen[case_id] for case_id in order]


def build_splits() -> dict[str, list[dict[str, Any]]]:
    seed_rows = load_jsonl(SEED_INPUT)
    generated = load_jsonl(GENERATED_INPUT)
    open_source = load_jsonl(OPEN_SOURCE_INPUT)

    template_source = [row for row in generated if row.get("source", {}).get("kind") == "open_source_dataset" and is_placeholder(row)]
    synthetic_source = [
        row
        for row in generated
        if row.get("source", {}).get("kind") == "synthetic_from_seed"
    ]
    dev_source = [
        *seed_rows,
        *synthetic_source,
        *open_source,
    ]
    dev_source = dedupe_by_id(dev_source)

    staging_source = [
        row
        for row in open_source
        if row.get("source", {}).get("kind") == "open_source_dataset"
        and (
            row.get("source", {}).get("license") == UNKNOWN_LICENSE
            or (row.get("label_review") or {}).get("status") != "approved"
            or not str(row.get("gold_behavior", "")).strip()
        )
    ]

    normalized_dev = [normalize_case(row, split="dev") for row in dev_source]
    normalized_staging = [normalize_case(row, split="staging") for row in staging_source]
    normalized_templates = [normalize_case(row, split="templates") for row in template_source]

    formal = [
        row
        for row in normalized_dev
        if row["active"] is True
        and not row.get("placeholder", False)
        and not is_dry_run(row)
        and (row.get("label_review") or {}).get("status") == "approved"
        and (
            row["source"].get("license") == PROJECT_INTERNAL_LICENSE
            or row["source"].get("license") != UNKNOWN_LICENSE
        )
    ]
    normalized_formal = []
    for row in formal:
        formal_row = deepcopy(row)
        formal_row["split"] = "formal"
        normalized_formal.append(formal_row)

    return {
        "formal": normalized_formal,
        "dev": normalized_dev,
        "staging": normalized_staging,
        "templates": normalized_templates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build formal/dev/staging/templates eval datasets.")
    parser.add_argument("--formal-output", type=Path, default=FORMAL_OUTPUT)
    parser.add_argument("--dev-output", type=Path, default=DEV_OUTPUT)
    parser.add_argument("--staging-output", type=Path, default=STAGING_OUTPUT)
    parser.add_argument("--templates-output", type=Path, default=TEMPLATES_OUTPUT)
    args = parser.parse_args()

    splits = build_splits()
    outputs = {
        "formal": args.formal_output,
        "dev": args.dev_output,
        "staging": args.staging_output,
        "templates": args.templates_output,
    }
    for split_name, path in outputs.items():
        write_jsonl(path, splits[split_name])
        errors = validate_dataset(path, dataset_split=split_name)
        if errors:
            raise SystemExit("\n".join(errors))

    print(
        json.dumps(
            {split_name: {"path": str(outputs[split_name]), "count": len(rows)} for split_name, rows in splits.items()},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
