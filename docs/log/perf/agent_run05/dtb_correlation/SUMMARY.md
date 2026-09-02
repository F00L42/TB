# LightCode ↔ proxy 关联报告

## 结论

使用每个请求的首个 user prompt 精确 SHA-256 作为 agent 锚点，再把同一 segment 内的辅助请求归到唯一 task；未修改评测 prompt。已映射 28/29 个 proxy session、442/443 个请求，以及 25/25 个实际启动的 LightCode agent session。

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
| [task_0a6202f300fc4951_r1](tasks/task_0a6202f300fc4951_r1.md) | 2_DataSmartCubeService_ece9bf60.json | `2_DataSmartCubeService_ece9bf60` | FINISHED | 3 | 3 | 24 | 280671.036 | 28911 | 2 |
| [task_1006deac18fd442e_r1](tasks/task_1006deac18fd442e_r1.md) | 3_DataSmartCubeService_31f1241c.json | `3_DataSmartCubeService_31f1241c` | FINISHED | 3 | 3 | 33 | 278054.037 | 24583 | 2 |
| [task_2dd0668a3d4b4d27_r1](tasks/task_2dd0668a3d4b4d27_r1.md) | 26_ExperienceAssuranceService_0222e644.json | `26_ExperienceAssuranceService_0222e644` | FINISHED | 2 | 2 | 17 | 133675.959 | 10066 | 2 |
| [task_7bc5f2fed23a4615_r1](tasks/task_7bc5f2fed23a4615_r1.md) | 37_DVAIAgentService_11718ad6.json | `37_DVAIAgentService_11718ad6` | FINISHED | 4 | 3 | 32 | 254023.304 | 21059 | 2 |
| [task_7e0616c06b124fc1_r1](tasks/task_7e0616c06b124fc1_r1.md) | 28_ExperienceAssuranceService_42e693fa.json | `28_ExperienceAssuranceService_42e693fa` | FINISHED | 3 | 3 | 127 | 796790.94 | 61172 | 2 |
| [task_a1683f4ef2db4d1b_r1](tasks/task_a1683f4ef2db4d1b_r1.md) | 29_ExperienceAssuranceService_cfd6a6f0.json | `29_ExperienceAssuranceService_cfd6a6f0` | FINISHED | 3 | 4 | 57 | 381123.623 | 32350 | 2 |
| [task_a2bc2cbecc624940_r1](tasks/task_a2bc2cbecc624940_r1.md) | 34_DVAIAgentService_6f4c4230.json | `34_DVAIAgentService_6f4c4230` | FINISHED | 3 | 3 | 32 | 218341.171 | 16254 | 2 |
| [task_e2234117c8984e63_r1](tasks/task_e2234117c8984e63_r1.md) | 35_DVAIAgentService_00725ad1.json | `35_DVAIAgentService_00725ad1` | FINISHED | 2 | 3 | 82 | 479069.96 | 37278 | 2 |
| [task_e95f366da8554c7d_r1](tasks/task_e95f366da8554c7d_r1.md) | 36_DVAIAgentService_ed9292b4.json | `36_DVAIAgentService_ed9292b4` | FINISHED | 3 | 4 | 38 | 283334.893 | 19558 | 0 |

## 机器可读产物

- `agent_map.jsonl`：LightCode 主/子 agent 到 proxy segment 的映射和聚合。
- `requests.jsonl`：逐请求指标、任务身份及原始 shard 行号。
- `unmatched.json`：所有未映射、歧义、未启动和一致性异常。
