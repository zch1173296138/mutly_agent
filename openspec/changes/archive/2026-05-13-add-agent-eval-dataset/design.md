## Context

当前项目的核心执行状态集中在 `AgentState`，包括 `tasks`、`tool_history`、`task_results`、`ready_tasks` 和 `final_report`。评测数据集需要同时描述最终答案是否正确，以及执行过程是否稳定，例如是否重复调用同一工具、是否超过最大步数、是否在资料缺失时停止。

## Goals / Non-Goals

**Goals:**
- 提供一套可复现、可机器校验的 Agent 评测数据集。
- 覆盖多 Agent 工作流中的路由、规划、工具调用、检索、PDF 解析、财报问答、HITL 和循环压力场景。
- 通过测试保证样本结构完整、类别覆盖充分、评分权重合法、循环压力样本有明确失败判定。

**Non-Goals:**
- 不实现完整评测运行器，不调用真实 LLM 或 MCP 工具。
- 不修改现有 LangGraph 节点逻辑。
- 不下载外部 PDF、网页或财报文件，样本中的 source 使用稳定的本地占位路径，后续可替换为真实快照。

## Decisions

- 使用 JSONL 存储样本：每行一条评测任务，便于增量追加、流式读取和人工审查。备选方案是单个 JSON 数组，但追加和 diff 都不如 JSONL 清晰。
- 使用 `schema.json` 描述字段契约：不引入 `jsonschema` 依赖，测试中用 Python 标准库校验关键字段和语义规则。这样能保持当前依赖稳定。
- 样本字段同时包含结果约束和过程约束：`gold_behavior` 描述正确行为，`expected_nodes`、`expected_tools`、`max_steps`、`loop_rules` 描述执行边界，适配当前 LangGraph 状态机的 trace 评测需求。
- 首批数据集保持中等规模：提供 24 条覆盖样本，先证明格式和覆盖面，再为真实文档快照和自动化评测 runner 留扩展空间。

## Risks / Trade-offs

- 评测样本中的部分 source 是占位路径 → README 明确说明首批数据集用于结构和稳定性评测，接入真实文件后再做答案级自动评分。
- 不引入 JSON Schema 校验库 → 测试不会覆盖完整 Draft 规范，但能覆盖当前项目最重要的质量约束。
- 仅新增静态数据和测试 → 暂时不能直接得出模型指标，需要后续接入 trace 采集与评测 runner。
