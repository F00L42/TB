# DTB 服务器验收 runbook

当前生产目标 DTB + vLLM 0.23.0 + vLLM-Ascend 0.23.0rc1。本地没有模型/设备，服务器验收 pending_remote。
初始入口：[RESULT](../../RESULT.md)、[replay](../../validation/replay/README.md)、[实验契约](../design/think_budget_generalization_plan.md)。

1. 记录实际两个服务仓库 SHA/dirty、Python/torch/torch-npu/CANN/模型修订、DTB commit、YAML/patch hash 与完整启动命令。
2. 升级成套 controller/patch，先做原生与 DTB 流式/非流式 smoke。错误、截断、JSON/XML、终态或答案失败即停止。
3. 独立 phase run 确认 main/native/拒识/fixed 路由，以及 direct-final/probe 真实发生和 hidden token 闭合。
   未达到阈值而自然结束只能说路径未覆盖；不能为了通过检查篡改统计。
4. 切回 off 并重启，保留相同请求、模型/seed、并发、到达节奏与 cache，对 Vanilla/候选作重复配对。
5. 并发分别取 1 和实际服务负载（小型脚本可取 4），记录吞吐、P50/P95、事件循环延迟、CPU、RSS、queue/prefill/decode。
   串行变快不能代替并发验收；闭环负载也不代表生产到达分布。
6. 比较优化前/后代码时分别部署两份相同配置和算法，重放相同矩阵。DTB vs Vanilla 只检验预算策略收益，不能单独归因本次 Python 重构。
7. 完成目标数据质量评测后再晋级生产候选；采集体积和 tokenizer 离线分析也计入观测开销。

本次静态优化只减少规则准备/折叠/结构扫描和非流式前缀复制。引擎排队、重复 prefill、probe 成本可能占主导，
需要源级 CPU profiler 与请求级 phase 联合验证，不能用全局每 10 秒 token 吞吐率归因单请求。
final 方法提取不改变算法；history、prompt 所有权和 producer/consumer 并发边界保持。

## 变更记录

- 2026-10-21（交付范围确认后）：DEER 作为调试遗留退役，移除目录及 serving patch 的字段/导入/初始化/路由；手册更名为 dtb_performance_analysis.md。567 测试/1,980 子测试和三个 patch 检查通过；仍需服务器验收。部署从干净的固定上游应用当前三份 patch，不能覆盖旧 DEER patch 后直接重启；详见[升级步骤](../design/dtb_system_routing_deployment.md#deer-调试接口退役后的升级)。
- 2026-10-21：补齐[交付接手导航](../maintenance/README.md)与[vLLM 迁移手册](../maintenance/vllm_upgrade.md)，清理可从固定 Git 恢复的采集副本；冻结证据、回归输入与生产行为不变。静态门禁增加冻结报告哈希检查，临时输出迁至 artifacts/checks；550 测试/1,980 子测试通过。最终 DEER/SDK 分支裁剪见[交付梳理](../maintenance/delivery_review.md)。
- 2026-09-22：加入关键词有界缓存、局部文本折叠、饱和扫描、非流式 join 与 final 职责提取。
- 2026-09-23：合入 34f39983 system/native 路由；独立验收默认 legacy 与 system 候选。
- 2026-09-23：删除历史大数据/转发参考/LightCode，迁移冻结 fixtures；增加 vllm_gateway 与最小服务器矩阵，真实收益仍待测。
