from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "id",
    "category",
    "difficulty",
    "user_query",
    "gold_behavior",
    "expected_nodes",
    "max_steps",
    "loop_rules",
    "scoring",
}

TOOL_FIELD_GROUP = {
    "expected_tools",
    "expected_tool_categories",
    "expected_project_tools",
    "expected_source_tools",
}

SOURCE_FIELDS = {
    "kind",
    "dataset",
    "original_id",
    "source_url",
    "license",
    "split",
    "transformation",
}

LOOP_RULE_FIELDS = {
    "max_total_steps",
    "max_same_tool_calls",
    "max_no_state_change_steps",
}

NORMALIZED_LOOP_RULE_FIELDS = LOOP_RULE_FIELDS | {"max_same_tool_same_args"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"{path}:{line_no}: blank lines are not allowed")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no}: each JSONL row must be an object")
        rows.append(row)
    return rows


def validate_case(
    row: dict[str, Any],
    *,
    line_no: int,
    dataset_split: str | None = None,
) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(row))
    if missing:
        errors.append(f"line {line_no}: missing required fields: {', '.join(missing)}")

    if not str(row.get("id", "")).strip():
        errors.append(f"line {line_no}: id must be non-empty")
    if not str(row.get("user_query", "")).strip():
        errors.append(f"line {line_no}: user_query must be non-empty")
    if not str(row.get("gold_behavior", "")).strip():
        errors.append(f"line {line_no}: gold_behavior must be non-empty")
    if "expected_nodes" in row and not isinstance(row["expected_nodes"], list):
        errors.append(f"line {line_no}: expected_nodes must be a list")
    if not (TOOL_FIELD_GROUP & set(row)):
        errors.append(
            "line "
            f"{line_no}: expected_tools or expected_tool_categories/expected_project_tools/"
            "expected_source_tools must be present"
        )
    for field in TOOL_FIELD_GROUP & set(row):
        if not isinstance(row[field], list):
            errors.append(f"line {line_no}: {field} must be a list")
    if "max_steps" in row and (
        not isinstance(row["max_steps"], int) or row["max_steps"] < 1
    ):
        errors.append(f"line {line_no}: max_steps must be a positive integer")

    loop_rules = row.get("loop_rules")
    if isinstance(loop_rules, dict):
        required_loop = NORMALIZED_LOOP_RULE_FIELDS if dataset_split in {
            "formal",
            "dev",
            "staging",
            "templates",
        } else LOOP_RULE_FIELDS
        missing_loop = sorted(required_loop - set(loop_rules))
        if missing_loop:
            errors.append(f"line {line_no}: loop_rules missing: {', '.join(missing_loop)}")
    elif "loop_rules" in row:
        errors.append(f"line {line_no}: loop_rules must be an object")

    scoring = row.get("scoring")
    if "scoring" in row and not isinstance(scoring, dict):
        errors.append(f"line {line_no}: scoring must be an object")

    source = row.get("source")
    if source is not None:
        if not isinstance(source, dict):
            errors.append(f"line {line_no}: source must be an object")
        else:
            missing_source = sorted(SOURCE_FIELDS - set(source))
            if missing_source:
                errors.append(
                    f"line {line_no}: source missing required fields: {', '.join(missing_source)}"
                )
            for field in SOURCE_FIELDS & set(source):
                if not str(source.get(field, "")).strip():
                    errors.append(f"line {line_no}: source.{field} must be non-empty")

    if dataset_split in {"formal", "dev", "staging", "templates"}:
        if "active" not in row:
            errors.append(f"line {line_no}: active is required for {dataset_split} split")
        if row.get("split") != dataset_split:
            errors.append(f"line {line_no}: split must be {dataset_split}")
        if not row.get("scoring_mode"):
            errors.append(f"line {line_no}: scoring_mode is required")
        for field in ("expected_tool_categories", "expected_project_tools", "expected_source_tools"):
            if field not in row:
                errors.append(f"line {line_no}: {field} is required for {dataset_split} split")
        if "expected_tools" in row:
            errors.append(f"line {line_no}: expected_tools must be split into normalized tool fields")

    if dataset_split == "formal":
        review_status = (row.get("label_review") or {}).get("status")
        source = row.get("source") or {}
        if row.get("active") is not True:
            errors.append(f"line {line_no}: formal rows must have active == true")
        if source.get("license") == "UNKNOWN_NEEDS_REVIEW":
            errors.append(f"line {line_no}: formal rows cannot contain UNKNOWN_NEEDS_REVIEW license")
        if (row.get("adapter_metadata") or {}).get("dry_run") is True:
            errors.append(f"line {line_no}: formal rows cannot contain dry_run samples")
        if row.get("placeholder") is True or "_slot_" in str(row.get("id", "")):
            errors.append(f"line {line_no}: formal rows cannot contain placeholder samples")
        if review_status == "needs_review":
            errors.append(f"line {line_no}: formal rows cannot contain needs_review samples")
        if review_status != "approved":
            errors.append(f"line {line_no}: formal rows must have label_review.status == approved")
    return errors


def infer_dataset_split(path: Path) -> str | None:
    parts = {part.lower() for part in path.parts}
    for split in ("formal", "dev", "staging", "templates"):
        if split in parts:
            return split
    return None


def validate_dataset(path: Path, *, dataset_split: str | None = None) -> list[str]:
    if not path.exists():
        return [f"dataset does not exist: {path}"]
    dataset_split = dataset_split or infer_dataset_split(path)
    errors: list[str] = []
    seen_ids: set[str] = set()
    try:
        rows = load_jsonl(path)
    except ValueError as exc:
        return [str(exc)]
    for line_no, row in enumerate(rows, 1):
        errors.extend(validate_case(row, line_no=line_no, dataset_split=dataset_split))
        case_id = str(row.get("id", ""))
        if case_id in seen_ids:
            errors.append(f"line {line_no}: duplicate id: {case_id}")
        seen_ids.add(case_id)
    if not rows:
        errors.append("dataset is empty")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate multy_agent eval JSONL dataset.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--split", choices=["formal", "dev", "staging", "templates"], default=None)
    args = parser.parse_args()

    errors = validate_dataset(args.dataset, dataset_split=args.split)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(1)
    print(f"valid: {args.dataset}")


if __name__ == "__main__":
    main()
