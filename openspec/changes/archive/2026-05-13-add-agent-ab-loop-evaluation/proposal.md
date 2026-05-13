## Why

The evaluation must prove whether LangGraph reduces ReAct-style dead loops by executing both variants and scoring their real traces. Comparing LangGraph against ReAct by assuming planner/reviewer nodes are inherently better is not a valid A/B test.

## What Changes

- Add a real paired A/B evaluation runner where each dataset case is executed by both `langgraph_state_machine` and `linear_react_baseline`.
- Capture trace events for both variants, including node/action name, tool calls, state diff or fingerprint, step count, stop reason, errors, and final output.
- Score both variants with the same `loop_rules` from the dataset: max total steps, repeated same-tool calls, and no-state-change steps.
- Compare variants on task success, loop rate, stop reason, evidence quality, tool behavior, cost/latency where available, and final answer quality.
- Ensure the ReAct baseline is not marked worse merely because it lacks LangGraph-only nodes such as planner or reviewer.
- Treat simulated or expectation-only traces as test fixtures only; they MUST NOT be used as evidence that LangGraph solved the loop problem.

## Capabilities

### New Capabilities
- `agent-ab-loop-evaluation`: Defines real paired A/B execution, trace capture, and loop-rule scoring for LangGraph versus a linear ReAct baseline.

### Modified Capabilities
- None.

## Impact

- Adds evaluation runner code under `app/evaluation/` or `scripts/`.
- Adds trace event models and report generation for A/B runs.
- Updates dataset documentation and tests so samples support real execution, not only static expectations.
- May add mock/local tool adapters for deterministic CI, while keeping the same trace schema as real runs.
- Does not change production chat behavior unless explicitly wired in a later change.
