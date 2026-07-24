# run_01 端到端时延分析

## 1. 数据范围与证据边界

本轮使用 Qwen3.6-27B、TP=4、DP=1、4 并发运行 16 个固定 QA_ID。客户端
明细为
[`dtb_..._06-41-01.csv`](../../test_results/dtb_2026-02-11_ReAct白盒-整体规划_2026-07-24_06-41-01.csv)，
聚合对照来自 [`_SUMMARY.csv`](../../test_results/_SUMMARY.csv)，控制器账本为
[`controller_phases.jsonl`](controller_phases.jsonl)。

请求级固定参数为：

```json
{
  "enable_think_budget": true,
  "fixed_prob_th": 0.90,
  "fixed_check_length": 1024,
  "fixed_think_stride": 512,
  "fixed_num_thinking": 4,
  "fixed_min_think_tokens": 100,
  "fixed_budget_tokens": 10000
}
```

服务有效配置由操作者确认为 `enable_value_min_confidence=false`。版本与配置
可复现性缺口见 [`manifest.json`](manifest.json)：声明的服务 commit
`ec79f53f` 中该默认值仍是 `true`，且本轮没有保存运行时配置文件 hash。

- **实测：** 134 条 phase 覆盖 16 个 parent，其中 118 次 engine call；
  phase parent 与客户端 `request_id` 完全对应。
- **实测：** phase wall 合计 `1085.6846 s`，客户端 E2E 合计
  `1087.6674 s`，差 `1.9828 s`，即 `123.9 ms/请求`；phase 解释
  `99.8177%` 的客户端时延。
- **实测：** before/after metrics 来自同一进程，118 次请求、
  555357 prompt token 和 43674 generated token 与 phase 完全一致。
- **采集缺口：** phase 的 cached token 为 245760，Prometheus
  `prefix_cache_hits_total` 增量为 247296，相差 1536；APC 结论使用
  phase 分解时必须保留这一差异。
- **质量边界：** 16/16 最终输出都能解析为 JSON，并包含相同的五个必需
  顶层字段；没有语义正确率或人工评分，不能据此声明质量非劣。

## 2. 端到端结果

| 指标 | Vanilla | run_00 | run_01 |
|---|---:|---:|---:|
| E2E 合计 | 1118.5239 s | 2964.2027 s | **1087.6674 s** |
| E2E 平均 | 69.9077 s | 185.2627 s | **67.9792 s** |
| E2E 中位数 | 69.9773 s | 179.8645 s | **66.7248 s** |
| E2E P95 | **84.0568 s** | 269.2437 s | 94.4605 s |
| 可见 token 合计 | 74534 | 39466 | 41268 |

### 2.1 相对 run_00

- **实测：** E2E 合计下降 `1876.5353 s / 63.31%`；15/16 个相同
  QA_ID 更快，paired delta 中位数为 `-108.67 s`。
- **实测：** 唯一变慢的是 `TC0000000203`，增加 `0.91 s`；它的可见
  token 从 1187 增至 3517，说明新固定思考预算会对部分原本较短的样本
  产生过度思考。
- **实测：** 总 engine generated token 从 114337 降到 43674，
  减少 `61.80%`。

### 2.2 相对 Vanilla

- **聚合实测：** run_01 平均和中位数分别改善 `2.76%`、`4.65%`，
  但 P95 恶化 `12.38%`（`+10.40 s`）。
- **聚合实测：** 可见 token 减少 `44.63%`，但平均时延只改善
  `2.76%`，说明多阶段生成、较差的阶段化 decode 效率和重复 prefill
  吃掉了大部分 token 收益。
- **证据边界：** 当前工作区没有 vanilla 客户端明细与 phase 账本，无法做
  run_01 对 vanilla 的逐请求配对或严格拆分 batching/TPOT 差异。

当前版本已经达到“平均值略优于 vanilla”，但还没有达到“尾延迟不退化”，
也没有完成语义质量非劣验证。

## 3. run_01 成本中心

