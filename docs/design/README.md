# 当前设计索引

本目录描述清理后的交付版本；历史提案和完整实验过程可在 Git `cb6d67de` 查阅。
新 Session 从根 [RESULT](../../RESULT.md) 获取状态，再按下表定位，不需要遍历历史日志。

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
