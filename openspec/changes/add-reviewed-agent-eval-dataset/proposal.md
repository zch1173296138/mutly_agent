## Why

The existing agent evaluation dataset can validate structure, evidence quotes, and loop scoring mechanics, but it does not yet encode a formal human review gate for `gold_answer` correctness. This change makes the dataset suitable for defensible A/B claims by requiring reviewed labels, evidence mapping, and a clear split between draft samples and approved golden samples.

## What Changes

- Add a reviewed-label workflow for agent evaluation cases.
- Require each formal evaluation case to record label review status, reviewer metadata, confidence, and review notes.
- Require `gold_answer` claims to be traceable to evidence, a recomputable calculation, or an explicit reviewed inference.
- Separate exploratory samples from approved golden samples so official A/B reports can filter to reviewed data only.
- Document how open-source datasets may be adapted into the local schema without bypassing review.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `agent-eval-dataset`: Adds reviewed-label and golden-set requirements for using dataset cases as formal evaluation evidence.

## Impact

- Affects files under `datasets/agent_eval/`, especially dataset schema, documentation, JSONL cases, and optional review logs.
- Affects dataset validation tests that currently check structure, evidence, and calculations.
- May affect A/B evaluation scripts if they need to filter by reviewed status or golden-set membership before reporting formal results.
