# LightCode ↔ proxy 关联报告

## 结论

使用每个请求的首个 user prompt 精确 SHA-256 作为 agent 锚点，再把同一 segment 内的辅助请求归到唯一 task；未修改评测 prompt。已映射 25/26 个 proxy session、273/274 个请求，以及 22/22 个实际启动的 LightCode agent session。

一个 LightCode agent session 可以对应多个 proxy session segment，同一 segment 也可能包含同一 task 的主 agent、子 agent 和 title generation 等辅助请求。agent 身份只赋给精确 prompt 命中的请求；其余请求仅在 segment 内所有 agent 锚点都属于同一 task 时标为 auxiliary。segment 命中多个 task 时判为歧义。没有官方子 session ID 的 task 工具记录视为未启动调用，单列但不计入关联分母。

## 完整性

- 未映射 proxy session：1
- 未映射已启动 LightCode agent：0
- 未启动 subagent 调用：0
- prompt 同时命中多个官方 agent 的请求：0
- 歧义 proxy session：0

## 逐任务

| Task | Case | Branch | Status | Agents | Proxy segments | Requests | LLM E2E ms | Completion tokens | Reports |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [task_09cc6464d5bf4093_r1](tasks/task_09cc6464d5bf4093_r1.md) | 34_DVAIAgentService_6f4c4230.json | `34_DVAIAgentService_6f4c4230` | FINISHED | 3 | 3 | 28 | 179359.025 | 13113 | 2 |
| [task_0ed6a349ce96466b_r1](tasks/task_0ed6a349ce96466b_r1.md) | 28_ExperienceAssuranceService_42e693fa.json | `28_ExperienceAssuranceService_42e693fa` | FINISHED | 2 | 2 | 36 | 254460.449 | 22130 | 2 |
| [task_40c6775d1e3d4914_r1](tasks/task_40c6775d1e3d4914_r1.md) | 26_ExperienceAssuranceService_0222e644.json | `26_ExperienceAssuranceService_0222e644` | FINISHED | 3 | 4 | 40 | 260813.193 | 22563 | 2 |
| [task_5e7acfad2ec04e85_r1](tasks/task_5e7acfad2ec04e85_r1.md) | 37_DVAIAgentService_11718ad6.json | `37_DVAIAgentService_11718ad6` | FINISHED | 2 | 2 | 28 | 287821.855 | 26245 | 2 |
| [task_64337995e17b4d94_r1](tasks/task_64337995e17b4d94_r1.md) | 2_DataSmartCubeService_ece9bf60.json | `2_DataSmartCubeService_ece9bf60` | FINISHED | 3 | 4 | 47 | 318223.734 | 28565 | 2 |
| [task_90079c04aee24ea6_r1](tasks/task_90079c04aee24ea6_r1.md) | 35_DVAIAgentService_00725ad1.json | `35_DVAIAgentService_00725ad1` | FINISHED | 1 | 1 | 6 | 60897.33 | 3614 | 0 |
| [task_e6cc8f2027f24f97_r1](tasks/task_e6cc8f2027f24f97_r1.md) | 29_ExperienceAssuranceService_cfd6a6f0.json | `29_ExperienceAssuranceService_cfd6a6f0` | FINISHED | 3 | 4 | 28 | 335428.413 | 30730 | 2 |
| [task_f637076490574c2a_r1](tasks/task_f637076490574c2a_r1.md) | 36_DVAIAgentService_ed9292b4.json | `36_DVAIAgentService_ed9292b4` | FINISHED | 3 | 3 | 22 | 222038.69 | 18421 | 0 |
| [task_fcaf871bd3224e82_r1](tasks/task_fcaf871bd3224e82_r1.md) | 3_DataSmartCubeService_31f1241c.json | `3_DataSmartCubeService_31f1241c` | FINISHED | 2 | 2 | 38 | 251751.054 | 19758 | 2 |

## 机器可读产物

- `agent_map.jsonl`：LightCode 主/子 agent 到 proxy segment 的映射和聚合。
- `requests.jsonl`：逐请求指标、任务身份及原始 shard 行号。
- `unmatched.json`：所有未映射、歧义、未启动和一致性异常。
