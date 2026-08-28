# Vanilla 基线思维链分析报告

> 数据来源: `results/qwen/local-task29/local-apiserver/vanilla/run-02`
> 评测任务: 20 tasks (LightCode concurrent_eval.py)
> 代理记录: 51 sessions, 749 管线 turns
> 分析日期: 2026-08-27

## 0. 结论

**DTB 优化空间显著但分布极不均匀**：剔除 LightCode 内部请求后，代码生成管线共 749 轮交互、105K reasoning tokens，其中：

1. **60% 的轮次 reasoning ≤50 tokens**（已经很短，压缩无意义），仅消耗 9.9% 的 reasoning tokens
2. **0.9% 的轮次（7 turns）reasoning >1000 tokens**，消耗 22% 的 reasoning tokens — 这是 DTB 的核心压缩目标
3. **长 thinking 轮次与特定工具强相关**：`read` 调用平均 reasoning 1964t，`edit` 861t，`write` 806t，远高于 `bash`(422t) 和短操作
4. **E2E 与 reasoning 强相关**（session 级 r=0.864），线性模型 `E2E ≈ 0.077 × reasoning + 20.2s`
5. **推荐起始参数 dtb_tight (budget=850)**：仅影响 7 个极长 thinking 轮次，节省 16.5% reasoning tokens，预估 E2E 节省 ~10.5%

## 1. 口径定义

### 1.1 任务与 Session 对应关系

LightCode 提交了 20 个评测任务，但代理只捕获了 14 个 skill 启动 session（对应 11 个唯一分支 + 3 次重试）：

| 类别 | 数量 | 说明 |
|---|---|---|
| evaluation_results.json 总任务 | 20 | 16 FINISHED + 2 FAILED + 1 TIMEOUT + 1 ROUND_COMPLETED |
| 代理记录的 skill sessions | 14 | 对应 11 个唯一分支（3 个分支有重试） |
| 代理未捕获的任务 | 9 | 未走到 skill 加载步骤 |

**9 个未捕获任务的原因**：

| 任务 ID | 状态 | 未捕获原因 |
|---|---|---|
| task_69335a195579444f | FAILED | OpenCode 启动失败，日志时间 10:40（代理 16:37 才启动） |
| task_281b6ac865054d4e | TIMEOUT | 无 session_logs，任务超时未启动 Agent |
| task_facc7e4736f54041 | FAILED | OpenCode 启动失败，日志时间 16:22（代理 16:37 才启动） |
| 其余 6 个 | FINISHED | 这些任务虽然 FINISHED，但 Agent 的请求未经过代理（可能使用不同的 OpenCode 实例或直连 vLLM） |

另外，`task_a2135c1bd3ac481f_r1` 有 eval report 但不在当前 evaluation_results.json 中，属于前一次运行的残留数据。

### 1.2 剔除 LightCode 内部请求

LightCode 评测框架会产生 4 类 session，其中 "Generate a title" 是纯内部请求，不参与代码生成：

| 类别 | Sessions | Turns | Reasoning | 说明 |
|---|---|---|---|---|
| **代码生成** (code_gen) | 14 | 417 | 58,472t (55.4%) | 实际代码编写/修改 |
| **DT设计文档** (lc_dt_design) | 13 | 205 | 31,516t (29.9%) | Diff 分析 → JSON 设计文档 |
| **Skill启动** (lc_skill_launch) | 14 | 127 | 15,473t (14.7%) | 加载 skill + 工作流编排 |
| ~~Title生成~~ (lc_title) | ~~10~~ | ~~97~~ | ~~28,915t~~ | **剔除**：LightCode 内部请求 |

剔除后统计口径：**749 turns, 105,461 reasoning tokens**。

### 1.3 逻辑任务分组

11 个唯一分支按时间排序（含 3 个重试）：

