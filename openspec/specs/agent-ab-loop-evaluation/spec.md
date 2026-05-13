# agent-ab-loop-evaluation Specification

## Purpose
TBD - created by archiving change add-agent-ab-loop-evaluation. Update Purpose after archive.
## Requirements
### Requirement: A/B variants execute the same case
The system SHALL execute every selected evaluation case through both `langgraph_state_machine` and `linear_react_baseline` before producing an A/B conclusion.

#### Scenario: Paired execution
- **WHEN** the A/B runner evaluates a dataset case
- **THEN** it produces one run result for `langgraph_state_machine` and one run result for `linear_react_baseline`

#### Scenario: Same input contract
- **WHEN** both variants run a case
- **THEN** both receive the same user query, available source metadata, tool adapter configuration, model adapter configuration, and loop rule thresholds

### Requirement: Trace events are captured from actual execution
Each variant run MUST emit ordered trace events from the actual runner rather than inferred or expectation-only events.

#### Scenario: Trace event contains execution data
- **WHEN** a variant advances one step
- **THEN** the trace event records the step index, variant, action or node name, state fingerprint before and after the step, and a bounded state diff summary

#### Scenario: Tool call is traced
- **WHEN** a variant calls a tool
- **THEN** the trace event records the tool name, normalized arguments, argument hash, output summary or output hash, and error status if present

#### Scenario: Stop reason is traced
- **WHEN** a variant stops
- **THEN** the run result records one stop reason from `completed`, `controlled_stop`, `loop_detected`, `max_steps_exceeded`, `tool_failure`, `human_input_required`, or `runtime_error`

### Requirement: Loop scoring uses shared loop_rules
The system SHALL score both variants with the same `loop_rules` declared by the dataset case.

#### Scenario: Total step threshold is exceeded
- **WHEN** a run records more trace steps than `loop_rules.max_total_steps`
- **THEN** the scorer marks the run as loop-triggered or max-step-aborted

#### Scenario: Same tool call threshold is exceeded
- **WHEN** a run calls the same tool with equivalent normalized arguments more than `loop_rules.max_same_tool_calls`
- **THEN** the scorer marks the run as loop-triggered

#### Scenario: No state change threshold is exceeded
- **WHEN** a run records more consecutive steps without a state fingerprint change than `loop_rules.max_no_state_change_steps`
- **THEN** the scorer marks the run as loop-triggered

### Requirement: ReAct is not penalized for missing LangGraph-only nodes
The evaluator MUST NOT mark the ReAct baseline as failed solely because its trace lacks planner, worker, reviewer, or other LangGraph-specific node names.

#### Scenario: ReAct completes without LangGraph nodes
- **WHEN** the ReAct baseline completes the task, respects loop rules, and meets the case outcome criteria
- **THEN** the evaluator can mark the ReAct run as passed even if its trace contains no LangGraph node names

### Requirement: A/B report separates observed evidence from expectations
The A/B report SHALL distinguish observed trace metrics from dataset expectations or fixture predictions.

#### Scenario: Report includes observed metrics
- **WHEN** an A/B report is generated
- **THEN** it includes observed pass rate, loop rate, max-step abort rate, repeated-tool-call rate, stop-reason counts, failed case IDs, and loop-triggered case IDs for each variant

#### Scenario: Simulated traces are not used as proof
- **WHEN** a report is generated from fixture or simulated traces
- **THEN** the report marks the run source as non-evidentiary for the claim that LangGraph solved ReAct loops

### Requirement: Deterministic tests cover runner and scorer mechanics
The implementation MUST include deterministic tests for trace capture, loop scoring, and paired reporting without requiring external LLM or network access.

#### Scenario: CI uses local adapters
- **WHEN** pytest runs the A/B evaluation tests
- **THEN** tests use local deterministic model and tool adapters while exercising the same runner and scorer interfaces used by live runs

