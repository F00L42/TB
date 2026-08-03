# Think Budget 泛化性实施计划

## 1. 设计目的

Think Budget 的第一目标是在 13 个产品慢思考场景上，在质量不下降的前提下，
最大化开启特性后的端到端时延收益。用户不应为自己的请求持续传入
`fixed_*` 参数；这些参数只作为阶段一寻找候选策略的实验入口，最终由服务端根据
可见请求自动选择预算。

本计划采用两个阶段：

1. **阶段一：算法寻优。** 先获得 13 场景的 Vanilla、仅开启 DTB、按 Vanilla
   思考长度构造的定制 fixed 策略数据，选择每场景最优策略，再合并为尽量少的预算
   profile，并验证 `scene → budget/reject` 自动选择。
2. **阶段二：在线增强。** 阶段一稳定后再实现陌生场景 history 学习、原生首次
   观察和必要的生产发布保障。阶段一不建设灰度、自动回滚或独立策略包抽象。

一句话流程是：**先用 Vanilla 建立逐样本基线并搜索各产品场景的质量合格最优 fixed
策略，再合并少量 profile、配置 scene 直达预算及拒识规则，最后用自动模式回放产品
集并做一次通用集校验。**

设计基线：

- 文档日期：2026-07-31；
- 目标环境：vLLM `v0.23.0`、vLLM-Ascend `v0.23.0rc1`；
- 当前单场景证据：[`run_02/RESULT.md`](../log/perf/run_02/RESULT.md)；
- 性能归因口径：[`deer_performance_analysis.md`](../debug/deer_performance_analysis.md)；
- 算法状态机边界：
  [`think_budget_vllm_adapter_refactor_plan.md`](../debug/think_budget_vllm_adapter_refactor_plan.md)。

### 1.1 不可违反的原则

1. 运行时只使用一个分类标识 `scene`，流程为
   `最新用户消息 → scene 或 reject → scene 对应预算`；不再引入
   `task_type` 或必须经过的语义类型实体。
2. 预算、匹配证据和优先级继续通过现有
   `budget_config.yaml → unified_config.py` 分级加载，不新增
   `PolicyBundle`。
3. 能推导的字段不存储；实验状态、revision 和验证 run ID 放在实验 manifest，
   不进入运行时 dataclass。
4. 唯一排序指标是 13 个产品场景等权宏平均 paired E2E gain；候选必须同时通过
   每场景及宏平均质量非劣、不得新增 hard failure 两个硬门。收益近似时选择
   profile 更少的方案。
5. 性能账本必须包含 visible thinking、hidden probe 和 final answer 的全部模型
   token，并区分 queue、prefill、decode 和控制器耗时。
6. 拒识是自动策略的正常结果。弱证据或 top1/top2 差距不足时不得强行匹配产品
   scene。

## 2. 已有证据与待回答问题

| 结论 | 证据强度 | 对本计划的影响 |
| --- | --- | --- |
| run_02 相对三次 Vanilla 均值，E2E 降低 `20.5613 s / 31.48%`，可见 token 降低 `55.48%` | 实测 | 固定策略在单场景存在显著收益，值得扩展到 13 场景 |
| 计入 hidden probe 后总模型 generated token 仍约降低 `53.20%` | 实测 | probe 成本必须保留，但不否定该策略的净收益 |
| 旧双分类链在 run_02 上把 16/16 请求判为 `classification` | 实测 + 源码确定 | 必须只分类最新用户消息，并删除双分类裁决 |
| 当前 decision 约为毫秒级，主要成本仍在模型生成 | 实测 | scene 匹配应保持本地确定性规则，不新增 LLM 分类请求 |
| 已有 Judge 主要验证输出结构，语义质量分尚待回填 | 实测 | 当前不能声明跨场景质量非劣 |

下一批数据需要回答：

1. 每个产品场景的 Vanilla 思考 token 分布和 E2E 分布是什么；
2. 仅开启 DTB 的默认策略在哪些场景已有净收益；
3. 依据 Vanilla 构造的首个定制 fixed 策略能否在质量合格时进一步降低 E2E；
4. 哪些场景可共享同一组预算，哪些必须保留专用策略；
5. scene 匹配的误路由、拒识和覆盖情况是否足以保留逐场景收益。

## 3. 最小数据模型

阶段一只使用以下四个概念。它们是文档和实验数据的边界，不要求都实现为
dataclass。

### 3.1 ScenarioSpec

描述一个产品数据集：

```text
ScenarioSpec
  scene                 # 唯一分类和报表标识
  dataset_path
  sample_id_field
  split                 # search / holdout
  quality_metric
  non_inferiority_margin
  hard_failure_rule
```