| 任务# | Sessions | Turns | Reasoning | E2E | 分支 | 管线步骤 |
|---|---|---|---|---|---|---|
| #1 | 3 | 55 | 3,147t | 28min | 30_EnvCmnService_e914d8a9 | Skill→DT设计→代码生成 |
| #2 | 2 | 40 | 8,032t | 45min | 18_SecoMPoliciesCBB_dad2b381 | Skill→DT设计 |
| #3 | 5 | 84 | 8,894t | 38min | 29_ExperienceAssuranceService | Skill→DT设计→代码生成→Title→代码生成 |
| #4 | 3 | 26 | 7,745t | 11min | 32_EnvCmnService_32e5abfe | Skill→DT设计→Title |
| #5 | 10 | 142 | 16,269t | 47min | 26_ExperienceAssuranceService | 多轮 Skill↔DT设计↔代码生成 |
| #6 | 3 | 57 | 9,792t | 49min | 5_DataSmartCubeService_b54e4f17 | Skill→Title→DT设计 |
| #7 | 3 | 63 | 9,656t | 26min | 20_SecoMPoliciesCBB_ed04d664 (第1次) | Skill→DT设计→代码生成 |
| #8 | 4 | 146 | 42,159t | 90min | 20_SecoMPoliciesCBB_ed04d664 (第2次) | Skill→Title→代码生成→Title |
| #9 | 8 | 111 | 15,310t | 52min | 34_DVAIAgentService_6f4c4230 | Skill→DT设计→代码生成→DT设计→... |
| #10 | 2 | 28 | 1,588t | 8min | 37_DVAIAgentService_11718ad6 | Skill→代码生成 |
| #11a | 2 | 20 | 1,779t | 10min | 4_DataSmartCubeService_63efcc07 (第1次) | Skill→DT设计 |
| #11b | 2 | 27 | 4,631t | 10min | 4_DataSmartCubeService_63efcc07 (第2次) | Skill→代码生成 |
| #11c | 2 | 24 | 1,872t | 9min | 4_DataSmartCubeService_63efcc07 (第3次) | Skill→代码生成 |
| #12 | 2 | 23 | 3,502t | 7min | 33_EnvCmnService_c374b544 | Skill→DT设计 |

## 2. 思维链 Token 分布

### 2.1 整体分布

| 百分位 | Reasoning Tokens |
|---|---|
| P10 | 12 |
| P25 | 18 |
| P50 (中位数) | 35 |
| P75 | 124 |
| P90 | 367 |
| P95 | 542 |
| P99 | 932 |
| Max | 10,139 |
| Mean | 141 |

### 2.2 分桶统计

| Reasoning 区间 | Turns | 占比 | Reasoning Tokens | 占比 | 累计 |
|---|---|---|---|---|---|
| 1-10 | 47 | 6.3% | 402 | 0.4% | 0.4% |
| 11-20 | 176 | 23.5% | 2,745 | 2.6% | 3.0% |
| 21-50 | 227 | 30.3% | 7,319 | 6.9% | 9.9% |
| 51-100 | 91 | 12.1% | 6,587 | 6.2% | 16.2% |
| 101-200 | 70 | 9.3% | 10,478 | 9.9% | 26.1% |
| **201-500** | **91** | **12.1%** | **29,270** | **27.8%** | **53.8%** |
| **501-1000** | **39** | **5.2%** | **25,459** | **24.1%** | **78.0%** |
| 1001-2000 | 4 | 0.5% | 5,826 | 5.5% | 83.5% |
| 2001-5000 | 1 | 0.1% | 2,097 | 2.0% | 85.5% |
| 5001+ | 2 | 0.3% | 15,278 | 14.5% | 100% |

**核心发现**：分布极度偏斜。60% 的轮次 reasoning ≤50t（消耗 <10% tokens），而 17.4% 的轮次（201-1kt）消耗 51.9% 的 reasoning tokens。

### 2.3 累计占比分析

| 占总 Reasoning% | 所需最少 Turns | 占总 Turns% |
|---|---|---|
| 10% | 2 | 0.3% |
| 30% | 18 | 2.4% |
| 50% | 55 | 7.3% |
| 80% | 174 | 23.2% |
| 95% | 437 | 58.3% |

**2 个轮次贡献 10% 的 reasoning tokens，55 个轮次贡献 50%。** 压缩这些少数长 thinking 轮次是 DTB 的核心优化路径。

### 2.4 长思维链轮次明细（reasoning >500 tokens）

以下列出所有 reasoning >500 tokens 的轮次，按 reasoning 降序排列：

