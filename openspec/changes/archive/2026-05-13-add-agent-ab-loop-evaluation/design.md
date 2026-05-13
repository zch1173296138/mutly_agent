## Context

The repository already has a LangGraph workflow and an agent evaluation dataset with `loop_rules`. The previous offline comparison was insufficient because it used dataset expectations or architecture assumptions to infer A/B outcomes. A valid experiment needs both variants to execute the same case and emit comparable traces.

The core fairness rule is: LangGraph can win only by producing better observed behavior under the same task and loop rules, not by receiving credit for owning planner/reviewer nodes that ReAct does not have.

## Goals / Non-Goals

**Goals:**

- Execute each selected dataset case through both variants:
  - `langgraph_state_machine`: the current compiled LangGraph workflow.
  - `linear_react_baseline`: a single-loop ReAct executor using the same model/tool adapter surface where possible.
- Capture trace events from actual execution.
- Record enough state information to detect no-progress loops:
  - step count,
  - node/action name,
  - tool name and normalized arguments,
  - tool output summary or hash,
  - state fingerprint before/after,
  - state diff summary,
  - final stop reason.
- Score both variants with identical `loop_rules`.
- Produce a paired report that distinguishes:
  - task success,
  - loop-triggered failure,
  - controlled stop,
  - max-step abort,
  - tool failure,
  - human-input required.

**Non-Goals:**

- Do not treat static `ab_test.expected_*` values as proof.
- Do not score ReAct down for not having LangGraph-only nodes.
- Do not require online traffic splitting in this change.
- Do not change the production chat endpoint unless the runner explicitly reuses existing graph construction.

## Decisions

- Use trace-derived scoring as the only basis for A/B conclusions.
  - Rationale: this directly addresses the loop question.
  - Rejected alternative: category-based or node-presence scoring, because it bakes the desired conclusion into the evaluator.

- Keep the dataset `loop_rules` as the shared stopping and scoring contract.
  - Rationale: both variants are judged against the same thresholds.
  - Rejected alternative: custom thresholds per variant, because that weakens comparability.

- Build runners behind a common `AgentVariantRunner` interface.
  - Rationale: the scorer should not know whether a trace came from LangGraph or ReAct.
  - The runner output is a `RunTrace` with ordered `TraceEvent` entries and a final result envelope.

- Capture state fingerprints and diffs at each step.
  - Rationale: repeated tool calls alone miss loops where the agent paraphrases the same reasoning without progress.
  - Diff summaries should be structured and bounded so reports remain reviewable.

- Provide deterministic CI execution through local/mock model and tool adapters.
  - Rationale: tests should verify the runner/scorer mechanics without depending on paid models or remote services.
  - Real model/tool execution can be enabled separately with explicit configuration.

## Risks / Trade-offs

- Real LLM execution can be flaky -> CI should use deterministic adapters; live runs should be marked as integration experiments.
- ReAct baseline may be under-specified -> Define its loop, prompt, tool interface, max step policy, and stop reasons before comparing.
- State diffs may leak large or sensitive content -> Store fingerprints and short summaries by default; raw payload capture must be opt-in.
- Existing LangGraph streaming path may not expose all state transitions -> Add runner-level instrumentation rather than relying only on UI SSE events.
- Tool normalization can hide meaningful argument changes -> Normalize only stable fields and keep raw argument hashes for audit.

## Migration Plan

1. Replace expectation-only A/B tasks with real runner tasks.
2. Implement trace models and shared scorer first.
3. Add LangGraph runner instrumentation.
4. Add ReAct baseline runner with the same tool/model adapters.
5. Run paired deterministic tests on a subset of loop and non-loop cases.
6. Add optional live/integration command for real model/tool traces.
