## MODIFIED Requirements

### Requirement: A/B variants execute the same case
The system SHALL execute every selected evaluation case through both `langgraph_state_machine` and `linear_react_baseline` before producing an A/B conclusion. When worker ablation is enabled, the system SHALL also execute `langgraph_react_worker` for the same selected case before producing worker-ablation conclusions.

#### Scenario: Paired execution
- **WHEN** the A/B runner evaluates a dataset case
- **THEN** it produces one run result for `langgraph_state_machine` and one run result for `linear_react_baseline`

#### Scenario: Same input contract
- **WHEN** both variants run a case
- **THEN** both receive the same user query, available source metadata, tool adapter configuration, model adapter configuration, and loop rule thresholds

#### Scenario: Worker ablation execution
- **WHEN** worker ablation is requested for a dataset case
- **THEN** the runner also produces one run result for `langgraph_react_worker` using the same input contract

### Requirement: A/B report separates observed evidence from expectations
The A/B report SHALL distinguish observed trace metrics from dataset expectations or fixture predictions. When worker ablation is enabled, the report SHALL include the ablation variant as observed execution evidence under the same source and evidentiary rules.

#### Scenario: Report includes observed metrics
- **WHEN** an A/B report is generated
- **THEN** it includes observed pass rate, loop rate, max-step abort rate, repeated-tool-call rate, stop-reason counts, failed case IDs, and loop-triggered case IDs for each variant

#### Scenario: Simulated traces are not used as proof
- **WHEN** a report is generated from fixture or simulated traces
- **THEN** the report marks the run source as non-evidentiary for the claim that LangGraph solved ReAct loops

#### Scenario: Report includes worker ablation metrics
- **WHEN** a report includes `langgraph_react_worker`
- **THEN** it includes the same observed metrics for that variant as for `langgraph_state_machine` and `linear_react_baseline`

#### Scenario: Report includes pairwise deltas
- **WHEN** a report includes all three variants
- **THEN** it includes pairwise deltas for pass rate and loop rate between `langgraph_state_machine`, `langgraph_react_worker`, and `linear_react_baseline`
