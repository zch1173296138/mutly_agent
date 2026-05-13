## 1. Variant Contract

- [x] 1.1 Add a `langgraph_react_worker` variant constant and include it where optional ablation variants are enumerated.
- [x] 1.2 Keep the existing two-variant A/B contract valid when ablation is not requested.
- [x] 1.3 Define pairwise comparison identifiers for `langgraph_state_machine`, `langgraph_react_worker`, and `linear_react_baseline`.

## 2. ReAct Worker Injection

- [x] 2.1 Implement an evaluation-only ReAct worker function that consumes planner task state and uses the existing model/tool adapter surface.
- [x] 2.2 Add a LangGraph ablation runner that compiles the graph with only the worker node replaced.
- [x] 2.3 Ensure controller, planner, reviewer, state schema, loop budget, stop reasons, and adapter inputs remain equivalent to the normal LangGraph runner.
- [x] 2.4 Ensure production chat graph construction still uses the existing `worker_node` by default.

## 3. Trace And Scoring

- [x] 3.1 Capture controller/planner/ReAct-worker/reviewer trace events with state fingerprints and bounded state diffs.
- [x] 3.2 Capture ReAct worker tool calls with normalized arguments, argument hashes, output summaries or hashes, and errors.
- [x] 3.3 Score `langgraph_react_worker` using only observed trace data and existing `loop_rules`.
- [x] 3.4 Preserve controlled stop, loop detected, max steps, tool failure, human input, completed, and runtime error taxonomy for the ablation runner.

## 4. Report And CLI

- [x] 4.1 Extend report aggregation to include optional third-variant metrics without requiring ablation in every report.
- [x] 4.2 Add pairwise pass-rate and loop-rate deltas for all three variant pairs when ablation is present.
- [x] 4.3 Extend the CLI to include or exclude `langgraph_react_worker` explicitly.
- [x] 4.4 Preserve `source` and `evidentiary` behavior so simulated ablation reports are not proof.

## 5. Tests

- [x] 5.1 Add deterministic tests that `langgraph_react_worker` executes a case and emits graph plus ReAct worker trace events.
- [x] 5.2 Add tests proving only the evaluation graph uses the injected ReAct worker and production graph wiring remains unchanged.
- [x] 5.3 Add scorer tests for repeated tool loops and no-progress loops inside the ReAct worker ablation.
- [x] 5.4 Add report tests for optional third variant metrics and pairwise deltas.
- [x] 5.5 Add CLI tests or smoke checks for running with and without the ablation variant.
- [x] 5.6 Run focused pytest for ablation runner, scorer, report, CLI behavior, and existing A/B regression tests.
