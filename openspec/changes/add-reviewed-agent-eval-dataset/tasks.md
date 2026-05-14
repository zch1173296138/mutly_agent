## 1. Dataset Contract

- [x] 1.1 Extend `datasets/agent_eval/schema.json` with optional review metadata for development cases and required metadata for formal golden cases.
- [x] 1.2 Add an answer-claim support structure that maps material `gold_answer` fields to direct evidence, calculation metadata, or reviewed inference.
- [x] 1.3 Document the label lifecycle in `datasets/agent_eval/README.md`: `draft`, `needs_review`, `approved`, and `rejected`.
- [x] 1.4 Document the rule that formal A/B conclusions can only use approved cases with non-low confidence.

## 2. Dataset Migration

- [x] 2.1 Add review metadata to existing dataset cases, defaulting unreviewed rows to `needs_review`.
- [x] 2.2 Add claim support mappings for existing loop stability cases first.
- [x] 2.3 Manually review and mark the existing `loop_stability` cases as approved only after confirming `gold_answer`, `gold_behavior`, evidence, and loop rules.
- [x] 2.4 Decide whether to keep all cases in `eval_cases.jsonl` or add a separate reviewed subset manifest for formal reports.

## 3. Validation Tests

- [x] 3.1 Update dataset tests to validate `label_review` shape and allowed status/confidence values.
- [x] 3.2 Add tests that formal golden cases have approved review metadata and non-empty review notes.
- [x] 3.3 Add tests that each material `gold_answer` claim has direct evidence, calculation support, or reviewed inference support.
- [x] 3.4 Keep existing source existence, quote traceability, scoring normalization, and financial recomputation tests passing.

## 4. Evaluation Workflow

- [x] 4.1 Add a reviewed-case filter or report flag to the A/B evaluation workflow.
- [x] 4.2 Ensure reports generated from unreviewed or low-confidence cases are marked non-evidentiary for formal claims.
- [x] 4.3 Add a deterministic test proving approved cases can produce evidentiary reports while draft cases do not.
- [x] 4.4 Update command examples for running development cases versus reviewed golden cases.

## 5. Open-Source Dataset Adaptation

- [x] 5.1 Add documentation for converting external benchmark samples into the local schema.
- [x] 5.2 Require adapted external samples to use frozen local sources and local evidence quotes before review.
- [x] 5.3 Add at least one draft example or template showing how an external benchmark item becomes a reviewed local case.
