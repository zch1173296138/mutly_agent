## Why

The current A/B evaluation compares the full LangGraph workflow against a linear ReAct baseline, which proves end-to-end behavior but does not isolate where loop-resistance comes from. We need a worker-level ablation to test whether the improvement comes from the LangGraph outer state machine, from the worker execution strategy, or from their interaction.

## What Changes

- Add a third evaluation-only variant: `langgraph_react_worker`.
- Run the same dataset case through the existing LangGraph shell while replacing only the worker node with a traditional ReAct-style worker loop.
- Keep controller, planner, reviewer, state schema, tool adapter, model adapter, loop rules, trace schema, and scoring logic comparable to the existing A/B runner.
- Extend reports so they can compare:
  - full LangGraph versus linear ReAct,
  - full LangGraph versus LangGraph with ReAct worker,
  - LangGraph with ReAct worker versus linear ReAct.
- Ensure this is an evaluation harness change only; it MUST NOT replace the production worker by default.

## Capabilities

### New Capabilities
- `react-worker-ablation-evaluation`: Defines the worker-level ablation variant and reporting requirements for isolating worker strategy from outer LangGraph orchestration.

### Modified Capabilities
- `agent-ab-loop-evaluation`: The existing paired evaluation must support an additional variant in reports without weakening the current two-variant contract.

## Impact

- Updates evaluation runner code under `app/evaluation/`.
- Adds an evaluation-only graph runner that can inject an alternate worker implementation.
- Updates CLI/report output to include the new variant and pairwise deltas.
- Adds deterministic tests for the ablation runner and report aggregation.
- Does not change production chat routing or the default production `worker_node`.
