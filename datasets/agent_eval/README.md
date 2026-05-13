# Agent 评测数据集

该目录用于评测多 Agent 研究助手的执行稳定性和结果质量。数据集重点不是训练模型，而是为 LangGraph 工作流和线性 ReAct baseline 提供同一组可复现任务。

## 文件

- `eval_cases.jsonl`：评测样本，每行一个 JSON 对象。
- `schema.json`：评测样本字段契约。
- `README.md`：数据集说明和扩展规则。

## 核心原则

A/B 结论只能来自真实执行 trace。评测程序必须让 `langgraph_state_machine` 和 `linear_react_baseline` 都实际运行同一个 case；启用 worker ablation 时，还必须实际运行 `langgraph_react_worker`。每个执行结果都要记录节点或动作、工具调用、状态指纹、状态 diff、步数、停止原因和错误信息。

`ab_test` 字段只是实验假设说明。即使样本里写了 `hypothesis_pass`、`hypothesis_loop` 或 `hypothesis_stop_reason`，这些字段也不能作为 LangGraph 解决 ReAct 死循环的证据。

## 样本字段

| 字段 | 说明 |
| --- | --- |
| `id` | 样本唯一 ID，格式为 `<prefix>_<number>`。 |
| `category` | 样本类别。 |
| `difficulty` | 难度，取值为 `easy`、`medium`、`hard`。 |
| `user_query` | 输入给 Agent 的用户任务。 |
| `available_sources` | 可用资料快照或本地真实文件路径。 |
| `gold_answer` | 标准答案或结构化判定信息。 |
| `gold_behavior` | 正确行为描述，包括应完成、应拒绝、应挂起或应停止。 |
| `evidence` | 支撑标准答案的证据片段，`quote` 必须能在对应 source 文件中逐字匹配。 |
| `expected_nodes` | LangGraph 路径的预期节点，仅用于图工作流覆盖检查，不能用于惩罚 ReAct。 |
| `expected_tools` | 预期调用或尝试调用的工具。 |
| `max_steps` | 单次任务最大允许执行步数。 |
| `loop_rules` | 死循环检测阈值，包括最大总步数、重复同工具同参数次数、连续无状态变化步数。 |
| `ab_test` | 可选 A/B 假设说明，必须标记 `hypothesis_only: true`。 |
| `scoring` | 评分权重，所有权重之和必须为 1.0。 |

## A/B 假设字段

`ab_test` 示例：

```json
{
  "experiment": "langgraph_vs_react_loop",
  "primary_metric": "loop_rate",
  "hypothesis_only": true,
  "variants": {
    "langgraph_state_machine": {
      "hypothesis_pass": true,
      "hypothesis_loop": false,
      "hypothesis_stop_reason": "controlled_stop",
      "hypothesis_notes": "预计 LangGraph 会在有限步骤内停止。"
    },
    "linear_react_baseline": {
      "hypothesis_pass": false,
      "hypothesis_loop": true,
      "hypothesis_stop_reason": "loop_detected",
      "hypothesis_notes": "预计 ReAct baseline 会暴露重复检索风险。"
    },
    "langgraph_react_worker": {
      "hypothesis_pass": false,
      "hypothesis_loop": true,
      "hypothesis_stop_reason": "loop_detected",
      "hypothesis_notes": "预计 worker-level ablation 会隔离 ReAct worker 在 LangGraph 外壳内的循环风险。"
    }
  }
}
```

这些字段只能帮助解释实验设计，不能参与实际 pass/fail 判定。实际判定必须使用 runner 输出的 `RunResult` 和 case 的 `loop_rules`。

## 建议指标

- `Task Success Rate`：任务最终是否按 `gold_behavior` 完成。
- `Loop Rate`：是否触发 `loop_rules` 中的循环判定。
- `Max Step Abort Rate`：是否因超过最大步数终止。
- `Repeated Tool Call Rate`：相同工具和等价参数的重复调用比例。
- `No State Change Steps`：连续多少步没有状态变化。
- `Stop Reason Counts`：`completed`、`controlled_stop`、`loop_detected`、`max_steps_exceeded`、`tool_failure`、`human_input_required`、`runtime_error` 的分布。

## 扩展规则

1. 每行必须是合法 JSON，不能有空行。
2. `id` 必须唯一。
3. `category` 必须属于 `schema.json` 中定义的枚举。
4. `scoring` 权重之和必须为 1.0。
5. `loop_stability` 样本必须明确写出停止、终止、挂起或说明缺失的正确行为。
6. 所有非 `none` source 必须是真实存在的本地文件，不能使用占位路径。
7. 需要答案级评测的样本必须提供 `gold_answer` 和 `evidence`。
8. `evidence.quote` 必须能在 `available_sources` 对应文件中逐字找到。
9. 财报计算类样本必须提供 `calculation`，并能由测试复算。
10. ReAct baseline 不能因为缺少 `planner`、`worker`、`reviewer` 等 LangGraph 节点而被判失败。
