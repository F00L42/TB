# 当前设计索引

本目录描述清理后的交付版本；历史提案和完整实验过程可在 Git `cb6d67de` 查阅。
新 Session 从根 [RESULT](../../RESULT.md) 获取状态，再按下表定位，不需要遍历历史日志。

## 阅读顺序与信息归属

1. 先读 [DTB 结构与请求执行入口](../../dtb/README.md)，了解 native 分流、流式 observation 和 thinking/probe/final。
2. 按 [模块导航](../../dtb/AGENTS.md) 找实现符号与对应测试，再读下表的专题设计。
3. 部署只按[当前部署手册](dtb_system_routing_deployment.md)；升级按[版本迁移手册](../maintenance/vllm_upgrade.md)。
4. 查看收益时读[技术博客](../report/closeout/dtb_project_technical_blog.md)及[完整 E2E 证据](../report/closeout/noemate_e2e_20261013.md)，不要把历史实验当作当前 checkout 的服务器验收。

本文统一导航，不删除或合并专题正文。职责分为：源码/配置决定当前行为，专题设计解释约束和取舍，
部署手册负责操作顺序，实验契约负责裁决，报告负责固定版本的历史证据。出现冲突时先核对源码与版本，
修正当前入口并保留历史依据；不要从旧方案恢复已经退出交付的功能。

| 主题 | 当前文档 | 状态 |
| --- | --- | --- |
| CPU 静态优化与并发边界 | [静态优化](dtb_static_cpu_refactor.md) | 已实现；目标 E2E 待测 |
| final 职责和结束契约 | [final 结构](dtb_final_answer_state_refactor.md) | 等价方法提取已实现 |
| system 身份、动作及原生分流 | [路由设计](dtb_system_scene_native_routing.md)、[部署](dtb_system_routing_deployment.md) | 已合入 34f39983；自动候选待服务器验收 |
| token 权威续写与所有权 | [prompt 设计](dtb_token_authoritative_prompt.md) | 当前实现 |
| Qwen token-stop/parser 边界 | [边界设计](dtb_reasoning_boundary_token_stop.md) | 已有 patch；60 条冻结链回归 |
| 数据、质量与性能裁决 | [实验契约](think_budget_generalization_plan.md) | 当前流程 |

API/默认参数以 `dtb/config/` 和 [DTB README](../../dtb/README.md) 为准。
Noemate/量化 YAML 是显式选择的候选，不是自动部署、运行中热切换或已通过服务器验收的承诺。
