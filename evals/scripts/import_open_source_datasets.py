from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.adapters.agentbench_adapter import AgentBenchAdapter
from evals.adapters.gaia_adapter import GAIAAdapter
from evals.adapters.toolbench_adapter import ToolBenchAdapter
from evals.adapters.base import UNKNOWN_LICENSE
from evals.scripts.validate_dataset import validate_dataset


DEFAULT_OUTPUT = ROOT / "evals" / "datasets" / "generated" / "open_source_agent_eval.jsonl"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )


def import_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    jobs = [
        (
            GAIAAdapter(),
            args.gaia_input,
            args.gaia_license,
            args.gaia_source_url,
        ),
        (
            AgentBenchAdapter(),
            args.agentbench_input,
            args.agentbench_license,
            args.agentbench_source_url,
        ),
        (
            ToolBenchAdapter(),
            args.toolbench_input,
            args.toolbench_license,
            args.toolbench_source_url,
        ),
    ]
    for adapter, input_path, license_value, source_url in jobs:
        if input_path is None:
            continue
        rows.extend(
            adapter.load(
                input_path=input_path,
                dry_run=False,
                split=args.split,
                limit=args.limit_per_source,
                license=license_value,
                source_url=source_url,
            )
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import reviewed small samples from GAIA, AgentBench, and ToolBench."
    )
    parser.add_argument("--gaia-input", type=Path)
    parser.add_argument("--agentbench-input", type=Path)
    parser.add_argument("--toolbench-input", type=Path)
    parser.add_argument("--split", required=True, help="Source split to record on every imported case.")
    parser.add_argument("--limit-per-source", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    parser.add_argument("--gaia-license", default=UNKNOWN_LICENSE)
    parser.add_argument("--agentbench-license", default=UNKNOWN_LICENSE)
    parser.add_argument("--toolbench-license", default=UNKNOWN_LICENSE)

    parser.add_argument("--gaia-source-url", default=GAIAAdapter.source_url)
    parser.add_argument("--agentbench-source-url", default=AgentBenchAdapter.source_url)
    parser.add_argument("--toolbench-source-url", default=ToolBenchAdapter.source_url)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = import_rows(args)
    if not rows:
        raise SystemExit("No input files were provided; nothing imported.")

    write_jsonl(args.output, rows)
    errors = validate_dataset(args.output)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"output": str(args.output), "case_count": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
