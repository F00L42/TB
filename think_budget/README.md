# 独立 Think Budget 参考实现

`think_budget/` 是原始 BudgetForcing 研究代码库。它作为 DTB 算法的离线参考来源保留，并不是生产服务路由。

## 范围

- 使用自身的 `naie/` 应用结构与 completions 风格模型客户端。
- 包含位于 `docs/` 下的研究与评测资料。
- 未实现生产适配器的请求模型、字段级外部覆盖约定、耗时遥测和可续接流式会话生命周期。

生产 vLLM `v0.23.0` 部署应使用 [`../dtb/`](../dtb/README.md) 和
[`../patches/vllm/v0.23.0/`](../patches/vllm/v0.23.0/README.md)。后续实验流程以
[`think_budget_generalization_plan.md`](../docs/design/think_budget_generalization_plan.md) 为准。

## 公开发布检查

公开前，请检查并外置本地端点、数据集路径、模型路径和组织专用任务模式。研究文档中的历史基准数字必须有可复现实验记录后才能作为结论引用；它们不是本仓库的发布验收标准。

将 `.env.example` 复制到部署使用的密钥管理位置，并通过进程环境变量配置。客户端在所选模型 URL 和密钥均提供前会拒绝发起模型调用，绝不会回退到源码中写死的端点或凭据。
