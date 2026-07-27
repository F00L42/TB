# run_02 端到端时延分析

## 1. 数据范围与证据边界

本轮使用 Qwen3.6-27B、TP=4、DP=1、4 并发运行 16 个固定样本。TB 服务
commit 为 `535aa57a992e9e5836033343268170fbc185b7d9`；固定环境与上游版本沿用
[`manifest.template.json`](../manifest.template.json)，本轮实例见
[`manifest.json`](manifest.json)。

请求参数为：

```json
{
  "max_tokens": 8192,
  "temperature": 0.0,
  "enable_think_budget": true,
  "fixed_prob_th": 0.90,
  "fixed_check_length": 1024,
  "fixed_think_stride": 512,
  "fixed_num_thinking": 4,
  "fixed_min_think_tokens": 100,
  "fixed_budget_tokens": 10000
}
```

客户端明细为
[`dtb_..._01-09-58.csv`](../../test_results/dtb_2026-02-11_ReAct白盒-整体规划_2026-07-27_01-09-58.csv)，
phase 账本为 [`controller_phases.jsonl`](controller_phases.jsonl)。

- **实测：** 98 条 phase 覆盖 16 个 parent，其中 82 次 engine call；phase
  parent 与客户端 `request_id` 完全对应。
- **实测：** phase wall 合计 `714.1891 s`，客户端 E2E 合计
  `716.1956 s`，差 `2.0065 s / 125.4 ms/请求`，phase 解释 E2E 的
  `99.7198%`。
- **实测：** before/after metrics 来自同一进程；82 次请求、370399 prompt
  token、33891 generated token、150528 cached token 与 phase 全部精确一致。
- **结构实测：** 16/16 客户端 `Judge` 为字面量 `True`，无客户端错误；
  该指标只校验 final JSON 结构，语义质量仍未评价。

## 2. 端到端结果

| 指标 | Vanilla 新基线（3 次均值） | run_01 | run_02 |
|---|---:|---:|---:|
| E2E 合计/次 | 1045.1766 s | 1087.6674 s | **716.1956 s** |
| E2E 平均/请求 | 65.3235 s | 67.9792 s | **44.7622 s** |
| E2E Q1 | 56.2285 s | 54.9850 s | **32.5439 s** |
| E2E 中位数 | 66.0511 s | 66.7248 s | **39.7866 s** |
| E2E P95 | 79.5103 s | 94.4605 s | **67.7917 s** |
| 可见 token 合计/次 | 72419.33 | 41268 | **32241** |
| 总模型 generated token | 72419.33 | 43674 | **33891** |
| final JSON 结构正确率 | 100% | 100% | 100% |

### 2.1 相对 Vanilla

- **三次聚合实测：** run_02 平均时延改善 `20.5613 s / 31.48%`，中位数
  改善 `39.76%`，P95 改善 `14.74%`。
- **三次聚合实测：** 可见 token 减少 `55.48%`；计入 1650 个 hidden
  probe token 后，总模型 generated token 仍减少约 `53.20%`。
- **配对实测：** 保存的 `09-27-57` Vanilla 明细与 run_02 的 QA_ID、问题、
  参考答案逐条一致；run_02 有 14/16 更快，paired delta 平均
  `-20.7944 s`、中位数 `-22.4341 s`。
- **尾部：** 两个配对退化请求为 `TC0000000211 (+20.87 s)` 和
  `TC0000000214 (+12.07 s)`；两者都在第 4 轮才接受。

### 2.2 相对 run_01

- **实测：** 总 E2E 改善 `371.4718 s / 34.15%`，13/16 个相同请求更快；
  paired delta 平均和中位数分别为 `-23.2170 s`、`-23.1950 s`。
- **实测：** 可见 token 减少 `9027 / 21.87%`，总模型 generated token
  减少 `9783 / 22.40%`，engine call 从 118 降到 82。