| # | Session | Turn | Reasoning | Content | Ratio | E2E | Prompt | Type | Tool | Step |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 7009df0f9307 | 76 | **10,139** | 22 | 1.00 | 780.5s | 157,436 | tool_result | read | 代码生成 |
| 2 | 7009df0f9307 | 80 | **5,139** | 15 | 1.00 | 446.6s | 171,750 | tool_result | edit | 代码生成 |
| 3 | 8155c36fcc64 | 17 | **2,097** | 16 | 0.99 | 199.3s | 60,282 | tool_result | write | 代码生成 |
| 4 | ed39ae85e614 | 1 | **1,762** | 10 | 0.99 | 112.9s | 104,957 | tool_result | bash | Skill启动 |
| 5 | 4a092f0fa0ac | 31 | **1,548** | 1 | 1.00 | 155.5s | 123,913 | tool_result | edit | 代码生成 |
| 6 | 80e079b2325c | 6 | **1,504** | 1 | 1.00 | 97.9s | 103,252 | tool_result | bash | DT设计文档 |
| 7 | 7009df0f9307 | 71 | **1,012** | 1 | 1.00 | 82.1s | 150,544 | tool_result | read | 代码生成 |
| 8 | 7009df0f9307 | 77 | 932 | 1 | 1.00 | 105.7s | 168,543 | tool_result | edit | 代码生成 |
| 9 | 8fe329386b89 | 8 | 904 | 16 | 0.98 | 57.7s | 56,724 | tool_result | todowrite | Skill启动 |
| 10 | 7009df0f9307 | 95 | 853 | 19 | 0.98 | 106.8s | 186,769 | tool_result | edit | 代码生成 |
| 11 | 7009df0f9307 | 88 | 830 | 13 | 0.98 | 90.2s | 181,098 | tool_result | edit | 代码生成 |
| 12 | 7009df0f9307 | 11 | 808 | 15 | 0.98 | 53.9s | 102,188 | tool_result | bash | 代码生成 |
| 13 | 7009df0f9307 | 85 | 803 | 1 | 1.00 | 87.0s | 179,330 | tool_result | edit | 代码生成 |
| 14 | 7009df0f9307 | 13 | 776 | 22 | 0.97 | 52.4s | 103,544 | tool_result | read | 代码生成 |
| 15 | c896b240955a | 26 | 744 | 59 | 0.93 | 52.0s | 103,182 | tool_result | bash | 代码生成 |
| 16 | e0670d3c1e55 | 9 | 741 | 60 | 0.93 | 64.8s | 79,938 | tool_result | write | 代码生成 |
| 17 | 8ad9d9f2a89b | 0 | 714 | 2164 | 0.25 | 127.9s | 43,700 | initial | - | DT设计文档 |
| 18 | 7009df0f9307 | 73 | 710 | 18 | 0.98 | 122.6s | 153,355 | tool_result | edit | 代码生成 |
| 19 | 7009df0f9307 | 74 | 704 | 10 | 0.99 | 60.8s | 155,017 | tool_result | bash | 代码生成 |
| 20 | 80e079b2325c | 2 | 693 | 22 | 0.97 | 50.8s | 80,062 | tool_result | bash | DT设计文档 |
| 21 | 4a092f0fa0ac | 29 | 689 | 60 | 0.92 | 94.7s | 116,243 | tool_result | edit | 代码生成 |
| 22 | 23a5850a7205 | 7 | 688 | 15 | 0.98 | 71.4s | 55,595 | tool_result | task | Skill启动 |
| 23 | 8e4419eef9e3 | 28 | 686 | 12 | 0.98 | 85.7s | 185,815 | tool_result | todowrite | DT设计文档 |
| 24 | 80e079b2325c | 5 | 649 | 1 | 1.00 | 47.2s | 95,911 | tool_result | bash,read | DT设计文档 |
| 25 | 80e079b2325c | 8 | 646 | 73 | 0.90 | 51.2s | 105,410 | tool_result | bash | DT设计文档 |
| 26 | 318604f50c64 | 13 | 642 | 18 | 0.97 | 60.2s | 111,443 | tool_result | todowrite | DT设计文档 |
| 27 | 80e079b2325c | 11 | 637 | 43 | 0.94 | 51.4s | 119,935 | tool_result | bash | DT设计文档 |
| 28 | c78f878e760a | 0 | 627 | 1155 | 0.35 | 80.4s | 41,120 | initial | - | DT设计文档 |
| 29 | 7009df0f9307 | 10 | 621 | 11 | 0.98 | 44.0s | 100,622 | tool_result | read | 代码生成 |
| 30 | 8e4419eef9e3 | 18 | 610 | 1 | 1.00 | 52.3s | 150,217 | tool_result | bash | DT设计文档 |
| 31 | 8ad9d9f2a89b | 7 | 606 | 1 | 1.00 | 41.4s | 88,684 | tool_result | bash,read | DT设计文档 |
| 32 | 61b8cd94ab42 | 19 | 603 | 42 | 0.93 | 44.2s | 106,874 | tool_result | bash | 代码生成 |
| 33 | 7009df0f9307 | 14 | 599 | 1 | 1.00 | 102.6s | 105,842 | tool_result | edit | 代码生成 |
| 34 | 4a092f0fa0ac | 7 | 568 | 54 | 0.91 | 35.6s | 87,639 | tool_result | bash | 代码生成 |
| 35 | ed39ae85e614 | 2 | 542 | 1 | 1.00 | 33.4s | 108,525 | tool_result | bash,read | Skill启动 |
| 36 | 8155c36fcc64 | 5 | 537 | 1 | 1.00 | 35.3s | 58,091 | tool_result | bash | 代码生成 |
| 37 | 80e079b2325c | 14 | 527 | 1 | 1.00 | 33.7s | 127,735 | tool_result | todowrite | DT设计文档 |
| 38 | 8155c36fcc64 | 3 | 527 | 2 | 1.00 | 30.5s | 51,856 | tool_result | bash,glob | 代码生成 |
| 39 | c76cfb3cd30f | 9 | 519 | 1 | 1.00 | 32.1s | 76,828 | tool_result | bash | 代码生成 |
| 40 | 80e079b2325c | 16 | 519 | 1 | 1.00 | 40.1s | 135,325 | tool_result | bash,glob,grep | DT设计文档 |

