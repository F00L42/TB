# 开发接手导航

先执行 `git branch --show-current`，再读本分支根 README/RESULT。交付评估与未决项见
[两分支梳理](delivery_review.md)。本文属于 DTB 特性分支，不是独立观察器 SDK 的 API 文档。

## 按工作定位

| 要完成的工作 | 先读 | 应当能产出的结果 |
| --- | --- | --- |
| 理解当前请求路径 | [DTB README](../../dtb/README.md)、[模块导航](../../dtb/AGENTS.md) | 画出 native / 流式 observation / 非流式三状态分支，找到实现与回归 |
| 升级 vLLM / Ascend | [版本迁移手册](vllm_upgrade.md)、[当前 patch](../../patches/vllm/v0.23.0/README.md) | 固定目标 SHA、接口差异表、最小 patch、包内适配和验收清单 |
| 修改场景或预算 | [路由设计](../design/dtb_system_scene_native_routing.md)、[部署手册](../design/dtb_system_routing_deployment.md) | 明确改规范 YAML 还是 controller；记录优先级、重启及回滚 |
| 修改输入/输出/parser 接口 | [token 权威输入](../design/dtb_token_authoritative_prompt.md)、[边界契约](../design/dtb_reasoning_boundary_token_stop.md) | 保护原生 token/text 连续性、child 快照、结束和取消语义 |
| 在 SDK 上开发重复检测 | 切到 `codex/thinking-observer`，读 `thinking_observer/docs/detector_development.md`，运行 `python thinking_observer/examples/observe_window.py` | 从 `Detector.start(context)` 返回的请求状态的 `observe(ReasoningDelta)` 获取增量，使用有界窗口；算法独立实现 |
| 复核演示与收益 | [最终演示](../report/closeout/presentation/README.md)、[技术博客](../report/closeout/dtb_project_technical_blog.md)、[E2E 博客](../report/closeout/noemate_e2e_20261013.md) | 找到分母、固定来源、反例和结论边界 |
| 清理实验产物 | [下方保留规则](#产物保留规则)、[fixture 清单](../../tests/fixtures/README.md) | 不丢唯一证据、不使回归静默跳过、不删除构建输入 |

## 文档层次

- 根 README/RESULT：分支能力、当前状态、验证结果；不要用历史测试计数代表本轮。
- [设计索引](../design/README.md)：集中阅读顺序，专题原文保留其约束和依据。
- [DTB AGENTS](../../dtb/AGENTS.md)：符号到测试的映射及请求/服务所有权。
- 部署/验证手册：可执行命令及失败条件。当前完整本地门禁是 `python tools/dtb/check_static.py`。
- 报告与证据：固定过去的源码/实验身份，不能随当前实现更新而重写历史数字。

## 产物保留规则

| 类别 | 放置与保留规则 |
| --- | --- |
| 生产源码、版本 patch、配置与部署手册 | 跟踪；一套当前版本的来源，不能复制已应用的上游实现进包 |
| 正式回归输入 | 保留 `tests/fixtures/` 与 `validation/replay/`；来源/hash 和失败用例同属交付内容 |
| 最终演示、博客、结论 | 保留 `docs/report/closeout/`；当前演示 r10，r5/r9 是构建链必需输入 |
| 精简原始证据投影 | 保留完整样本投影、manifest、反例和复算脚本；不得为缩小体积改变分母 |
| 采集日志与派生详情 | 新 run 进入 Git 忽略的 `artifacts/perf/<run_id>/`；保存归档身份后再决定是否退出源码树 |
| 纯缓存与无依赖旧演示 | 可删除；先确认没有本地修改、生成链依赖和 junction/reparse point |
| 未跟踪的唯一服务器结果 | 先归档并核验，不能仅凭“Git 忽略”就删除 |

本轮移出的 Noemate run 可从 `03d9ec6c3d7b48dc7925fc024b63462bd9fe7855` 取回；
使用证据包的 Git blob 提取器即可，不需 checkout 全目录。历史对象未重写，完整 clone 的历史体积不会变小。
不需要历史大对象的源码接手可用浅克隆；从原始 Git capture 重建时仍须补齐证据文档列出的固定提交。

完整 E2E 统计快照的逐值复算本轮在 Python 3.14.6 通过；Python 3.12.14 在
`initial_root_comparison` 的精确比较未通过。使用相同运行时复算，不放宽比较或覆盖冻结证据。
冻结 CSV 保留原字节，由 bundle manifest 校验；不是一般 CSV 的换行豁免。

## 完成一次改动

先确认分支和工作区改动 → 定位符号/测试 → 修改最小边界 → 本地门禁 → 同步当前文档/变更记录。
涉及引擎、parser、取消或性能时，补做目标服务器验收并记录实际环境；未执行部分保留 `pending_remote`。
当前本地门禁通过也不自动代表 SDK、新版 vLLM 或历史 DEER 路径通过。