- **源码确定：** `903d8dc..535aa57` 的运行时代码改动只增加决策/置信度
  遥测、计算不参与决策的 Value-Min raw，并移除重复日志；average score 和
  接受分支语义没有改变。
- **证据边界：** run_01 未记录 sampling params、seed、warmup 和 cache 初态，
  因此不能把 34.15% 改善归因于 telemetry commit。run_02 必须先按完全相同
  的 `temperature=0 / max_tokens=8192 / cold cache` 再重复至少两次。

## 3. run_02 成本中心

| Phase | 调用 | wall | E2E 占比 | prefill | decode | generated token |
|---|---:|---:|---:|---:|---:|---:|
| decision | 16 | 0.088 s | 0.01% | — | — | 0 |
| initial_think | 16 | 321.385 s | **44.87%** | 16.500 s | 300.433 s | 16384 |
| iterative_think | 17 | 171.507 s | **23.95%** | 12.525 s | 156.818 s | 8704 |
| confidence_probe | 33 | 79.976 s | 11.17% | 32.914 s | 43.740 s | 1650 |
| final_answer | 16 | 141.232 s | 19.72% | 11.672 s | 127.848 s | 7153 |

- **实测：** initial + iterative thinking 为 `492.893 s / 68.82%`，仍是最大
  成本中心；final 为 `19.72%`，probe 为 `11.17%`。
- **实测：** engine decode、prefill、queue 分别占 E2E
  `87.80% / 10.28% / 0.44%`。decision 平均 `5.53 ms`，全部 controller
  CPU 仅约 `28.7 ms`，普通 Python 微优化没有可见收益。
- **实测：** 排除首次 initial 后，后续 prefill 合计 `57.11 s / 7.97%`；
  APC 的绝对优化上界小于阈值和思考预算空间。

### 3.1 run_01 到 run_02 的成本迁移

| Phase | run_01 调用 / wall / token | run_02 调用 / wall / token | wall 变化 |
|---|---:|---:|---:|
| initial_think | 16 / 355.318 s / 16384 | 16 / 321.385 s / 16384 | -9.55% |
| iterative_think | 37 / 476.926 s / 18439 | 17 / 171.507 s / 8704 | **-64.04%** |
| confidence_probe | 49 / 107.378 s / 2401 | 33 / 79.976 s / 1650 | -25.52% |
| final_answer | 16 / 145.975 s / 6450 | 16 / 141.232 s / 7153 | -3.25% |

**实测：** iterative wall 单项减少 `305.419 s`，占两轮 E2E 改善的约
`82.2%`。run_02 的主要变化是置信度更早接受，而不是 controller CPU、APC
或 final 变快。

## 4. 置信度轨迹

| 指标 | 数值 |
|---|---:|
| probe 数 / hidden token | 33 / 1650 |
| 接受轮次 1 / 2 / 3 / 4 | 8 / 2 / 3 / 3 |
| 未接受请求 | 0 |
| average score min / P50 / P95 / max | 0.8363 / 0.8972 / 0.9656 / 0.9736 |
| accepted score min / P50 / max | 0.9022 / 0.9127 / 0.9736 |
| Value pair 范围 | 31–40 / probe |

- **实测：** 所有 33 个 probe 都使用 `average_fallback`；16 个请求全部在
  第 4 轮以内接受，没有 fail-open 或最小思考预算拒绝。
- **实测：** 若所有请求都走满 `3072` think token，需要 49152 visible
  thinking token；本轮实际为 25088。1650 hidden probe token 换回了 24064
  visible thinking token，净少 22414 token，不能直接取消 probe。
- **实测反事实：** 将 raw Value-Min 转成概率后，16 个实际接受 probe 中只有
  2 个达到 `0.90`。启用 Value-Min 很可能显著增加轮次和尾延迟；在获得语义
  收益证据前应继续保持关闭。

### 4.1 阈值离线回放

以下只从已观测路径扣除更早达到阈值后的 iterative/probe phase；更早 final
的长度、内容和真实调度未知，不能当作已测收益。