| Phase | 调用 | wall | E2E 占比 | prefill | decode | generated token |
|---|---:|---:|---:|---:|---:|---:|
| decision | 16 | 0.088 s | 0.01% | — | — | 0 |
| initial_think | 16 | 355.318 s | 32.67% | 17.502 s | 332.253 s | 16384 |
| iterative_think | 37 | 476.926 s | **43.85%** | 27.502 s | 446.260 s | 18439 |
| confidence_probe | 49 | 107.378 s | 9.87% | 44.254 s | 58.108 s | 2401 |
| final_answer | 16 | 145.975 s | 13.42% | 12.141 s | 132.631 s | 6450 |

- **实测：** initial + iterative thinking 合计 `832.244 s`，占客户端
  E2E `76.52%`；这是当前首要优化对象。
- **实测：** engine decode、prefill、queue 分别占 E2E
  `89.11% / 9.32% / 0.26%`。phase 内其余时间与客户端未解释差值合计
  只有约 `14.08 s / 1.29%`。
- **实测：** decision 平均 `5.47 ms/请求`。继续优化分类、预算计算或
  Python 几何平均不会产生可见 E2E 收益。

### 3.1 重构前后成本迁移

| 项目 | run_00 | run_01 | 变化 |
|---|---:|---:|---:|
| visible thinking token | 12278 | 34823 | **+183.62%** |
| hidden probe token | 74871 | 2401 | **-96.79%** |
| final-answer token | 27188 | 6450 | **-76.28%** |
| probe wall | 1922.167 s | 107.378 s | **-94.41%** |
| thinking wall | 375.783 s | 832.244 s | **+121.47%** |
| final wall | 664.052 s | 145.975 s | **-78.02%** |

**实测：** 固定 50-token probe 和新状态机已经解决 run_00 的主要性能
故障；成本中心从隐藏长 probe 转移为固定 `1024 + N×512` 的可见思考。

**实测：** run_00 只有 7/16 个最终输出可解析为目标 JSON，final phase
有 8 次 length stop；run_01 为 16/16 可解析且 16 次 final 全部自然 stop。
这支持结构完整性改善，但不能替代语义质量评分。

## 4. 置信度门是否值得保留

49 个 probe 的 score 分布为：

| 指标 | 数值 |
|---|---:|
| min / P25 / P50 / P75 / P95 / max | 0.0674 / 0.8285 / 0.8692 / 0.8959 / 0.9275 / 0.9395 |
| 接受轮次 1 / 2 / 3 / 4 | 2 / 3 / 3 / 4 个请求 |
| 4 轮仍未接受 | 4 个请求 |

- **实测：** 12 次 accepted 全部满足 `score >= 0.90`，没有低于阈值
  接受或高于阈值拒绝。
- **实测：** probe 生成 2401 hidden token，但早停使 visible thinking 从
  “所有请求都走满 3072 token”的 49152 降到 34823，少生成 14329
  visible-think token。扣除 probe 后，净少生成 11928 token。
- **结论：** 当前 50-token probe 已经有正的模型工作量收益。直接取消或
  大幅降低 probe 频率不是首选；长 probe 已不是 run_01 的问题。
- **源码确定：** `fixed_num_thinking=4` 表示最多 4 次 probe；全部失败时
  状态机会生成最后一个 512-token stride 再 final。因此本轮有效最大思考量
  是 `1024 + 4×512 = 3072`，`fixed_budget_tokens=10000` 实际不绑定。

### 4.1 阈值离线回放

以下只是在既有置信度轨迹上，把首次达到新阈值之后的已观测 think/probe
phase 静态扣除；较早 final 的长度、质量和真实时延未知，不能当成已测收益。

| 阈值 | 会提前改变的请求 | 静态少走 phase wall | 少生成 visible think | 少生成后续 probe |
|---:|---:|---:|---:|---:|
| 0.88 | 4 | 125.77 s | 4096 | 350 |
| 0.875 | 5 | 131.34 s | 4280 | 350 |
| 0.85 | 11 | 393.90 s | 13319 | 1150 |

**假设：** `0.88` 或 `0.875` 可能在较小质量风险下回收约
`7.9–8.2 s/请求` 的静态路径成本。证伪方法是保持其他参数不变做 paired
质量/E2E 实验；`0.85` 只作为探索性下界，不应直接设为生产默认值。

## 5. 优化优先级

### P0：先建立语义质量门与严格 Vanilla 基线

当前只能证明结构完整，不能证明诊断规划质量。下一轮必须：

