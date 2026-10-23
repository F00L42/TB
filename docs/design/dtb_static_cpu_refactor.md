# DTB 静态 CPU 优化

实现基线 `f342e127`，优化提交 `5360067a`；随后合入 system 路由 `34f39983`。
当前交付与验证入口见 [RESULT](../../RESULT.md)。优化保持算法执行逻辑。

| 位置 | 优化 | 保持的契约 |
| --- | --- | --- |
| `scene_router._compile_keyword` | 最多 2048 条不可变规则缓存；一次 classify 只折叠一次文本 | 原 regex/casefold 差异、Unicode 边界、评分/排序/拒识、重复关键词 |
| `scene_router` 结构规则 | 存在性用 search；分数饱和后停止 finditer | 返回完整证据字典与顺序不变 |
| `response_adapter.aggregate_queue` | 收集文本片段并 join 一次 | 首对象复用、token/finish 顺序、错误与取消、原生 full parser |
| `_FinalAnswerState` | 提取边界准备、额度计算、流消费 | cue、generate 参数、child 顺序、ledger、terminal/EOF/取消 |

缓存只持有配置关键词，不持有请求正文、分类结果或 tokenizer；淘汰只重复等价规则构造。
并发请求的折叠文本、匹配列表、prompt 与 ledger 均局部。system_prefix 走启动编译规则，
其性能不应归于 legacy 关键词微基准。方法提取主要改善可读性，不宣称它单独提速。

非流式 join 避免逐块复制增长中的前缀，代价是保留 O(chunk 数) 个片段引用到聚合完成。
实际峰值内存、事件循环延迟、吞吐仍须在目标并发/输出长度下实测。
producer/consumer 队列、prompt 所有权复制、telemetry/history 策略、难度算法均保持。

```bash
python tools/dtb/check_static.py --benchmark
```

微基准读取 Git 中旧实现，比较合成局部操作及 633 条冻结输入的**完整路由字典**。
结果在 `.pytest_cache/dtb-static-cpu.json`，只代表本机 Python 操作成本，不能换算模型 E2E。
冻结问题位于 `tests/fixtures/scenes/`；来源/哈希见 [fixture 清单](../../tests/fixtures/provenance.json)。
服务器验收见 [runbook](../debug/dtb_performance_analysis.md)。
