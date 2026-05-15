# multy_agent Agent Evaluation Dataset

本目录维护用于评测 LangGraph、DAG 与 rerank 分支是否减少传统 ReAct 工具调用死循环的数据集工程。

## 目录结构

- `datasets/seed/eval_cases.jsonl`：从 `datasets/agent_eval/eval_cases.jsonl` 复制的 25 条 seed case。
- `datasets/generated/multy_agent_loop_eval.jsonl`：统一 schema 后的生成数据集，包含 seed、合成 loop/adversarial case 和 open-source adapter 预留位。
- `datasets/formal/formal_loop_minimal.jsonl`：最小正式集，只包含已审核、active、非占位、license 可用的样本。
- `datasets/dev/dev_loop_eval.jsonl`：开发集，包含 seed、synthetic 和真实导入 open-source 样本，不包含 adapter slot。
- `datasets/staging/open_source_agent_eval.jsonl`：开源样本审核区，包含 license 或标签仍需确认的真实导入样本。
- `datasets/templates/open_source_slots.jsonl`：模板集，只包含 open-source adapter placeholder slot。
- `adapters/`：GAIA、AgentBench、ToolBench、tau-bench 的 dry-run adapter。
- `evaluators/`：确定性 evaluator，不依赖真实 LLM 或外部 API。
- `scripts/`：数据集生成、校验、本地评测与 LangSmith 同步脚本。
- `registry.yaml`：数据集、adapter 和 evaluator 注册表。

## 添加新 Case

新增 JSONL 行时至少保留这些字段：

```text
id, category, difficulty, user_query, gold_behavior,
expected_nodes, expected_tools, max_steps, loop_rules, scoring
```

如果 case 包含 `source` 字段，必须提供：

```text
kind, dataset, original_id, source_url, license, split, transformation
```

不要删除 seed case 的原始字段。需要扩展字段时直接追加新字段，例如 `source`、`synthetic_metadata` 或 `adapter_metadata`。

## 生成数据集

```bash
python evals/scripts/build_generated_dataset.py
```

该命令会：

1. 将 `datasets/agent_eval/eval_cases.jsonl` 复制到 `evals/datasets/seed/eval_cases.jsonl`。
2. 生成 `evals/datasets/generated/multy_agent_loop_eval.jsonl`。
3. 写入 25 条 seed case、20 条 `synthetic_from_seed` case、20 条 `open_source_dataset` adapter 预留位。

## 校验数据集

```bash
python evals/scripts/validate_dataset.py evals/datasets/generated/multy_agent_loop_eval.jsonl
```

校验会检查每行是否为合法 JSON、必填字段是否存在、`source` 字段是否完整、`id` 是否重复。

四层 split 需要带 `--split` 运行更严格校验：

```bash
python evals/scripts/validate_dataset.py evals/datasets/formal/formal_loop_minimal.jsonl --split formal
python evals/scripts/validate_dataset.py evals/datasets/dev/dev_loop_eval.jsonl --split dev
python evals/scripts/validate_dataset.py evals/datasets/staging/open_source_agent_eval.jsonl --split staging
python evals/scripts/validate_dataset.py evals/datasets/templates/open_source_slots.jsonl --split templates
```

## 本地 Eval

```bash
python evals/scripts/run_eval_local.py --dataset evals/datasets/generated/multy_agent_loop_eval.jsonl
```

输出包含每条 case 的 `pass/fail`，以及汇总指标：

- `termination_rate`
- `loop_rate`
- `avg_tool_calls`
- `duplicate_tool_call_ratio`
- `max_step_violation_rate`
- `stuck_running_count`

当前本地脚本使用确定性 trace fixture，只用于验证 evaluator 与数据契约。接入真实 LangGraph 或 ReAct runner 后，应保留相同输出指标。

## 真实 LangGraph Eval

正向 smoke test 默认使用 stable mock LLM，不依赖真实 API key：

```bash
python evals/scripts/run_eval_langgraph.py \
  --dataset evals/datasets/formal/formal_loop_minimal.jsonl \
  --mock-tools \
  --timeout-sec 120
```

负向 loop control 用于确认 loop evaluator 能抓到重复工具调用：

```bash
python evals/scripts/run_eval_langgraph.py \
  --dataset evals/datasets/formal/formal_loop_minimal.jsonl \
  --variant langgraph_state_machine \
  --mock-tools \
  --mock-llm-loop \
  --timeout-sec 120
```

