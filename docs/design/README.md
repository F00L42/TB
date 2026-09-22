# 设计文档索引

状态核对基线：`f342e1276a0d8b9c8b2c02f57917b19be936b799`，加上
`codex/dtb-static-refactor` 的静态优化。日期：2026-09-22。
此处的“已实现”只表示代码存在；每次服务器验收的模型、参数、SHA 和质量结论仍以该 run 的
`RESULT.md` 为准。文档中的历史哈希、旧路径、测试数不能作为当前部署身份。

查当前运行方式先读 [DTB README](../../dtb/README.md) 和
[代码导航](../../dtb/AGENTS.md)，查原理再进入下表。修改功能时同步更新相应文档和本索引；
不要把待实现设计里的 YAML、符号或流程写成现有接口。

| 文档 | 状态与用途 | 当前实现或证据入口 |
| --- | --- | --- |
| [静态 CPU 优化与重构](dtb_static_cpu_refactor.md) | 已实现，本地交付；服务器 `pending_remote` | 路由、非流式聚合、final 私有方法；附统一检查命令 |
| [最低介入值](dtb_minimum_intervention_threshold.md) | observation 主流程已实现；§12 的普通 H 文本归一化仍是候选 | `deferred_observation.py`；实现范围见原文 §11.4，不能把全文视为部署完成 |
| [Token ID 权威输入](dtb_token_authoritative_prompt.md) | 已实现；保留输入文本快照及 token 续写所有权 | `request_context.py`、`request_adapter.py` |
| [reasoning token-stop](dtb_reasoning_boundary_token_stop.md) | Qwen 流式协议和三份 patch 已实现；保留非流式路径 | `parser_boundary.py`、版本化 [patch 说明](../../patches/vllm/v0.23.0/README.md) |
| [最终答案兼容性](think_budget_finalization_compatibility.md) | 已实现并持续演进；正文含 v3/v4 与特定 run 的历史结论 | `generation_states.py`、`schema.py`；当前 trial cue 以源码为准 |
| [FinalAnswerState 重构](dtb_final_answer_state_refactor.md) | 本分支已完成局部方法提取；历史评审和测试数保留日期 | `_FinalAnswerState`、`test_dtb_final_answer.py` |
| [包清理和 Telemetry](dtb_cleanup_and_telemetry_refactor_plan.md) | 2026-08-20 P0–P4 已实施，记录旧实现/制品哈希 | `dtb/`、`observability/`；历史 P5 状态不代替后来各 run 的验收 |
| [泛化实验流程](think_budget_generalization_plan.md) | 当前实验/完整性/质量/配对统计规范 | 根 AGENTS 指定的第 6 节；不据旧 run 推算新模型基线 |
| [Agent 多轮实验](agent_multiturn_dtb_experiment_plan.md) | 实验流程，含历史扫描候选 | 先读页首更新和 [proxy 约束](../../proxy/AGENTS.md) |
| [Agent 自动预算初稿](dtb_agent_scene_budget_plan.md) | 待实现设计；其性能门槛为目标 | 不存在已交付的 system 身份自动策略；不等同于当前 user-text 分类 |
| [System 规则与原生路由](dtb_system_scene_native_routing.md) | 基线未实现，本分支未增加这项能力 | 当前 `visible_user_text()` 和 scene resolver；其他工作区改动需独立合入验收 |
| [GLM-5.3 加速计划](dtb_glm53_agent_acceleration_plan.md) | tokenizer/源码分析和适配计划，非 GLM 生产验收 | `ReasoningProtocolAdapter` 当前只有 QWEN3；不能只换 token ID |
| [Qwen/GLM parser 对比](dtb_qwen36_glm53_parser_comparison.md) | 固定源码和 tokenizer 诊断 | 上游 parser；离线回放不等于 GLM/NPU 性能 |
| [通用 Agent 观测代理](general_agent_observation_proxy.md) | 首版已实现；剩余设计见 §7.4 | [使用说明](../../proxy/README-observation.md) |
| [收尾交接](dtb_closeout_handoff.md) | 跨任务工作清单和既有证据索引，不是生产配置规范 | f342e127 已提交分析快照；各后续任务的完成情况另查其分支/提交 |
| [服务内迁移](budget_forcing_migration.md) | 历史背景，旧 adapter 路径停用 | 当前生产包为 `dtb/` |
| [迁移评审](budget_forcing_migration_review.md) | 2026-07-10 历史评审，引用当时的 `naie/` | 不把旧问题列表当作当前缺陷清单 |

## 文档冲突的处理

1. 先确定讨论的是当前生产源码、历史实现、候选方案还是特定服务器 run。
2. 当前行为核对源码、YAML、patch 及对应测试；部署命令以版本化 patch README 为准。
3. 历史结论保留原实验身份，不覆盖成新参数；设计发生演进时在页首标注状态并链接新入口。
4. 源码确定、本地合成测试、实机结果分别记录。当前没有服务器访问能力，不能用本地 PASS
   替代 `pending_remote`，也不能因本文更新日期较新就宣布旧服务器验收失效。
