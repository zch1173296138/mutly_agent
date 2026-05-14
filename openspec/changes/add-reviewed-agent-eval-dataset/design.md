## Context

The repository already contains an agent evaluation dataset under `datasets/agent_eval/`, with JSONL cases, schema validation, grounded evidence quotes, recomputable financial calculations, and deterministic A/B loop evaluation tests. Those checks prevent many structural and mechanical errors, but they do not establish that every `gold_answer` is semantically correct or human-approved for formal evaluation claims.

This change adds a review layer on top of the existing dataset contract. Programmatic checks continue to validate sources, quotes, calculations, scoring, and loop rules. Human review becomes the final gate for deciding whether a case can be used in official golden-set A/B reporting.

## Goals / Non-Goals

**Goals:**

- Make `gold_answer` correctness auditable at the field or claim level.
- Add a review status model that separates draft cases from approved golden cases.
- Require formal reports to use only approved, non-low-confidence cases.
- Preserve the current deterministic runner and scoring mechanics.
- Keep open-source dataset reuse possible by converting external samples into the local reviewed schema.

**Non-Goals:**

- This change does not create a training dataset.
- This change does not prove live LLM behavior by itself; live evidence still requires real runner traces.
- This change does not require a new labeling application or database.
- This change does not replace human review with an LLM judge.

## Decisions

### Store review metadata with each formal case

Each case that can enter the golden set will include a `label_review` object with status, reviewer, confidence, review date, and notes.

Alternatives considered:

- Separate-only review log: easier to keep labels clean, but harder to filter cases without joining files.
- Inline-only review metadata: simple for validation and filtering, but can make JSONL rows longer.

Decision: use inline `label_review` as the authoritative gate, with an optional review log for audit history if needed.

### Treat `gold_answer` as claims, not just a blob

Formal cases should make each important answer field traceable to one of three support types: direct evidence quote, recomputable calculation, or reviewed inference. This avoids treating an evidence quote as proof for unrelated fields.

Alternatives considered:

- Keep current free-form `gold_answer`: minimal changes, but weak auditability.
- Require every value to be directly extractable by script: strong automation, but too restrictive for summary and negative-case labels.

Decision: require explicit answer evidence mapping for formal cases, while still allowing reviewed inference when a program cannot fully validate semantics.

### Use approved cases only for formal A/B claims

The runner can still execute any selected case for development, but reports that claim LangGraph solves ReAct loops must identify whether the input set is reviewed. A formal report should include only cases with `label_review.status == "approved"` and `label_review.confidence != "low"`.

Alternatives considered:

- Block all non-approved cases in the runner: safer, but less useful for local development and debugging.
- Allow any case and rely on documentation: flexible, but easy to misuse.

Decision: validation and reporting should make review status explicit, while allowing developers to run drafts intentionally.

## Risks / Trade-offs

- Review metadata can become stale after a source or gold answer edit -> validation should require review fields to be present and reviewers should re-approve changed rows before formal use.
- Claim mapping adds annotation overhead -> keep the schema minimal and allow reviewed inference for cases that cannot be fully machine-checked.
- Inline reviewer names may be sensitive in public repositories -> use team aliases or initials if needed.
- Approved status can create false confidence -> reports should still distinguish deterministic adapter results from live runner evidence.

## Migration Plan

1. Extend the dataset schema to accept and validate `label_review` and answer evidence mapping fields.
2. Add review metadata to existing cases, initially marking them as `needs_review` unless they have been manually approved.
3. Review and approve the existing loop stability cases first, because they are central to LangGraph/ReAct claims.
4. Update tests to verify that formal golden cases have approved review metadata and claim support.
5. Update A/B reporting or documentation so formal conclusions use the reviewed subset.

Rollback is straightforward: keep the existing fields optional for development runs and remove golden-set filtering if the review process needs redesign.

## Open Questions

- Should reviewer identity be a free-form string, a team alias, or a fixed enum?
- Should approved cases stay in `eval_cases.jsonl` with metadata, or should an explicit `golden_cases.jsonl` be introduced later?
- Should formal reports fail when any selected case is unapproved, or only mark the report as non-evidentiary?