真实 LLM + mock tools 用于只验证模型决策，不启动 MCP 工具：

```bash
python evals/scripts/run_eval_langgraph.py \
  --dataset evals/datasets/formal/formal_loop_minimal.jsonl \
  --variant langgraph_state_machine \
  --mock-tools \
  --real-llm \
  --timeout-sec 120
```

`run_eval_langgraph.py` 会读取 JSONL case，调用项目里的真实 `build_graph().compile()` LangGraph 入口，并把执行过程转换为与 `run_eval_local.py` 兼容的 report JSON。每条 case 会记录 node path、tool calls、tool arguments、task status、final output 和 wall time，并复用 `termination`、`no_loop`、`node_path`、`latency`、`tool_repetition` evaluator 生成 summary。

运行模式：

- `--variant`：选择 `langgraph_state_machine`、`linear_react_baseline` 或 `langgraph_react_worker`。
- `--mock-tools`：使用进程内确定性工具，不启动 MCP，也不依赖外部 API key。
- `--mock-llm-stable`：默认模式。使用确定性 LLM，最多调用一次 expected tools，然后返回 `gold_behavior`。
- `--mock-llm-loop`：负向控制模式。使用确定性 LLM 重复调用 expected tools，用于验证 loop evaluator。
- `--real-llm`：使用真实 LLM 配置；本机需要配置 `OPENAI_API_KEY` 和模型环境变量。
- `--timeout-sec`：限制单条 case 的最大执行时间；超时 case 会以 `running` 状态写入 report，计入 `stuck_running_count`。

`--mock-llm-stable`、`--mock-llm-loop`、`--real-llm` 三选一；不传时默认 `--mock-llm-stable`。只有同时使用真实 LLM 和真实工具时，report 的 `evidentiary` 才为 `true`；纯 mock report 会标为 `evidentiary: false`。

示例输出仍包含：

- `termination_rate`
- `loop_rate`
- `avg_tool_calls`
- `duplicate_tool_call_ratio`
- `max_step_violation_rate`
- `stuck_running_count`

## A/B Eval

运行三种 runner variant 并输出 variants summary 与 pairwise deltas：

```bash
python evals/scripts/run_eval_ab.py \
  --dataset evals/datasets/formal/formal_loop_minimal.jsonl \
  --mock-tools \
  --mock-llm-stable \
  --timeout-sec 120
```

该命令会自动执行：

- `langgraph_state_machine`
- `linear_react_baseline`
- `langgraph_react_worker`

输出顶层包含：

- `runner_mode = "ab"`
- `mock_tools`
- `llm_mode`
- `evidentiary`
- `variants`
- `pairwise_deltas`

可以把 `--mock-llm-stable` 换成 `--mock-llm-loop` 做负向控制，或换成 `--real-llm` 接入真实模型。`--real-llm --mock-tools` 只验证模型决策，不启动 MCP；完全真实证据运行需要去掉 `--mock-tools` 并配置 `mcp_servers.json`。

## 同步到 LangSmith

先 dry-run 检查转换结果：

```bash
python evals/scripts/sync_langsmith.py \
  --dataset evals/datasets/generated/multy_agent_loop_eval.jsonl \
  --dataset-name multy_agent_loop_eval \
  --dry-run
```

确认 `LANGSMITH_API_KEY` 等环境变量已在本机安全配置后，去掉 `--dry-run` 执行同步。脚本不会写入任何真实 API key。

每条 case 会转换为：

- `inputs = {"user_query": ...}`
- `reference_outputs = {"gold_behavior": ..., "gold_answer": ...}`
- `metadata = category, difficulty, source, loop_rules, expected_nodes, expected_tool_categories, expected_project_tools, expected_source_tools, expected_tools`

`expected_tools` 是兼容字段：旧数据集已有该字段时保留原值；新 split 数据集会由 `expected_project_tools + expected_source_tools` 拼接得到。

## Open-source Dataset License

Adapter 默认只生成 dry-run 预留位，不下载大型外部数据。所有 open-source adapter 输出的 license 默认写为：

```text
UNKNOWN_NEEDS_REVIEW
```

