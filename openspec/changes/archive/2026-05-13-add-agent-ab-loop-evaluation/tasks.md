## 1. Experiment Contract

- [x] 1.1 Define `TraceEvent`, `RunTrace`, `RunResult`, and `ABReport` data structures with fields for variant, step index, node/action, tool call, state fingerprints, state diff summary, stop reason, errors, timing, and token/cost metadata when available.
- [x] 1.2 Define a common `AgentVariantRunner` interface that accepts one dataset case and returns a real `RunResult`.
- [x] 1.3 Define stop reason constants: `completed`, `controlled_stop`, `loop_detected`, `max_steps_exceeded`, `tool_failure`, `human_input_required`, and `runtime_error`.
- [x] 1.4 Remove or demote expectation-only A/B pass/fail logic so `ab_test.expected_*` can document hypotheses but cannot be used as proof.

## 2. Shared Scoring

- [x] 2.1 Implement a loop scorer that uses only actual trace data and the case `loop_rules`.
- [x] 2.2 Score max total steps, repeated same-tool calls with normalized arguments, and consecutive no-state-change steps.
- [x] 2.3 Add task outcome scoring that does not require LangGraph-specific node names for ReAct.
- [x] 2.4 Add report aggregation for observed pass rate, loop rate, max-step abort rate, repeated-tool-call rate, stop-reason counts, failed IDs, and loop IDs.

## 3. LangGraph Runner

- [x] 3.1 Build a LangGraph variant runner that invokes the compiled graph for one dataset case.
- [x] 3.2 Instrument each graph step to capture node name, state fingerprint before/after, bounded state diff summary, and final stop reason.
- [x] 3.3 Capture tool calls from `tool_history` or runner-level tool adapters with normalized arguments and output hashes.
- [x] 3.4 Ensure LangGraph runner respects the case `max_steps` / `loop_rules.max_total_steps` budget and reports controlled stops separately from loop-triggered aborts.

## 4. Linear ReAct Runner

- [x] 4.1 Define the linear ReAct baseline prompt and execution loop explicitly.
- [x] 4.2 Reuse the same model adapter and tool adapter surface as the LangGraph runner where practical.
- [x] 4.3 Capture every ReAct thought/action/tool/observation/final step as trace events.
- [x] 4.4 Enforce the same step budget and stop reason taxonomy as LangGraph.
- [x] 4.5 Verify ReAct can pass non-loop cases without requiring planner/reviewer node names.

## 5. Deterministic Test Adapters

- [x] 5.1 Add local deterministic model adapters for success, missing-source, repeated-tool-loop, and tool-failure scenarios.
- [x] 5.2 Add local deterministic tool adapters that return stable outputs from `available_sources`.
- [x] 5.3 Use deterministic adapters in CI so tests run without external LLM, network, MinerU, or MCP dependencies.

## 6. Dataset Alignment

- [x] 6.1 Review `datasets/agent_eval/eval_cases.jsonl` and remove fields that force A/B conclusions instead of describing hypotheses.
- [x] 6.2 Ensure each loop-pressure case has enough local source metadata for both runners to execute the same task.
- [x] 6.3 Update `datasets/agent_eval/README.md` to explain observed trace scoring versus hypothesis fields.
- [x] 6.4 Update `schema.json` so real-run metadata and optional hypothesis fields are represented clearly.

## 7. Tests

- [x] 7.1 Add tests that fail if A/B reports are generated without executing both variants.
- [x] 7.2 Add scorer tests for total step loops, repeated tool loops, no-state-change loops, controlled stops, and runtime errors.
- [x] 7.3 Add LangGraph runner tests using deterministic adapters.
- [x] 7.4 Add ReAct runner tests using deterministic adapters.
- [x] 7.5 Add paired report tests proving ReAct is not penalized for missing LangGraph-specific nodes.
- [x] 7.6 Run focused pytest for dataset, runner, scorer, and report tests.

## 8. CLI / Report Output

- [x] 8.1 Add a command or script to run the paired A/B evaluation over selected dataset cases.
- [x] 8.2 Write JSON report output with per-case traces, per-variant metrics, and aggregate deltas.
- [x] 8.3 Mark report source as `deterministic_adapter`, `live_integration`, or `simulated_fixture`.
- [x] 8.4 Prevent simulated fixture reports from being summarized as evidence that LangGraph solved ReAct loops.