| 阈值 | 提前改变请求 | 静态少走 phase wall | 少生成 visible think | 少生成后续 probe |
|---:|---:|---:|---:|---:|
| 0.89 | 5 | 95.24 s | 4096 | 400 |
| 0.88 | 5 | 120.09 s | 5120 | 500 |
| 0.875 | 5 | 132.43 s | 5632 | 550 |
| 0.85 | 7 | 182.25 s | 7680 | 750 |

**假设：** `0.89` 是下一轮风险最低的阈值变体；静态路径上限约
`5.95 s/请求`。`0.88/0.875` 的静态空间更大，但必须通过语义质量门；
`0.85` 继续只作为探索下界。

## 5. BudgetPlan 与分类遥测

- **实测：** 16/16 的 effective branch 均为 `fixed`，四个预算字段、
  `confidence_threshold=0.9` 和 `hard_think_token_limit=8192` 均与请求参数
  一致，字段来源完整。
- **实测：** task type 却为 16/16 `classification`，而 scene type 为
  `planning=11 / fault_diagnosis=4 / code_generation=1`。
- **源码确定：** 当前分类器扫描完整长 prompt；任务说明中的 “classify
  actions” 等固定文字会命中 classification 指示词。因为本轮走 fixed branch，
  该误分类不影响有效预算；一旦切换 task/difficulty/history branch，就可能
  错用约 800-token 的 classification 预算。

**方案：** 动态预算实验前，先让 task classifier 只读取真实任务/问题区段，
或过滤固定系统说明，并增加这 16 个样本的分类黄金测试；不要直接关闭 fixed
overrides 来测试动态预算。

## 6. 优化优先级

### P0：先确认 run_02 可重复，并建立语义质量门

1. 完全复用本轮 payload、到达顺序、并发 4、cold cache，重复至少 2 次；
2. 每次保存客户端明细、metrics、phase 和 manifest；
3. 除 JSON 结构外，评分事实一致性、已执行步骤识别、后续计划可执行性和工具
   参数正确性，并预先声明非劣界。

### P1：先做阈值单变量

按以下顺序，每个变体至少 3 个随机交错重复：

```text
T0 = prob_th 0.90
T1 = prob_th 0.89
T2 = prob_th 0.88
T3 = prob_th 0.875
T4 = prob_th 0.85（探索）
```

先比较 T1 与 T0；只有语义非劣时才继续降低。

### P1：再做可见思考预算单变量

保持阈值 `0.90` 分别测试：

```text
B1 = fixed_check_length 512
B2 = fixed_think_stride 256
B3 = fixed_num_thinking 3
```

- **静态上界：** initial 减半对应 8192 token，按本轮 initial decode/token
  估算约 `150.2 s` decode；真实接受轨迹会变化。
- **静态上界：** stride 减半对应 4352 token，按本轮 iterative
  decode/token 估算约 `78.4 s` decode。
- **实测轨迹反事实：** B3 只会影响 3 个第 4 轮请求；若三者保持相同 final
  prompt，最多省 3 个 round-4 probe，即 `5.95 s / 0.83% E2E`，优先级低于
  check/stride。

这些静态值不可相加；先选出单变量语义非劣版本，再组合。

### P2：APC / resumable 与 backend 警告

- 后续 prefill 上界只有 `7.97%`，且本轮 phase cached token 与 Prometheus
  已精确一致；APC 不是当前第一优化项。
- server log 在首批请求只出现每个 TP rank 一次的 int32/int64 ArgSort
  AiCPU fallback 警告。尚无 profiler 证据证明它持续占用显著时间；只有在
  threshold/budget 收敛后，才对 confidence logprob 路径做算子 profiling。

### 暂不实施

- 不启用 Value-Min；本轮反事实显示会大幅收紧接受。
- 不缩短 final；16/16 均自然 stop 且结构正确，语义质量尚未评分。
- 不优先优化 Python、JSON、controller decision 或 scheduler queue。
- 不重新分析 MTP；本轮没有回归证据。