数据集名称不是天然的预算 profile。只有请求特征可稳定匹配、且成本—质量曲线确实
不同的场景，才保留独立 scene；否则合并到共享预算。

### 3.2 CandidatePolicy

阶段一实验候选：

```text
CandidatePolicy
  policy_id
  fixed_budget_tokens
  fixed_check_length
  fixed_min_think_tokens
  fixed_think_stride
  fixed_num_thinking
  fixed_prob_th
```

`fixed_num_thinking` 由总预算、首次检查长度和 stride 推导后写入实际请求，确保实验
可复现；不再存储可由这些字段推导的 `max_visible_think`。

### 3.3 RouteResult

仅在验证自动路由时记录，不进入 `BudgetPlan`：

```text
RouteResult
  scene
  scene_matched
  candidate_scene
  top_score
  second_score
  score_margin
  reject_reason
  matched_evidence
```

不生成额外 `confidence`。规则路由只使用原始 score、margin 和明确拒识原因。

### 3.4 ExperimentResult

仅用于日志与报表：

```text
ExperimentResult
  run_id / variant / scene / sample_id / parent_request_id
  e2e_ms / ttft_ms
  visible_think_tokens / hidden_probe_tokens / final_answer_tokens
  quality / hard_failure
  effective_budget / route_result / phase_metrics
```

## 4. 模块设计

### 4.1 配置模块

文件：

- `think_budget_vllm_adapter/config/budget_config.yaml`
- `think_budget_vllm_adapter/config/unified_config.py`

`SCENES.<scene>` 同时保存：

- `indicators.strong`、`indicators.weak`；
- `typical_tokens`、`token_range`；
- `check_length`、`min_think_tokens`、`think_stride`、`num_thinking`；
- `prob_th` 和 trial answer 长度相关配置。

公开接口：

```python
get_scene_definitions() -> dict
get_scene_budget(scene: str) -> dict
get_scene_priority() -> list[str]
is_known_scene(scene: str) -> bool
```

`SCENE_PRIORITY` 只解决同分时的确定性顺序，不代替拒识门。

### 4.2 scene 匹配模块

文件：`think_budget_vllm_adapter/optimizations/scene_classifier.py`

输入只取最新一条用户消息，排除 system prompt、工具说明、few-shot assistant 内容
和输出 schema 对规则的污染。

匹配规则：

1. 中文短语使用规范化子串匹配；
2. 英文单词和短语使用词边界匹配，避免 `search` 命中 `searchable`；
3. 代码块、LaTeX、多步骤、选项行、`*_count` 字段和 SQL 使用结构规则；
4. 每个配置证据最多计分一次；
5. top score 低于 `scene_match_min_score`，或 top1-top2 小于
   `scene_match_min_margin` 时返回 `general/reject`。

接口：

```python
classify_scene(
    question: str,
    *,
    min_score: float,
    min_margin: float,
    scene_override: str = "",
) -> RouteResult
```

离线测试必须从 `docs/log/test_results` 抽取真实问题。当前已选取
`TC0000000201`、`0204`、`0212`、`0216`：明确故障和规划样本应命中，证据并列样本
应拒识，并防止复现全部被判成 classification 的旧错误。

### 4.3 预算决策模块

文件：`think_budget_vllm_adapter/budget_controller.py`

决策顺序：

```mermaid
flowchart TD
    A["最新用户消息"] --> B["scene 匹配与拒识"]
    B --> C{"请求含 fixed 实验参数?"}
    C -- "是" --> D["FIXED；同时记录 RouteResult"]
    C -- "否" --> E{"scene 命中?"}
    E -- "是" --> F["SCENE_DIFFICULTY：scene 预算与难度结果合并"]
    E -- "否" --> G["HISTORY：按 general/scene 历史预算；无历史时完整观察"]
    D --> H["统一 fixed override 与 hard token limit"]
    F --> H
    G --> H
    H --> I["BudgetPlan"]
```

`BudgetPlan` 只保留执行状态机所需字段：

```text
scene / scene_matched / decision_branch / budget
confidence_threshold / percentile_step
source_by_field / policy_version / hard_think_token_limit
```

分类证据、难度分解和 history 统计通过 `decision_details` 打点，不放入
`BudgetPlan`。

### 4.4 history 模块

文件：

- `think_budget_vllm_adapter/history/history_module.py`
- `think_budget_vllm_adapter/history/history_budget.py`

history 的设计目的不是利用在线正确性反馈，而是让模型面对未识别的新场景时，逐步
减少不必要的思考：

1. 首次请求走原生完整思考，记录真实思考 token；
2. 样本较少时按历史最大值和均值乘安全系数给预算；
3. 稳定后按思考 token 的分位数选择预算；
4. 在线决策不读取正确性；质量只由离线陌生验证集检查。

