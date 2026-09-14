# LightCode ↔ proxy 关联报告

## 结论

使用每个请求的首个 user prompt 精确 SHA-256 作为 agent 锚点，再把同一 segment 内的辅助请求归到唯一 task；未修改评测 prompt。已映射 27/28 个 proxy session、349/350 个请求，以及 26/26 个实际启动的 LightCode agent session。

一个 LightCode agent session 可以对应多个 proxy session segment，同一 segment 也可能包含同一 task 的主 agent、子 agent 和 title generation 等辅助请求。agent 身份只赋给精确 prompt 命中的请求；其余请求仅在 segment 内所有 agent 锚点都属于同一 task 时标为 auxiliary。segment 命中多个 task 时判为歧义。没有官方子 session ID 的 task 工具记录视为未启动调用，单列但不计入关联分母。

## 完整性

- 未映射 proxy session：1
- 未映射已启动 LightCode agent：0
- 未启动 subagent 调用：1
- prompt 同时命中多个官方 agent 的请求：0
- 歧义 proxy session：0

## 逐任务

| Task | Case | Branch | Status | Agents | Proxy segments | Requests | LLM E2E ms | Completion tokens | Reports |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [task_096f8f6866ac41e1_r1](tasks/task_096f8f6866ac41e1_r1.md) | 2_DataSmartCubeService_ece9bf60.json | `2_DataSmartCubeService_ece9bf60` | ROUND_COMPLETED | 3 | 3 | 41 | 382988.488 | 38933 | 2 |
| [task_1999d314b33f4c07_r1](tasks/task_1999d314b33f4c07_r1.md) | 37_DVAIAgentService_11718ad6.json | `37_DVAIAgentService_11718ad6` | FINISHED | 3 | 3 | 33 | 248563.308 | 20999 | 2 |
| [task_2aaf4b0491f543a4_r1](tasks/task_2aaf4b0491f543a4_r1.md) | 36_DVAIAgentService_ed9292b4.json | `36_DVAIAgentService_ed9292b4` | FINISHED | 4 | 3 | 42 | 297458.456 | 24104 | 2 |
| [task_4a510b43b97841c7_r1](tasks/task_4a510b43b97841c7_r1.md) | 3_DataSmartCubeService_31f1241c.json | `3_DataSmartCubeService_31f1241c` | ROUND_COMPLETED | 3 | 3 | 35 | 301785.13 | 29546 | 2 |
| [task_53694f706c5d48e0_r1](tasks/task_53694f706c5d48e0_r1.md) | 28_ExperienceAssuranceService_42e693fa.json | `28_ExperienceAssuranceService_42e693fa` | FINISHED | 3 | 3 | 48 | 315265.405 | 29937 | 2 |
| [task_6c62b3a2474e4c73_r1](tasks/task_6c62b3a2474e4c73_r1.md) | 35_DVAIAgentService_00725ad1.json | `35_DVAIAgentService_00725ad1` | FINISHED | 2 | 2 | 50 | 310991.703 | 29002 | 2 |
| [task_7a1bc74bc2f3413d_r1](tasks/task_7a1bc74bc2f3413d_r1.md) | 34_DVAIAgentService_6f4c4230.json | `34_DVAIAgentService_6f4c4230` | FINISHED | 3 | 3 | 24 | 144833.012 | 12324 | 2 |
| [task_8be00cf3c1044f78_r1](tasks/task_8be00cf3c1044f78_r1.md) | 29_ExperienceAssuranceService_cfd6a6f0.json | `29_ExperienceAssuranceService_cfd6a6f0` | ROUND_COMPLETED | 3 | 3 | 34 | 308150.373 | 30420 | 2 |
| [task_ca5d56b98d99410e_r1](tasks/task_ca5d56b98d99410e_r1.md) | 26_ExperienceAssuranceService_0222e644.json | `26_ExperienceAssuranceService_0222e644` | FINISHED | 3 | 4 | 42 | 250683.604 | 21461 | 2 |

## 机器可读产物

- `agent_map.jsonl`：LightCode 主/子 agent 到 proxy segment 的映射和聚合。
- `requests.jsonl`：逐请求指标、任务身份及原始 shard 行号。
- `unmatched.json`：所有未映射、歧义、未启动和一致性异常。
