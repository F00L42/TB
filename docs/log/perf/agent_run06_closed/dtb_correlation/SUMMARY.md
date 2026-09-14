# LightCode ↔ proxy 关联报告

## 结论

使用每个请求的首个 user prompt 精确 SHA-256 作为 agent 锚点，再把同一 segment 内的辅助请求归到唯一 task；未修改评测 prompt。已映射 29/31 个 proxy session、362/364 个请求，以及 27/27 个实际启动的 LightCode agent session。

一个 LightCode agent session 可以对应多个 proxy session segment，同一 segment 也可能包含同一 task 的主 agent、子 agent 和 title generation 等辅助请求。agent 身份只赋给精确 prompt 命中的请求；其余请求仅在 segment 内所有 agent 锚点都属于同一 task 时标为 auxiliary。segment 命中多个 task 时判为歧义。没有官方子 session ID 的 task 工具记录视为未启动调用，单列但不计入关联分母。

## 完整性

- 未映射 proxy session：2
- 未映射已启动 LightCode agent：0
- 未启动 subagent 调用：0
- prompt 同时命中多个官方 agent 的请求：0
- 歧义 proxy session：0

## 逐任务

| Task | Case | Branch | Status | Agents | Proxy segments | Requests | LLM E2E ms | Completion tokens | Reports |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [task_2e968846f7554f4f_r1](tasks/task_2e968846f7554f4f_r1.md) | 2_DataSmartCubeService_ece9bf60.json | `2_DataSmartCubeService_ece9bf60` | FINISHED | 3 | 3 | 40 | 362861.485 | 37051 | 2 |
| [task_33595d58f5ad4a55_r1](tasks/task_33595d58f5ad4a55_r1.md) | 36_DVAIAgentService_ed9292b4.json | `36_DVAIAgentService_ed9292b4` | FINISHED | 3 | 3 | 30 | 188643.839 | 16516 | 2 |
| [task_5d1355921ea447cd_r1](tasks/task_5d1355921ea447cd_r1.md) | 35_DVAIAgentService_00725ad1.json | `35_DVAIAgentService_00725ad1` | FINISHED | 3 | 3 | 47 | 453954.423 | 46056 | 2 |
| [task_6b5be7666c544f7f_r1](tasks/task_6b5be7666c544f7f_r1.md) | 34_DVAIAgentService_6f4c4230.json | `34_DVAIAgentService_6f4c4230` | FINISHED | 3 | 4 | 28 | 170884.004 | 15177 | 2 |
| [task_7b8244804a5345a6_r1](tasks/task_7b8244804a5345a6_r1.md) | 26_ExperienceAssuranceService_0222e644.json | `26_ExperienceAssuranceService_0222e644` | FINISHED | 3 | 4 | 35 | 217193.997 | 18392 | 2 |
| [task_949aa3dfa5d6465f_r1](tasks/task_949aa3dfa5d6465f_r1.md) | 29_ExperienceAssuranceService_cfd6a6f0.json | `29_ExperienceAssuranceService_cfd6a6f0` | FINISHED | 3 | 3 | 44 | 607524.556 | 65845 | 2 |
| [task_b67f136381f84fd6_r1](tasks/task_b67f136381f84fd6_r1.md) | 28_ExperienceAssuranceService_42e693fa.json | `28_ExperienceAssuranceService_42e693fa` | FINISHED | 3 | 3 | 59 | 368069.485 | 35161 | 2 |
| [task_f0a46dd044574b79_r1](tasks/task_f0a46dd044574b79_r1.md) | 37_DVAIAgentService_11718ad6.json | `37_DVAIAgentService_11718ad6` | FINISHED | 3 | 3 | 33 | 284179.05 | 25118 | 2 |
| [task_f1054d6e21214253_r1](tasks/task_f1054d6e21214253_r1.md) | 3_DataSmartCubeService_31f1241c.json | `3_DataSmartCubeService_31f1241c` | FINISHED | 3 | 3 | 46 | 352001.728 | 32396 | 2 |

## 机器可读产物

- `agent_map.jsonl`：LightCode 主/子 agent 到 proxy segment 的映射和聚合。
- `requests.jsonl`：逐请求指标、任务身份及原始 shard 行号。
- `unmatched.json`：所有未映射、歧义、未启动和一致性异常。