陌生验证方法：固定一组从未参与规则和预算选择的任务，按同一 scene 连续请求
第 1 次到第 n 次，分别报告 E2E、预算和正确率；只有正确率相对第一次完整思考不下降
时，才能认为 history 压缩有效。

当前实现已把历史索引和统计统一为 `scene`，并将无在线质量标签明确记为
`thinking_tokens_without_online_quality`。但 `first_full_observation` 目前仍由控制器
大预算路径模拟，并不等同于原生单次生成。真正的 native bypass 及其验收属于阶段二；
在完成前不得把该分支描述为“已实现原生首次观察”。

### 4.5 telemetry 与提取模块

文件：

- `think_budget_vllm_adapter/telemetry.py`
- `tools/extract_tb_phase_metrics.py`

`TB_PHASE` schema 2 对每个 phase 记录：

```text
parent_request_id / child_request_id / phase / round
wall_ms / queue_ms / prefill_ms / decode_ms / controller_cpu_ms
prompt_tokens / cached_prompt_tokens / generated_tokens
visible / stop_reason
```

decision 额外记录：

```text
scene / scene_matched / decision_branch / effective BudgetPlan
classification_source / classification_input_chars
candidate_scene / scores / top_score / second_score / score_margin
min_score / min_margin / reject_reason
strong_matches / weak_matches / structural_matches
difficulty breakdown / history mode and statistics
decision module timings
```

probe 继续记录置信度方法、原始值、pair count、是否接受及原因。日志不记录 prompt 或
回答正文。

提取器兼容 schema 1 和 2，并输出：

- `controller_phases.jsonl`；
- `requests.jsonl`；
- `phase_summary.csv`。

## 5. 具体重构清单

本次评审对应的源码改动：

| 文件 | 改动 |
| --- | --- |
| `context.py` | `BudgetPlan` 和决策分支改为 scene 语义，删除双分类字段和重复诊断状态 |
| `config/unified_config.py`、`budget_config.yaml` | 合并原任务预算、scene token 和指示词为 `SCENES` |
| `optimizations/scene_classifier.py` | 配置驱动的词边界/结构匹配、score+margin 拒识 |
| `optimizations/task_type_budget.py` | 删除 |
| `budget_controller.py` | 最新用户消息直达 scene；scene 命中读预算，拒识走 history |
| `history/*` | 只保留 scene 索引、统计和预算，不再动态注册任务类型 |
| `telemetry.py`、提取器 | schema 2 决策证据、有效预算、history/难度和模块耗时 |
| `tests/*` | 更新 scene-only 决策测试，并从现有产品 CSV 抽样验证分类 |

收到 13 场景数据后再做的改动：

1. 为每个可稳定识别的产品场景补充 `SCENES` 节点和离线正/负样本；
2. 写入质量合格的 CandidatePolicy，优先把相同或近似参数合并；
3. 校准 `scene_match_min_score` 与 `scene_match_min_margin`；
4. 对自动模式做产品 holdout 回放和一次通用/OOD 校验；
5. 阶段二再实现 history 的真实 native bypass。

## 6. 阶段一实验与数据采集

### 6.1 三类必需 run

每个产品场景使用相同样本、顺序、采样参数、随机种子、并发、到达节奏和 prefix
cache 状态，至少提供：

1. `vanilla`：原生单次生成；
2. `dtb_default`：仅开启 DTB，不传 fixed 参数；
3. `dtb_fixed_v1`：按本节公式由 Vanilla 构造的场景定制参数。

不要把三个 variant 混在同一进程热状态中。若必须复用进程，manifest 中记录执行
顺序并在结果中标注 cache 状态。

### 6.2 基于 Vanilla 构造首个 fixed 候选

对场景 \(s\)，先从 Vanilla 输出解析每条请求的**思考 token**，令
\(T_{50,s}\) 为其中位数。若当前结果只有 completion token、无法分离思考与答案，
先补解析，不能直接用 completion token 代替。

首个候选 `fixed_v1`：

```text
fixed_check_length
  = clamp(round_to_50(0.20 * T50), 100, 2000)

fixed_min_think_tokens
  = fixed_check_length

fixed_think_stride
  = clamp(round_to_50(0.10 * T50), 50, 1000)

fixed_budget_tokens
  = max(fixed_check_length,
        round_to_50(0.50 * T50))

fixed_num_thinking
  = max(1,
        ceil((fixed_budget_tokens - fixed_check_length)
             / fixed_think_stride))

fixed_prob_th = 0.93
```

这个候选用于快速判断“按场景定制是否有价值”，不是最终最优参数。质量合格且有正
收益后，以 `fixed_budget_tokens` 的 `0.35/0.50/0.65 * T50` 做粗搜，再只在最优点
附近调整 check length、stride 和阈值。

