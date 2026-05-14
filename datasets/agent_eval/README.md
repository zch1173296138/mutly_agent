# Agent Evaluation Dataset

This directory contains a versioned evaluation dataset for comparing a LangGraph multi-agent workflow with a linear ReAct baseline. The dataset is for evaluation, not model training.

## Files

- `eval_cases.jsonl`: one JSON evaluation case per line.
- `schema.json`: machine-readable case contract.
- `README.md`: labeling, review, and extension rules.
- `sources/`: frozen local sources used by evidence quotes.

## Core Principle

A/B conclusions must come from observed runner traces. Dataset fields such as `ab_test.hypothesis_pass`, `hypothesis_loop`, and `hypothesis_stop_reason` are hypotheses only and must not be used as proof that LangGraph solved a ReAct loop.

For formal claims, the selected cases must also be reviewed. A case is formal evidence only when:

- `label_review.status == "approved"`
- `label_review.confidence != "low"`
- `label_review.reviewer`, `label_review.reviewed_at`, and `label_review.notes` are non-empty
- material `gold_answer` fields are backed by `gold_answer_claims`

Draft, rejected, missing-review, and low-confidence cases may be used for development runs, but reports containing them must be treated as non-evidentiary.

## Case Fields

| Field | Description |
| --- | --- |
| `id` | Unique case ID, formatted as `<prefix>_<number>`. |
| `category` | Evaluation category from `schema.json`. |
| `difficulty` | `easy`, `medium`, or `hard`. |
| `user_query` | User task sent to each agent variant. |
| `available_sources` | Frozen local source metadata. |
| `gold_answer` | Structured expected answer or judgment. |
| `gold_answer_claims` | Claim-level support for approved cases. |
| `gold_behavior` | Expected behavior, including completion, refusal, clarification, suspension, or stop behavior. |
| `evidence` | Source quotes that must match local source files exactly. |
| `expected_nodes` | Expected LangGraph node coverage; this must not penalize ReAct for missing LangGraph-only nodes. |
| `expected_tools` | Expected tool calls or attempted tool calls. |
| `max_steps` | Case-level execution budget. |
| `loop_rules` | Loop thresholds: total steps, same-tool same-argument calls, and no-state-change steps. |
| `ab_test` | Optional A/B hypothesis metadata. Must be `hypothesis_only: true`. |
| `label_review` | Human review status used to decide whether the case can support formal conclusions. |
| `scoring` | Metric weights. Values must sum to 1.0. |
| `calculation` | Recomputable numeric metadata for financial cases. |

## Label Lifecycle

- `draft`: early sample; structure may still change.
- `needs_review`: structurally usable but not approved for formal claims.
- `approved`: reviewed and eligible for formal reporting when confidence is not `low`.
- `rejected`: retained for audit or redesign, but excluded from formal reporting.

Recommended workflow:

1. Freeze the source material under `datasets/agent_eval/sources/` or another stable local path.
2. Write `user_query`, `gold_answer`, `gold_behavior`, `evidence`, `loop_rules`, and `scoring`.
3. Add `gold_answer_claims` for every material `gold_answer` field.
4. Set `label_review.status` to `needs_review`.
5. Run dataset validation tests.
6. Have a human reviewer inspect the source, gold answer, behavior, evidence, and loop thresholds.
7. Mark the case `approved` only after review.

## Gold Answer Claims

Approved cases must explain why each material gold answer field is correct.

Supported claim types:

- `direct_evidence`: the claim is directly supported by one or more local evidence quotes.
- `calculation`: the claim is supported by recomputable calculation metadata.
- `reviewed_inference`: the claim requires human interpretation, but the reviewer confirmed it is supported by evidence.

Example:

```json
{
  "field": "should_stop",
  "value": true,
  "support_type": "reviewed_inference",
  "evidence": [
    {
      "source_id": "finance_snapshot",
      "quote": "This snapshot covers Apple fiscal years 2022, 2023, and 2024 only.",
      "locator": "Apple Inc."
    }
  ],
  "review_note": "The requested 2026 year is outside the source coverage, so the correct behavior is to stop."
}
```

## Formal Versus Development Runs

Development run:

```bash
uv run python scripts/run_agent_ab_eval.py --case-id loop_001
```

Formal reviewed run:

```bash
uv run python scripts/run_agent_ab_eval.py --approved-only --formal --include-react-worker-ablation
```

`--approved-only` filters the selected cases to reviewed, non-low-confidence cases. `--formal` marks the report as intended for formal evidence; if unreviewed cases are included, the report is marked non-evidentiary.

## Open-Source Dataset Adaptation

Open-source benchmarks such as HotpotQA, GAIA, ToolBench, BFCL, or tau-bench can provide useful task ideas, but their rows must not enter the golden set directly.

To adapt an external sample:

1. Copy or summarize the needed source material into a frozen local source file.
2. Convert the task into the local `eval_cases.jsonl` schema.
3. Add local `evidence.quote` entries that match local files exactly.
4. Add `gold_answer_claims`.
5. Add `loop_rules` when the sample is used for loop stability.
6. Set `label_review.status` to `needs_review`.
7. Have a human reviewer approve it before formal use.

Draft template:

```json
{
  "id": "external_001",
  "category": "rag_retrieval",
  "difficulty": "medium",
  "user_query": "Answer the adapted external benchmark question using the frozen source.",
  "available_sources": [
    {
      "id": "external_source",
      "type": "local_doc",
      "path": "datasets/agent_eval/sources/external_sample_001.md",
      "note": "Frozen local adaptation of an external benchmark source"
    }
  ],
  "gold_answer": {
    "answer": "expected answer"
  },
  "gold_answer_claims": [
    {
      "field": "answer",
      "value": "expected answer",
      "support_type": "direct_evidence",
      "evidence": [
        {
          "source_id": "external_source",
          "quote": "exact quote from local source"
        }
      ]
    }
  ],
  "label_review": {
    "status": "needs_review",
    "reviewer": null,
    "reviewed_at": null,
    "confidence": "low",
    "notes": "Imported draft; pending human review."
  }
}
```

## Extension Rules

1. Every line in `eval_cases.jsonl` must be valid JSON.
2. `id` must be unique.
3. `category` must be declared in `schema.json`.
4. `scoring` values must sum to 1.0.
5. `loop_stability` cases must explicitly state the correct stop, suspend, clarify, or missing-information behavior.
6. Every non-`none` source must point to a real local file.
7. Every `evidence.quote` must be found verbatim in the referenced source.
8. Financial calculation samples must include recomputable `calculation` metadata.
9. Formal reports must use approved, non-low-confidence cases or be marked non-evidentiary.
10. ReAct baseline must not fail solely because it lacks LangGraph-only nodes such as `planner`, `worker`, or `reviewer`.
