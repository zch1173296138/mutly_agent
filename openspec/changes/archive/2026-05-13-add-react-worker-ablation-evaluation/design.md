## Context

The current `agent-ab-loop-evaluation` change establishes real execution and trace-derived scoring for `langgraph_state_machine` versus `linear_react_baseline`. That experiment is necessary but broad: it changes the whole execution architecture at once.

The new question is narrower: if the LangGraph outer workflow remains the same, what happens when only the worker execution strategy is changed to a traditional ReAct loop? This requires an ablation variant that keeps controller, planner, reviewer, state schema, adapters, loop rules, and trace reporting stable while swapping the worker implementation only inside the evaluation harness.

## Goals / Non-Goals

**Goals:**

- Add an evaluation-only `langgraph_react_worker` variant.
- Execute the existing compiled LangGraph workflow with an injected ReAct worker node.
- Preserve the same dataset case input, model adapter, tool adapter, loop budget, stop reason taxonomy, trace schema, and scorer used by the existing A/B runner.
- Report enough pairwise metrics to distinguish:
  - end-to-end architecture effects,
  - worker strategy effects,
  - interaction between outer orchestration and worker loop behavior.
- Keep production chat behavior unchanged.

**Non-Goals:**

- Do not replace `app.graph.nodes.worker.worker_node` globally in production.
- Do not claim LangGraph solved loops solely because `langgraph_react_worker` differs from `linear_react_baseline`.
- Do not introduce online traffic splitting.
- Do not add a new scoring rubric separate from existing `loop_rules`.

## Decisions

- Implement the ablation as a third runner variant named `langgraph_react_worker`.
  - Rationale: it can be compared against both existing variants without changing their meaning.
  - Rejected alternative: replace the existing LangGraph runner's worker permanently, because that would destroy the baseline needed to understand current behavior.

- Inject the alternate worker at evaluation runtime.
  - Rationale: the production graph should remain untouched, while the evaluation graph can compile with a worker function selected by the runner.
  - Rejected alternative: monkeypatch production modules globally for all tests, because it risks leaking behavior across tests and production code paths.

- Reuse the existing ReAct loop semantics where possible.
  - Rationale: the ablation should answer whether a ReAct-style worker behaves differently inside LangGraph, not define a third unrelated worker algorithm.
  - Rejected alternative: create a new bespoke worker loop, because it would add another confounder.

- Preserve trace compatibility.
  - Rationale: the scorer and report should consume `RunResult` uniformly regardless of variant.
  - Required trace fields remain node/action name, state fingerprints, state diff, tool calls, normalized arguments, output hashes, stop reason, errors, and timing.

- Report pairwise deltas.
  - Rationale: one aggregate delta between LangGraph and ReAct is insufficient once the ablation variant exists.
  - Required pairs:
    - `langgraph_state_machine` vs `linear_react_baseline`
    - `langgraph_state_machine` vs `langgraph_react_worker`
    - `langgraph_react_worker` vs `linear_react_baseline`

## Risks / Trade-offs

- Alternate worker injection may diverge from production graph wiring -> Keep the graph builder path explicit and test that controller/planner/reviewer still execute around the injected worker.
- ReAct worker inside LangGraph may receive planner-produced task state rather than the original user prompt -> Treat this as intended for the ablation and record the exact state/task input in trace events.
- Pairwise reporting can be misread as proof from deterministic fixtures -> Preserve `source` and `evidentiary` semantics; `simulated_fixture` remains non-evidentiary.
- The third variant increases test runtime -> Use deterministic adapters in CI and keep live integration runs explicit.

## Migration Plan

1. Add the new variant constant and runner class.
2. Add a graph construction path or runtime injection mechanism that replaces only the worker node for evaluation.
3. Reuse existing deterministic adapters and scorer.
4. Extend report aggregation to support three variants and pairwise deltas.
5. Update CLI selection/output to include the ablation variant.
6. Add focused tests for trace capture, non-production isolation, pairwise reporting, and loop scoring.
7. Leave production chat graph unchanged.