在导入 GAIA、AgentBench、ToolBench 或 tau-bench 的真实样本前，必须人工确认原始数据集 license、split、使用限制和引用要求。无法确认时不要改写 license，也不要将对应 case 用作正式证据。

## 导入 Open-source 小样本

GAIA、AgentBench 和 ToolBench adapter 支持从本地 JSON/JSONL 小样本导入，不会自动下载外部数据：

```bash
python evals/scripts/import_open_source_datasets.py \
  --gaia-input path/to/gaia_sample.jsonl \
  --gaia-license REVIEWED_LICENSE \
  --agentbench-input path/to/agentbench_sample.jsonl \
  --agentbench-license REVIEWED_LICENSE \
  --toolbench-input path/to/toolbench_sample.jsonl \
  --toolbench-license REVIEWED_LICENSE \
  --split validation \
  --limit-per-source 10 \
  --output evals/datasets/generated/open_source_agent_eval.jsonl
```

导入规则：

- 只读取本地文件中的前 `--limit-per-source` 条样本。
- 每条样本都会保留 `original_id`、`source_url`、`license`、`split`。
- 如果 `--split test`、`official_test` 或 `formal_test` 且 license 仍为 `UNKNOWN_NEEDS_REVIEW`，脚本会拒绝导入。
- 对无法自动确定 `gold_answer` 的样本，`label_review.status` 保持为 `needs_review`。
- Adapter 会根据源数据字段生成 `category`、`expected_nodes`、`expected_tools`、`max_steps`、`loop_rules` 和 `scoring`。

也可以运行下载脚本获取公开小样本并生成统一数据集：

```bash
pip install modelscope pyarrow
python evals/scripts/download_open_source_samples.py \
  --limit-per-source 5 \
  --split validation \
  --output evals/datasets/generated/open_source_agent_eval.jsonl
```

当前默认下载源：

- GAIA: `AI-ModelScope/GAIA:2023/validation/metadata.level1.parquet`，通过 ModelScope CLI 下载小型 validation metadata。license 仍为 `UNKNOWN_NEEDS_REVIEW`，不得导入正式 `test` split。
- AgentBench: `THUDM/AgentBench:data/knowledgegraph/dev.json`，GitHub API 标识 license 为 `Apache-2.0`。
- ToolBench: `OpenBMB/ToolBench:data_example/instruction/G1_query.json`，GitHub API 标识 license 为 `Apache-2.0`。

脚本会把源小样本保存到 `evals/datasets/source_samples/`，再生成 `evals/datasets/generated/open_source_agent_eval.jsonl`。当前已生成的数据集包含 15 条真实小样本：5 条 GAIA、5 条 AgentBench、5 条 ToolBench。

## 四层 Split

运行以下命令从三份输入文件整理四层数据集：

```bash
python evals/scripts/build_dataset_splits.py
```

输入文件：

- `evals/datasets/seed/eval_cases.jsonl`
- `evals/datasets/generated/multy_agent_loop_eval.jsonl`
- `evals/datasets/generated/open_source_agent_eval.jsonl`

输出文件：

- `formal/formal_loop_minimal.jsonl`：正式最小评测集。只允许 `label_review.status == "approved"`、`active == true`、非 `dry_run`、非 placeholder，且 license 为 `project_internal` 或已确认 license。formal 校验会拒绝 `UNKNOWN_NEEDS_REVIEW`、`needs_review`、`dry_run` 和 placeholder。
- `dev/dev_loop_eval.jsonl`：日常开发评测集。包含 seed、synthetic 和真实导入 open-source 样本，不包含 `*_slot_*` 占位样本。
- `staging/open_source_agent_eval.jsonl`：开源样本暂存审核集。用于存放 license 或 gold behavior 仍需人工确认的 open-source 样本。
- `templates/open_source_slots.jsonl`：模板集。只包含 adapter placeholder slot，用于后续人工导入时参考 schema。

构建时会补齐并规范化字段：

- `active`
- `split`
- `source`
- `loop_rules.max_same_tool_same_args`
- `scoring_mode`
- `expected_tool_categories`
- `expected_project_tools`
- `expected_source_tools`

`expected_tools` 会被拆分为上述 3 个工具字段。seed 和 synthetic 的 license 标为 `project_internal`；无法确认 license 的开源样本继续保留 `UNKNOWN_NEEDS_REVIEW`。