### 6.3 回传产物

每个 run 放到 `docs/log/perf/<run_id>/`：

```text
manifest.json
client_requests.jsonl 或原始结果 CSV
server.log
controller_phases.jsonl
requests.jsonl
phase_summary.csv
RESULT.md
```

`manifest.json` 最低字段：

```text
run_id / variant / scene / policy_id
vllm / vllm-ascend / pytorch / torch-npu / CANN / model
adapter_commit / vllm_commit / vllm_ascend_commit
sampling / seed / concurrency / arrival_pattern / prefix_cache_state
fixed parameters
dataset path / sample count
```

客户端逐样本结果最低字段：

```text
scene / sample_id / parent_request_id
e2e_ms / ttft_ms / completion_tokens
quality / hard_failure / error
```

服务端通过相同 `parent_request_id` 关联所有内部 phase。Vanilla 没有内部 phase 时，
仍需提供客户端 E2E、思考/答案 token 分解和质量结果。

### 6.4 选择规则

对每个样本 \(i\) 做配对：

```text
paired_gain_i = E2E_vanilla_i - E2E_candidate_i
scene_gain_s  = mean(paired_gain_i in scene s)
macro_gain    = mean(scene_gain_s over 13 scenes)
```

选择顺序固定为：

1. 删除新增 hard failure 的候选；
2. 删除任一场景或 13 场景宏平均质量不满足预声明非劣界的候选；
3. 在剩余候选中按 `macro_gain` 从高到低排序；
4. 收益差落在预声明“近似区间”内时，选择不同预算 profile 更少的候选。

P50、P95、token 分解、phase 耗时、覆盖率和拒识率用于解释结果，不参与重新加权
排名。

## 7. 两阶段实施顺序

### 阶段一：算法寻优

1. 冻结 13 个 `ScenarioSpec` 和 search/holdout；
2. 收集三类必需 run；
3. 按场景通过质量门后选择 fixed 候选；
4. 将近似候选合并为少量预算 profile；
5. 为这些 profile 编写 scene 证据与拒识单测；
6. 在 holdout 上运行自动 scene 路由；
7. 对最终自动候选运行一次开源通用/OOD 测试；
8. 输出 best observed 策略和未解决的拒识场景。

### 阶段二：在线增强

1. 对陌生 scene 实现真正的原生首次观察；
2. 按无标签 token 历史逐步压缩；
3. 用陌生验证集第 1..n 次正确率非下降验收；
4. 算法收益稳定后，再补最小灰度和故障回退能力。

## 8. 验收标准

### 8.1 代码与接口

- [x] adapter 运行时不存在第二套任务分类器或任务预算索引；
- [x] 最新用户消息是 scene 匹配唯一自然语言输入；
- [x] 每个可路由 scene 在 `SCENES` 中同时具有匹配证据和预算；
- [x] 英文证据使用边界匹配，真实产品样本覆盖命中、拒识和负例；
- [x] `BudgetPlan` 不存 RouteResult、难度分解、history 统计或可推导
      `max_visible_think`；
- [x] schema 2 decision 日志能还原 scene 选择、拒识原因和所有有效预算来源；
- [x] schema 1 历史日志仍可由提取器读取。

### 8.2 阶段一算法结果

- [ ] 13 场景均有可配对的 Vanilla、DTB default、fixed 数据；
- [ ] 唯一排序结果为 13 场景等权宏平均 paired E2E gain；
- [ ] 每个场景及宏平均质量均通过预声明非劣界；
- [ ] 不新增解析错误、工具错误、关键事实错误等 hard failure；
- [ ] 收益近似时已选择 profile 数更少的方案；
- [ ] hidden probe 已计入总模型生成 token 和 phase 时间；
- [ ] 最终自动模式在产品 holdout 保持正宏平均收益，并完成一次通用/OOD 校验。

### 8.3 阶段二 history

- [ ] 第一次陌生 scene 请求已确认走原生单次生成，而非控制器大预算近似；
- [ ] 在线预算只依赖可观测 token 历史，不假设在线正确性反馈；
- [ ] 陌生验证集第 1..n 次的正确率相对首次不下降；
- [ ] history 的额外收益、最坏回归和控制器成本均有请求级证据。

## 9. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-07-30 | 初版：定义 13 场景寻优、自动路由和生产化方案 |
| 2026-07-31 | 调整为算法寻优优先、生产化后置的两阶段计划 |
| 2026-07-31 | 根据评审删除强制语义类型、PolicyBundle 和重复指标；统一 scene，明确拒识门、history 无在线质量反馈目的、源码重构和三类服务器数据采集计划 |