**关键观察**：

- **session 7009df0f9307（分支 20_SecoMPoliciesCBB，第2次重试）** 是最极端的案例：102 轮中 12 轮 reasoning >500t，单轮最高 10,139t（read 工具后深度分析），该 session 贡献 31,239t reasoning（占总量的 29.6%）
- **前 2 个轮次**（10,139t + 5,139t = 15,278t）贡献了 **14.5% 的总 reasoning tokens**，仅占 0.3% 的轮次数
- **reasoning >1000t 的 7 个轮次**全部来自代码生成和 Skill 启动步骤，其中 5 个集中在 session 7009df0f9307

## 3. 按管线步骤分析

| 步骤 | Turns | Reasoning | 占比 | Avg | Median | Max |
|---|---|---|---|---|---|---|
| Skill启动 | 127 | 15,473t | 14.7% | 122 | 35 | 1,762 |
| DT设计文档 | 205 | 31,516t | 29.9% | 154 | 53 | 1,504 |
| 代码生成 | 417 | 58,472t | 55.4% | 140 | 28 | 10,139 |

- **代码生成步骤**消耗最多 reasoning tokens（55.4%），且分布最偏斜（median=28, max=10,139）
- **DT设计文档步骤** reasoning 中位数最高（53），因为 Diff 分析需要深度推理
- **Skill启动步骤** reasoning 总量最少，但单轮可到 1,762t（复杂工作流编排）

## 4. 长思维链与工具调用的关系

仅分析代码生成步骤中 reasoning >200 tokens 的 57 个轮次：

| 工具 | Turns | Avg Reasoning | Median | Max | 说明 |
|---|---|---|---|---|---|
| **read** | 7 | **1,964** | 621 | 10,139 | 读取源码后的深度分析 |
| **edit** | 17 | **861** | 599 | 5,139 | 编辑前的代码规划 |
| **write** | 5 | **806** | 470 | 2,097 | 文件写入前的设计 |
| bash | 24 | 422 | 391 | 808 | 命令执行前的决策 |
| no_tools | 3 | 237 | 218 | 283 | 纯推理无工具调用 |
| glob/grep | 3 | 340 | 240 | 450 | 文件搜索 |

**关键发现**：

1. **`read` 工具调用的 reasoning 最长**（avg 1,964t），因为 Agent 读取源码后需要进行深度代码理解、逻辑推理和修改规划
2. **`edit` 和 `write` 的 reasoning 也很长**（avg 800-860t），因为修改代码前需要仔细分析上下文和设计修改方案
3. **`bash` 的 reasoning 相对较短**（avg 422t），因为命令构造的推理复杂度较低
4. **最长的 2 个 reasoning（10,139t 和 5,139t）都是 `read`→`edit` 组合**，发生在 session 7009df0f9307 的 turn 76/80：先读取大量源码，再规划复杂编辑

## 5. E2E 时延分析

### 5.1 Reasoning 与 E2E 的关系

| 指标 | 值 |
|---|---|
| Turn 级相关系数 | r = 0.576 |
| Session 级相关系数 | **r = 0.864** |
| 线性模型 | E2E ≈ 0.077 × reasoning + 20.2s |
| 解码速度 | ~13 reasoning tokens/s |
| 基础延迟 (prefill) | 20.2s |

### 5.2 Reasoning 分桶与 E2E

| Reasoning 区间 | Turns | Avg E2E | 累计 E2E |
|---|---|---|---|
| ≤50t | 451 | 19.5s | 8,812s (37.9%) |
| 50-200t | 161 | 30.3s | 4,872s (21.0%) |
| 200-1kt | 130 | 59.0s | 7,675s (33.0%) |
| >1kt | 7 | 267.8s | 1,875s (8.1%) |

200t 以上的轮次仅占 18.3% 的 turns，但贡献 41.1% 的 E2E 时间。

