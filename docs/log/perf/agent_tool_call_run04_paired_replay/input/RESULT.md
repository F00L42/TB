# Agent failure paired-replay input

每个历史上下文各自使用完全相同的 request body 执行 Vanilla/DTB A/B。
`semantic_pair_id` 只表示 DTB 故障上下文与 Vanilla 健康上下文属于同一语义阶段；
两份不同历史 body 不直接互算时延。

| Pair | Origin | Category | Case | Expected tools | Archived request |
| --- | --- | --- | --- | --- | --- |
| case36-task-json-truncation-1 | dtb | protocol_failure | 36_DVAIAgentService_ed9292b4.json | task | `02e78969b36f45a2` |
| case36-task-json-truncation-1 | vanilla | protocol_failure | 36_DVAIAgentService_ed9292b4.json | task | `58020b784c544d22` |
| case36-task-json-truncation-2 | dtb | protocol_failure | 36_DVAIAgentService_ed9292b4.json | task | `73dde28da2544e49` |
| case36-task-json-truncation-2 | vanilla | protocol_failure | 36_DVAIAgentService_ed9292b4.json | task | `58020b784c544d22` |
| case36-task-json-truncation-3 | dtb | protocol_failure | 36_DVAIAgentService_ed9292b4.json | task | `d9389420a6c94b84` |
| case36-task-json-truncation-3 | vanilla | protocol_failure | 36_DVAIAgentService_ed9292b4.json | task | `58020b784c544d22` |
| case35-checkout-action-missing | dtb | protocol_failure | 35_DVAIAgentService_00725ad1.json | bash | `af590ee7efa14439` |
| case35-checkout-action-missing | vanilla | protocol_failure | 35_DVAIAgentService_00725ad1.json | bash | `01263fedacc6427e` |
| case36-report-write-action-missing | dtb | protocol_failure | 36_DVAIAgentService_ed9292b4.json | write | `1e1d5c135dfb473f` |
| case36-report-write-action-missing | vanilla | protocol_failure | 36_DVAIAgentService_ed9292b4.json | write | `a60accbd88004f5a` |
| case26-code-mutation-probe | dtb | quality_probe | 26_ExperienceAssuranceService_0222e644.json | write | `91813109ceea425c` |
| case26-code-mutation-probe | vanilla | quality_probe | 26_ExperienceAssuranceService_0222e644.json | write | `c759d659dc864a6a` |
| case28-code-mutation-probe | dtb | quality_probe | 28_ExperienceAssuranceService_42e693fa.json | edit | `130d869c1b6f4caa` |
| case28-code-mutation-probe | vanilla | quality_probe | 28_ExperienceAssuranceService_42e693fa.json | edit,write | `0636852d9a344cc6` |
| case3-xml-mutation-probe | dtb | quality_probe | 3_DataSmartCubeService_31f1241c.json | write | `56986b83de6147d5` |
| case3-xml-mutation-probe | vanilla | quality_probe | 3_DataSmartCubeService_31f1241c.json | write | `12fdbc54c4bd4db4` |

## 使用边界

- `protocol_failure` 用于系统可用性门禁，可做协议通过率和协议通过后的 paired latency。
- `quality_probe` 只回放生成代码的 mutation turn；它能保留真实上下文并采集工具参数，
  但不会执行工具或编译代码，不能替代完整 LightCode 质量评测。
- 所有 latency 只在同一行、同一 request body 的 Vanilla/DTB 之间配对。
