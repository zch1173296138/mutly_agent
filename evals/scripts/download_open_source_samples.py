from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.scripts.import_open_source_datasets import import_rows, write_jsonl
from evals.scripts.validate_dataset import validate_dataset


SAMPLES_DIR = ROOT / "evals" / "datasets" / "source_samples"
OUTPUT_PATH = ROOT / "evals" / "datasets" / "generated" / "open_source_agent_eval.jsonl"

AGENTBENCH_LICENSE = "Apache-2.0"
TOOLBENCH_LICENSE = "Apache-2.0"
GAIA_LICENSE = "UNKNOWN_NEEDS_REVIEW"

AGENTBENCH_SAMPLE = "data/knowledgegraph/dev.json"
TOOLBENCH_SAMPLE = "data_example/instruction/G1_query.json"
GAIA_MODELSCOPE_DATASET = "AI-ModelScope/GAIA"
GAIA_MODELSCOPE_PARQUET = "2023/validation/metadata.level1.parquet"


def github_contents(repo: str, path: str) -> tuple[str, str]:
    url = f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["html_url"]


def write_small_jsonl(source_text: str, output_path: Path, *, limit: int) -> None:
    data = json.loads(source_text)
    if isinstance(data, dict) and "examples" in data:
        rows = data["examples"]
    elif isinstance(data, dict):
        rows = []
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    rows.append({"id": f"{key}:{len(rows) + 1}", "instruction": str(item)})
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError(f"unsupported sample shape: {output_path}")
    rows = rows[:limit]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )


def _run_modelscope_download(dataset: str, file_path: str, local_dir: Path) -> bool:
    import subprocess

    local_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "modelscope",
            "download",
            "--dataset",
            dataset,
            file_path,
            "--local_dir",
            str(local_dir),
        ],
        text=True,
        capture_output=True,
        timeout=300,
    )
    return completed.returncode == 0


def _write_gaia_jsonl_from_parquet(parquet_path: Path, output_path: Path, *, limit: int) -> None:
    import pandas as pd

    df = pd.read_parquet(parquet_path).head(limit)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = df.to_dict(orient="records")
    output_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in records)
        + "\n",
        encoding="utf-8",
    )


def try_download_gaia(output_path: Path, *, limit: int, split: str) -> tuple[Path | None, str | None]:
    if split == "validation":
        modelscope_dir = output_path.parent / "modelscope_gaia"
        parquet_path = modelscope_dir / GAIA_MODELSCOPE_PARQUET
        if parquet_path.exists() or _run_modelscope_download(
            GAIA_MODELSCOPE_DATASET,
            GAIA_MODELSCOPE_PARQUET,
            modelscope_dir,
        ):
            try:
                _write_gaia_jsonl_from_parquet(parquet_path, output_path, limit=limit)
                return output_path, (
                    "GAIA imported from ModelScope validation metadata; license remains "
                    "UNKNOWN_NEEDS_REVIEW and labels require review."
                )
            except Exception as exc:
                return None, f"GAIA ModelScope parquet conversion failed: {exc}"

    token = os.getenv("HF_TOKEN")
    if not token:
        return None, "GAIA skipped: HF_TOKEN is not set and ModelScope validation metadata was unavailable."

    params = urllib.parse.urlencode(
        {
            "dataset": "gaia-benchmark/GAIA",
            "config": "2023_level1",
            "split": split,
            "offset": 0,
            "length": min(limit, 10),
        }
    )
    request = urllib.request.Request(
        f"https://datasets-server.huggingface.co/rows?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, f"GAIA skipped: Dataset Viewer returned HTTP {exc.code}."

    rows = [item["row"] for item in data.get("rows", [])][:limit]
    if not rows:
        return None, "GAIA skipped: no rows returned by Dataset Viewer."
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    return output_path, None


def download_and_import(limit_per_source: int, split: str, output: Path) -> dict[str, Any]:
    samples_dir = SAMPLES_DIR
    samples_dir.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    agentbench_text, agentbench_url = github_contents("THUDM/AgentBench", AGENTBENCH_SAMPLE)
    toolbench_text, toolbench_url = github_contents("OpenBMB/ToolBench", TOOLBENCH_SAMPLE)

    agentbench_path = samples_dir / "agentbench_knowledgegraph_dev.jsonl"
    toolbench_path = samples_dir / "toolbench_g1_query.jsonl"
    write_small_jsonl(agentbench_text, agentbench_path, limit=limit_per_source)
    write_small_jsonl(toolbench_text, toolbench_path, limit=limit_per_source)

    gaia_path, gaia_note = try_download_gaia(
        samples_dir / "gaia_2023_level1_validation.jsonl",
        limit=limit_per_source,
        split=split,
    )
    if gaia_note:
        notes.append(gaia_note)

    import_args = argparse.Namespace(
        gaia_input=gaia_path,
        agentbench_input=agentbench_path,
        toolbench_input=toolbench_path,
        split=split,
        limit_per_source=limit_per_source,
        output=output,
        gaia_license=GAIA_LICENSE,
        agentbench_license=AGENTBENCH_LICENSE,
        toolbench_license=TOOLBENCH_LICENSE,
        gaia_source_url="https://huggingface.co/datasets/gaia-benchmark/GAIA",
        agentbench_source_url=agentbench_url,
        toolbench_source_url=toolbench_url,
    )
    rows = import_rows(import_args)
    write_jsonl(output, rows)
    errors = validate_dataset(output)
    if errors:
        raise RuntimeError("\n".join(errors))
    return {
        "output": str(output),
        "case_count": len(rows),
        "samples": {
            "gaia": str(gaia_path) if gaia_path else None,
            "agentbench": str(agentbench_path),
            "toolbench": str(toolbench_path),
        },
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download small public samples and build open_source_agent_eval.jsonl."
    )
    parser.add_argument("--limit-per-source", type=int, default=5)
    parser.add_argument("--split", default="validation")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    result = download_and_import(args.limit_per_source, args.split, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