## 6. DTB 压缩模拟

| 参数组 | Budget | 受影响 Turns | Reasoning 节省 | 预估 E2E 节省 | 风险评估 |
|---|---|---|---|---|---|
| dtb_loose | 1650 | 2 (0.3%) | 11.9% | ~4.2% | 极低 |
| dtb_moderate | 1100 | 5 (0.7%) | 14.8% | ~5.2% | 极低 |
| **dtb_tight** | **850** | **7 (0.9%)** | **16.5%** | **~10.5%** | **低** |
| Aggressive | 500 | 47 (6.3%) | 24.3% | ~13.2% | 中 |
| Very Aggressive | 200 | 208 (27.8%) | 47.9% | ~21.5% | 高 |

### 受影响轮次定位（dtb_tight, budget=850）

以下 7 个轮次的 reasoning >850t，将被 DTB 截断：

| Session | Turn | 原始 Reasoning | 截断后 | 节省 | E2E | Tool | Step |
|---|---|---|---|---|---|---|---|
| 7009df0f9307 | 76 | 10,139t | 850t | 9,289t | 780.5s | read | 代码生成 |
| 7009df0f9307 | 80 | 5,139t | 850t | 4,289t | 446.6s | edit | 代码生成 |
| 8155c36fcc64 | 17 | 2,097t | 850t | 1,247t | 199.3s | write | 代码生成 |
| ed39ae85e614 | 1 | 1,762t | 850t | 912t | 112.9s | bash | Skill启动 |
| 4a092f0fa0ac | 31 | 1,548t | 850t | 698t | 155.5s | edit | 代码生成 |
| 80e079b2325c | 6 | 1,504t | 850t | 654t | 97.9s | bash | DT设计文档 |
| 7009df0f9307 | 71 | 1,012t | 850t | 162t | 82.1s | read | 代码生成 |

**风险分析**：

- **dtb_tight (850)**：仅截断上表 7 个轮次，保留约 65 秒思考时间（850t ÷ 13t/s），对大多数代码理解任务足够。最大风险点在 session 7009df0f9307 turn 76（read 10,139t → 850t），这是极端长度的源码分析推理
- **Aggressive (500)**：额外影响 40 个 500-850t 的轮次，其中包含 DT 设计文档生成和复杂代码规划，质量风险中等
- **Very Aggressive (200)**：覆盖 27.8% 的轮次，包括大量中等长度的工具决策推理，质量风险最高

## 7. 评测质量基线

| 指标 | 值 |
|---|---|
| 总任务（evaluation_results.json） | 20 |
| 代理捕获的任务 | 14 skill sessions (11 唯一分支 + 3 重试) |
| 有 eval report 的任务 | 17 (含前次运行残留 1 个) |
| DT 通过率 ≥93% | 6 tasks |
| 编译失败 | 12 tasks |
| — 基础设施/依赖问题 | 8 tasks (非代码质量) |
| — 代码质量问题 | 4 tasks |
| FAILED/TIMEOUT（无 eval report） | 3 tasks |

编译失败中 8/12 为 Maven 依赖不可达等基础设施问题，非 thinking 质量导致。DTB 压缩需要确保 **6 个已通过任务的通过率不退化**。

## 8. 推荐实验方案

```
优先级 1: dtb_tight (budget=850)
  - APPEND_BODY: {"model":"qwen3.6-27b","enable_think_budget":True,
                   "fixed_budget_tokens":850,"fixed_check_length":500,
                   "fixed_think_stride":250,"fixed_num_thinking":2,
                   "fixed_min_think_tokens":500,"fixed_prob_th":0.93}
  - 预期: 节省 16.5% reasoning, E2E 加速 ~10%
  - 风险: 低（仅影响 7 个极长轮次，主要在 session 7009df0f9307）

优先级 2: Aggressive (budget=500)
  - APPEND_BODY: {"model":"qwen3.6-27b","enable_think_budget":True,
                   "fixed_budget_tokens":500,"fixed_check_length":300,
                   "fixed_think_stride":200,"fixed_num_thinking":2,
                   "fixed_min_think_tokens":300,"fixed_prob_th":0.93}
  - 预期: 节省 24.3% reasoning, E2E 加速 ~13%
  - 风险: 中（影响 edit/write 前的深度推理）

优先级 3: dtb_auto
  - APPEND_BODY: {"model":"qwen3.6-27b","enable_think_budget":True}
  - 让 DTB 自动决定 budget
  - 对比固定参数与自动模式的效果
```

验证指标：6 个已通过任务的 DT 通过率不低于基线（≥93%），同时观察 reasoning 节省和 E2E 加速。
