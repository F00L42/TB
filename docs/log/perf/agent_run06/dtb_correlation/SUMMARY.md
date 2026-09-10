# LightCode ↔ proxy 关联报告

## 结论

使用每个请求的首个 user prompt 精确 SHA-256 作为 agent 锚点，再把同一 segment 内的辅助请求归到唯一 task；未修改评测 prompt。已映射 31/32 个 proxy session、342/343 个请求，以及 27/27 个实际启动的 LightCode agent session。

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
| [task_11b57d5f886944b0_r1](tasks/task_11b57d5f886944b0_r1.md) | 26_ExperienceAssuranceService_0222e644.json | `26_ExperienceAssuranceService_0222e644` | ROUND_COMPLETED | 3 | 4 | 34 | 250364.896 | 20900 | 2 |
| [task_3aceb40d1d0740b9_r1](tasks/task_3aceb40d1d0740b9_r1.md) | 34_DVAIAgentService_6f4c4230.json | `34_DVAIAgentService_6f4c4230` | FINISHED | 3 | 4 | 30 | 191821.757 | 15817 | 2 |
| [task_3ff67d1386ae44b8_r1](tasks/task_3ff67d1386ae44b8_r1.md) | 29_ExperienceAssuranceService_cfd6a6f0.json | `29_ExperienceAssuranceService_cfd6a6f0` | FINISHED | 3 | 3 | 30 | 299654.028 | 29363 | 2 |
| [task_40b46c752c374b54_r1](tasks/task_40b46c752c374b54_r1.md) | 28_ExperienceAssuranceService_42e693fa.json | `28_ExperienceAssuranceService_42e693fa` | FINISHED | 4 | 3 | 58 | 409098.861 | 38283 | 2 |
| [task_5207955a0e3b43da_r1](tasks/task_5207955a0e3b43da_r1.md) | 37_DVAIAgentService_11718ad6.json | `37_DVAIAgentService_11718ad6` | FINISHED | 3 | 4 | 35 | 270430.951 | 21452 | 2 |
| [task_8bfc6cf89d744f60_r1](tasks/task_8bfc6cf89d744f60_r1.md) | 2_DataSmartCubeService_ece9bf60.json | `2_DataSmartCubeService_ece9bf60` | FINISHED | 3 | 4 | 30 | 372685.963 | 36822 | 2 |
| [task_9ef563c35da84ea1_r1](tasks/task_9ef563c35da84ea1_r1.md) | 36_DVAIAgentService_ed9292b4.json | `36_DVAIAgentService_ed9292b4` | FINISHED | 3 | 3 | 50 | 308524.513 | 25268 | 2 |
| [task_ac20a2ae7ba148ae_r1](tasks/task_ac20a2ae7ba148ae_r1.md) | 3_DataSmartCubeService_31f1241c.json | `3_DataSmartCubeService_31f1241c` | FINISHED | 3 | 3 | 48 | 290597.541 | 26092 | 2 |
| [task_e3509fe91c7b4b75_r1](tasks/task_e3509fe91c7b4b75_r1.md) | 35_DVAIAgentService_00725ad1.json | `35_DVAIAgentService_00725ad1` | FINISHED | 3 | 3 | 27 | 406361.955 | 42249 | 2 |

## 机器可读产物

- `agent_map.jsonl`：LightCode 主/子 agent 到 proxy segment 的映射和聚合。
- `requests.jsonl`：逐请求指标、任务身份及原始 shard 行号。
- `unmatched.json`：所有未映射、歧义、未启动和一致性异常。
