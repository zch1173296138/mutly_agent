## Why

当前项目已经具备 Controller、Planner、Worker、Reviewer 的 LangGraph 工作流，但缺少一套可复现的评测数据集来验证多 Agent 执行稳定性、工具调用正确性和最终报告质量。新增评测数据集可以把「是否缓解线性 ReAct 死循环和状态失控」转化为可测试、可回归的工程指标。

## What Changes

- 新增 Agent 评测数据集目录，包含可机器校验的 JSON Schema、JSONL 评测样本和使用说明。
- 覆盖 Controller 路由、Planner 任务拆解、RAG 检索、PDF / MinerU 解析、财报数值问答、工具缺失、HITL 和死循环压力场景。
- 为每条样本定义标准行为、期望节点、期望工具、最大步数、循环检测规则和评分权重。
- 新增 pytest 测试，校验数据集结构、ID 唯一性、分类覆盖、评分权重和循环压力样本完整性。

## Capabilities

### New Capabilities
- `agent-eval-dataset`: 定义用于评测 LangGraph 多 Agent 研究协作系统的数据集格式、样本要求和质量约束。

### Modified Capabilities
- 无。

## Impact

- 新增 `datasets/agent_eval/` 下的数据集文件和文档。
- 新增测试文件，使用 Python 标准库校验数据集，不引入新的运行时依赖。
- 不修改现有 Agent 工作流、API、数据库或前端行为。
