## ADDED Requirements

### Requirement: Evaluation includes a LangGraph ReAct worker ablation variant
The system SHALL provide an evaluation-only variant named `langgraph_react_worker` that runs the LangGraph outer workflow while replacing only the worker execution strategy with a traditional ReAct-style worker loop.

#### Scenario: Ablation variant executes a case
- **WHEN** the evaluation runner executes a selected dataset case with `langgraph_react_worker`
- **THEN** the runner invokes controller, planner, and reviewer behavior through the LangGraph workflow
- **THEN** the worker step uses the ReAct worker strategy instead of the production worker implementation

#### Scenario: Production worker remains unchanged
- **WHEN** the ablation variant is configured for evaluation
- **THEN** the production `worker_node` remains the default worker for normal chat execution

### Requirement: Worker ablation isolates worker strategy
The ablation runner MUST keep all non-worker evaluation inputs and policies equivalent to the normal LangGraph variant.

#### Scenario: Same outer workflow contract
- **WHEN** `langgraph_state_machine` and `langgraph_react_worker` run the same case
- **THEN** both receive the same user query, available source metadata, model adapter configuration, tool adapter configuration, state schema, and loop rule thresholds

#### Scenario: Only worker strategy differs
- **WHEN** comparing `langgraph_state_machine` to `langgraph_react_worker`
- **THEN** evaluator conclusions identify the comparison as a worker-strategy ablation rather than a full architecture comparison

### Requirement: ReAct worker ablation emits compatible traces
The `langgraph_react_worker` runner MUST emit the same trace event schema as the existing A/B variants.

#### Scenario: Graph node trace is captured
- **WHEN** the ablation runner advances through controller, planner, worker, or reviewer
- **THEN** each step records the variant, step index, node or action name, state fingerprints before and after the step, and bounded state diff summary

#### Scenario: ReAct worker tool trace is captured
- **WHEN** the injected ReAct worker calls a tool
- **THEN** the trace records the tool name, normalized arguments, argument hash, output summary or output hash, and error status if present

#### Scenario: Stop reason is comparable
- **WHEN** the ablation runner stops
- **THEN** the run result records one stop reason from the shared stop reason taxonomy used by the existing A/B evaluator

### Requirement: Worker ablation is scored with shared loop rules
The system SHALL score `langgraph_react_worker` using only observed trace data and the same dataset `loop_rules` used for the existing variants.

#### Scenario: Repeated tool call loop
- **WHEN** the ReAct worker inside LangGraph calls the same tool with equivalent normalized arguments more than `loop_rules.max_same_tool_calls`
- **THEN** the scorer marks the ablation run as loop-triggered

#### Scenario: No-progress worker loop
- **WHEN** the ReAct worker inside LangGraph records more consecutive no-state-change steps than `loop_rules.max_no_state_change_steps`
- **THEN** the scorer marks the ablation run as loop-triggered

#### Scenario: Controlled stop is separated from loop abort
- **WHEN** the LangGraph shell stops the ablation run in a bounded controlled state
- **THEN** the report records `controlled_stop` separately from `loop_detected` and `max_steps_exceeded`
