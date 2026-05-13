# agent-eval-dataset Specification

## Purpose
TBD - created by archiving change add-agent-eval-dataset. Update Purpose after archive.
## Requirements
### Requirement: Dataset files are versionable and machine-readable
The system SHALL provide an Agent evaluation dataset as versionable files under `datasets/agent_eval/`, including JSONL samples, a JSON Schema, and human-readable documentation.

#### Scenario: Dataset files exist
- **WHEN** a developer inspects `datasets/agent_eval/`
- **THEN** the directory contains `eval_cases.jsonl`, `schema.json`, and `README.md`

### Requirement: Each evaluation case has outcome and process constraints
Each dataset sample MUST define the user task, gold answer, evidence, expected behavior, expected graph nodes, expected tools, maximum step budget, loop detection rules, and scoring weights.

#### Scenario: Evaluation case is structurally valid
- **WHEN** a test parses a JSONL row from the dataset
- **THEN** required fields are present and have valid types

### Requirement: Dataset evidence is grounded in real local sources
Every non-empty source referenced by an evaluation case MUST point to a real local file, and every evidence quote MUST be present in the referenced source file.

#### Scenario: Evidence quote is traceable
- **WHEN** tests read an evaluation case evidence item
- **THEN** the referenced source file exists and contains the evidence quote

### Requirement: Financial answers are recomputable
Financial report QA samples MUST include calculation metadata so numeric gold answers can be recomputed from source values.

#### Scenario: Financial calculation matches gold answer
- **WHEN** tests recompute a financial sample's calculation
- **THEN** the computed result matches the stored calculation result

### Requirement: Dataset covers core multi-agent research workflows
The dataset MUST cover Controller routing, Planner decomposition, RAG retrieval, PDF parsing, financial report QA, end-to-end report synthesis, missing tool handling, HITL handling, and loop stability pressure cases.

#### Scenario: Category coverage is complete
- **WHEN** tests collect all sample categories
- **THEN** every required category is represented by at least one sample

### Requirement: Loop pressure cases are explicitly bounded
Loop stability samples MUST define a maximum total step count, maximum repeated same-tool calls, and maximum no-state-change steps.

#### Scenario: Loop sample has stop conditions
- **WHEN** a test reads a sample with category `loop_stability`
- **THEN** the sample contains `loop_rules.max_total_steps`, `loop_rules.max_same_tool_calls`, and `loop_rules.max_no_state_change_steps`

### Requirement: Scoring weights are normalized
Every sample MUST define scoring weights that sum to 1.0 so evaluation metrics are comparable across cases.

#### Scenario: Scoring is normalized
- **WHEN** tests sum the values in a sample's `scoring` object
- **THEN** the total equals 1.0 within a small floating-point tolerance

