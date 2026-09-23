# DTB / DEER 服务器验收 runbook

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

- 2026-09-22：加入关键词有界缓存、局部文本折叠、饱和扫描、非流式 join 与 final 职责提取。
- 2026-09-23：合入 34f39983 system/native 路由；独立验收默认 legacy 与 system 候选。
- 2026-09-23：删除历史大数据/转发参考/LightCode，迁移冻结 fixtures；增加 vllm_gateway 与最小服务器矩阵，真实收益仍待测。
