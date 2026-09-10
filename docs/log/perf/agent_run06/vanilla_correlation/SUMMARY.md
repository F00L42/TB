# LightCode ↔ proxy 关联报告

## 结论

使用每个请求的首个 user prompt 精确 SHA-256 作为 agent 锚点，再把同一 segment 内的辅助请求归到唯一 task；未修改评测 prompt。已映射 28/30 个 proxy session、377/392 个请求，以及 27/27 个实际启动的 LightCode agent session。

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
| [task_27105ffe4d174f08_r1](tasks/task_27105ffe4d174f08_r1.md) | 26_ExperienceAssuranceService_0222e644.json | `26_ExperienceAssuranceService_0222e644` | FINISHED | 3 | 3 | 29 | 180138.424 | 15877 | 2 |
| [task_4eff54083e54409d_r1](tasks/task_4eff54083e54409d_r1.md) | 2_DataSmartCubeService_ece9bf60.json | `2_DataSmartCubeService_ece9bf60` | FINISHED | 3 | 3 | 37 | 235867.778 | 22765 | 2 |
| [task_5c1c4b1d693044e3_r1](tasks/task_5c1c4b1d693044e3_r1.md) | 37_DVAIAgentService_11718ad6.json | `37_DVAIAgentService_11718ad6` | FINISHED | 3 | 3 | 47 | 319037.137 | 24693 | 2 |
| [task_a1b7108aa81246e2_r1](tasks/task_a1b7108aa81246e2_r1.md) | 35_DVAIAgentService_00725ad1.json | `35_DVAIAgentService_00725ad1` | FINISHED | 3 | 3 | 50 | 459294.777 | 43482 | 0 |
| [task_a465222813f346ee_r1](tasks/task_a465222813f346ee_r1.md) | 34_DVAIAgentService_6f4c4230.json | `34_DVAIAgentService_6f4c4230` | FINISHED | 3 | 4 | 29 | 182105.041 | 16980 | 2 |
| [task_be4ddcdb9a00494f_r1](tasks/task_be4ddcdb9a00494f_r1.md) | 36_DVAIAgentService_ed9292b4.json | `36_DVAIAgentService_ed9292b4` | FINISHED | 3 | 3 | 42 | 350402.091 | 29423 | 2 |
| [task_d7796811db6f49c5_r1](tasks/task_d7796811db6f49c5_r1.md) | 28_ExperienceAssuranceService_42e693fa.json | `28_ExperienceAssuranceService_42e693fa` | FINISHED | 3 | 3 | 51 | 387230.445 | 38204 | 2 |
| [task_e39ab431bb634fe4_r1](tasks/task_e39ab431bb634fe4_r1.md) | 29_ExperienceAssuranceService_cfd6a6f0.json | `29_ExperienceAssuranceService_cfd6a6f0` | FINISHED | 3 | 3 | 57 | 455543.627 | 43176 | 2 |
| [task_ebf2cbba04144cc4_r1](tasks/task_ebf2cbba04144cc4_r1.md) | 3_DataSmartCubeService_31f1241c.json | `3_DataSmartCubeService_31f1241c` | FINISHED | 3 | 3 | 35 | 270972.577 | 25334 | 2 |

## 机器可读产物

- `agent_map.jsonl`：LightCode 主/子 agent 到 proxy segment 的映射和聚合。
- `requests.jsonl`：逐请求指标、任务身份及原始 shard 行号。
- `unmatched.json`：所有未映射、歧义、未启动和一致性异常。