1. 保存相同 QA_ID 的 vanilla 明细和 phase/metrics；
2. 固定 sampling params、seed、到达顺序、warmup 和 prefix-cache 初态；
3. 对最终 `</think>` 后的 JSON 评分：可解析率、五字段完整率、事实一致性、
   已执行步骤识别、后续计划可执行性和工具参数正确性；
4. 至少 3 个随机化重复，先看 paired delta，再看平均/P50/P95；
5. 预先声明非劣界，任何时延变体未过质量门都不能合入。

### P1：单变量校准置信度阈值

保持 `1024 / 512 / 4 / 50-token probe` 不变，依次测试：

```text
T0: prob_th = 0.90
T1: prob_th = 0.88
T2: prob_th = 0.875
T3: prob_th = 0.85（探索性）
```

`T1/T2` 是当前最容易实施且日志显示有直接空间的变体。每个变体报告
accept round、hidden token、visible thinking、final token、paired E2E 和质量。

### P1：单变量缩减可见思考预算

在阈值控制组上分别测试，暂不做全因子组合：

```text
B1: fixed_check_length 1024 -> 512
B2: fixed_think_stride 512 -> 256
B3: fixed_num_thinking 4 -> 3
```

- **静态上界：** 首段减半最多少 8192 token；按本轮 initial decode/token
  估算约 166 s decode，但接受轨迹会变化。
- **静态估计：** stride 减半对应约 9220 个已观测 iterative token，
  按本轮 iterative decode/token 估算约 223 s decode；该数字不可与阈值
  回放直接相加。
- **实测轨迹反事实：** `num_thinking=3` 会使到达第四轮的 8 个请求少走
  8 次 round-4 probe，并使其中 4 个未接受请求少走 terminal stride；
  已观测 wall 合计 57.69 s，占 E2E 5.30%。最终答案从更短思考开始，必须
  做质量验证。

先选出通过质量门的单变量，再组合“最佳阈值 + 最佳 budget”，避免无法归因。

### P2：保持语义的 APC / resumable 优化

- **实测：** prefill 合计 101.40 s，占 E2E 9.32%；扣除不可避免的首次
  initial prefill 后，所有后续 prefill 的绝对上界是 83.90 s / 7.71%。
- **实测：** iterative/probe/final 的 phase cache 比例分别为
  53.28% / 42.35% / 57.12%，cached token 已出现
  1536、3072、4608，说明增长前缀在 run_01 中得到部分复用。
- **方案：** 先解决 phase 与 Prometheus cached token 相差 1536 的口径；
  再用并发 1 与并发 4 对照，检查同一 parent 的 token prefix、cache salt、
  DP rank 和 block 保留。若增长前缀仍有稳定缺口，再在 DEER 验证“主思考
  sequence resumable、probe 仍独立分支”的方案。
- **边界：** 不能为了复用 KV 改变 token 序列、采样状态、取消语义或原生
  priority/LoRA/trace/DP 上下文。

### P3：暂缓普通 Python、队列和日志微优化

queue 只有 0.26%，decision 只有 0.01%，phase residual 加客户端 gap 的
理论上界约 1.29%。除非 CPU profiler 给出新证据，不应优先改
JSON 序列化、对象复制、controller arithmetic 或 scheduler 参数。

### 不建议当前实施

- 不建议把 50-token trial 直接作为 final：48/49 个 probe 都以 length
  结束，最终答案平均约 403 token，trial 不是完整交付候选。
- 不建议继续缩短 probe 作为第一变量：即使把 probe decode 全部消除，上界
  也只有 58.11 s，且会破坏已经证实的早停收益。
- 不建议进一步压缩 final：16/16 都自然 stop 且结构完整；语义评分前继续
  限制 final 的风险高于可证明收益。
- 不重新分析 MTP；本轮没有回归证据。

## 6. 下一轮采集补充

除现有 schema 1 phase 外，建议持久化：

- effective `BudgetPlan`、decision branch、task type 与各字段来源；
- `confidence_method`、average confidence、Value-Min raw、pair/value-pair
  count、`accept_reason`；
- 运行时 `budget_config.yaml` 与 `unified_config.py` SHA256、三个仓库 dirty；
- final JSON 结构分和语义质量分；
- vanilla 的同口径 phase 账本，用于验证阶段化生成是否导致 batching/TPOT
  损失。
