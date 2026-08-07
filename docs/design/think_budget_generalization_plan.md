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
required_matches / missing_required
strong_matches / weak_matches / structural_matches
difficulty breakdown / history mode and statistics
hard_think_token_limit 的全部候选上限、生效来源与最终值
decision module timings
```

probe 继续记录置信度方法、原始值、pair count、是否接受及原因。日志不记录 prompt 或
回答正文。

提取器兼容 schema 1 和 2，命令行只接收一个 `run_<id>` 目录。该目录根部必须有
`server.log`，目录内必须恰有一个 `_SUMMARY.csv`，summary 引用的每个具体结果文件名
也必须在目录内唯一可解析。多个数据集、Judge 和历史请求共用日志时，使用客户端结果
CSV 中的 `request_id` 作为权威白名单，与 `TB_PHASE.parent_request_id` 做精确等值关联：

```text
具体结果 CSV request_id
  → 结果文件归属表
  → server.log 中同 parent_request_id 的 phase
  → 每个具体结果文件的独立提取目录
```

10 秒等待和首末日志行号只用于检查执行顺序，不作为归属条件。首末行表示已知
`request_id` 在结构化服务日志中的可见范围，不等同于客户端 HTTP 测试的精确起止时间；
原生路径完全不打印该 ID 时允许为 `null`。Judge 或旧请求没有出现
在被测结果 CSV 的白名单中，因此即使 phase 与测试请求交错也会被排除。提取器同时输出
`request_index.csv`，逐行记录结果 CSV 行号、是否在日志出现、phase 数及首末日志行；
跨结果文件重复的非空 `request_id` 属于不可消歧输入，直接拒绝提取。Vanilla 没有
`TB_PHASE` 是合法状态，客户端 E2E 结果仍归档，phase 文件为空。

接口固定为：

```bash
python tools/extract_tb_phase_metrics.py docs/log/perf/<run_id>
```

提取器不再接收或推导 run 外部输入，也不复制原始 CSV。若服务器产物未完整上传，必须
先在服务器完成提取，再回传 `extraction_manifest.json`、`request_index.csv` 和各隔离
目录中的摘要；GitHub 上缺失 `server.log` 或明细 CSV 的 run 不要求可重新执行提取。

## 5. 具体重构清单

本次评审对应的源码改动：

| 文件 | 改动 |
| --- | --- |
| `context.py` | `BudgetPlan` 和决策分支改为 scene 语义，删除双分类字段和重复诊断状态 |
| `config/unified_config.py`、`budget_config.yaml` | 合并原任务预算、scene token 和指示词为 `SCENES` |
| `optimizations/scene_classifier.py` | 配置驱动的词边界/结构匹配、产品模板 required gate、score+margin 拒识 |
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

每个 run 放到 `docs/log/perf/<run_id>/`。测试脚本无法按场景切日志时，使用以下层级；
`test_results` 下移为 run 的子目录，每个具体结果文件对应一个隔离的提取目录：

```text
docs/log/perf/<run_id>/
├── manifest.json
├── server.log
├── test_results/
│   ├── _SUMMARY.csv
│   └── <具体结果文件>.csv
├── extraction_manifest.json
├── request_index.csv
├── extracted/
│   └── <具体结果文件名去扩展名>/
│       ├── controller_phases.jsonl
│       ├── requests.jsonl
│       └── phase_summary.csv
└── RESULT.md
```

`extraction_manifest.json` 记录整份服务日志中 phase 总数、被白名单选中的数量、排除
数量，以及每个结果文件的请求覆盖率和首末匹配行。禁止把不同具体结果文件重新聚合到
同一个 `phase_summary.csv`；跨场景比较在后续分析阶段从各隔离目录读取。`_SUMMARY.csv`
和结果 CSV 可以直接放在 run 根目录，也可以放在 `test_results/` 等子目录；只要求在
整个 run 下名称唯一。

`manifest.json` 最低字段：

```text
run_id / variant / scene / policy_id
vllm / vllm-ascend / pytorch / torch-npu / CANN / model
adapter_commit / vllm_commit / vllm_ascend_commit
sampling / seed / concurrency / arrival_pattern / prefix_cache_state
fixed parameters
dataset path / sample count
```

### 6.4 run_06：分类与 fixed_v3 并行

run_06 不等待 fixed_v3 收口再启动分类验证，但也不提前把未验证的聚类中心写入运行时：

- 6 个 fixed_v2 已过质量门的场景只传 `enable_think_budget = true`，验证模板路由能
  自动恢复各自的冻结向量；
- 其余 7 个场景显式传入各自 fixed_v3，fixed 分支优先于 scene 预算；
- 第一阶段固定 `enable_difficulty_estimator = false`、
  `enable_history_budget = false`；未识别请求使用 default budget，暂不宣称 native bypass；
- `max_think_tokens` 作为 scene 的硬预算上限参与统一取最小值，避免只恢复四维
  `BudgetVector`、却丢失 fixed 实验中的 `fixed_budget_tokens` 约束；
- 6 组冻结配置按 hard limit 暂记为 fast（850/1050）、medium（1500/1600）、
  deep（2600/3050）三类候选。该标签只用于分析和后续交叉回放，不进入运行时对象，
  run_06 前不合并类内参数。

具体场景映射、锚点、预算和验收命令见
[`run_06_scene_routing_and_fixed_v3_plan.md`](../debug/run_06_scene_routing_and_fixed_v3_plan.md)。

客户端逐样本结果最低字段：

```text
scene / sample_id / parent_request_id
e2e_ms / ttft_ms / completion_tokens
quality / hard_failure / error
```

服务端通过相同 `parent_request_id` 关联所有内部 phase。Vanilla 没有内部 phase 时，
仍需提供客户端 E2E、思考/答案 token 分解和质量结果。

### 6.4 裁决分支与生效配置审计

每个 run 在完成请求白名单隔离后、进入性能候选比较前，必须执行一次离线**裁决审计**。
它的目的不是增加运行时抽象，而是证明被测请求实际走了预期分支、使用了预期配置，
避免把 scene 误路由、难度动态预算或 fixed 未生效造成的结果归因给候选参数。

权威输入只使用：

```text
_SUMMARY.csv 与具体结果 CSV
extraction_manifest.json
extracted/<具体结果名>/requests.jsonl
```

对每个预期经过控制器的请求，从 `requests.jsonl` 读取：

```text
decision_branch / scene / scene_matched / reject_reason
effective BudgetPlan / confidence_threshold / percentile_step
hard_think_token_limit / source_by_field / policy_version
```

生效配置签名固定为：

```text
(check_length, min_think_budget, num_thinking, think_stride,
 confidence_threshold, percentile_step, hard_think_token_limit)
```

按具体结果文件分别汇总分支、scene、配置签名和字段来源；同一测试集出现多个 scene、
多个配置或拒识时必须展开，不能只报告众数。`DecisionBranch.HISTORY` 只表示进入未命中
scene 的代码分支；若 `decision_details.history.budget_mode=history_disabled`，应报告为
“history 兜底、实际使用 default”，不能描述成使用了历史预算。

每个 run 的 `RESULT.md` 至少记录覆盖率闭合、总体分支/scene 计数、全部有效配置、逐
测试集分解、拒识原因和异常请求。进入性能选择前必须满足：

```text
sum(各结果客户端行) = extraction_manifest.client_rows
sum(decision_branch) = sum(scene) = 被审计 DTB 请求总数
每个 DTB 请求恰有一个 effective BudgetPlan
Vanilla 原生路径没有 DTB decision phase
dtb_default 不出现 fixed 分支或 fixed 字段来源
dtb_fixed_* 全部进入 fixed，且字段来源与传入参数一致
```

任一不等式、缺失 BudgetPlan 或 variant 分支断言失败时，该 run 只能用于排障，不能
参与 E2E 排名。实例见 [`run_04/RESULT.md`](../log/perf/run_04/RESULT.md)。

### 6.5 选择规则

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

步骤 3 与步骤 4～5 不必全局串行。只要已有多个场景通过质量门，就可以用这些
CandidatePolicy 开始**离线**聚类、分类规则和拒识单测；未通过场景继续局部 fixed
搜索并标记为 `reject/native`。步骤 6 仍是收口门：只有待覆盖场景已有质量合格策略，
或拒识已验证真正回到原生单次生成，才能写入并测试自动运行配置。聚类结果只用于让
多个 scene 共享相同预算值，不引入运行时必经的 `BudgetProfile` 对象。

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
- [x] run_05 的 633 条请求已离线全量回放：6 个冻结场景 282/282 命中目标 scene，
      其余 351/351 未误入这 6 个产品 scene；
- [x] scene 命中时四维预算、置信度阈值和 hard limit 均能恢复 frozen v2，且字段来源
      可由 decision telemetry 还原；
- [x] 第一阶段默认关闭 difficulty/history；拒识 telemetry 明确记录
      `history_disabled`、`default_budget` 和拒识原因；
- [x] `BudgetPlan` 不存 RouteResult、难度分解、history 统计或可推导
      `max_visible_think`；
- [x] schema 2 decision 日志能还原 scene 选择、拒识原因和所有有效预算来源；
- [x] schema 1 历史日志仍可由提取器读取。
- [x] 共享服务日志可按结果 CSV 的 `request_id` 精确拆分，旧请求和 Judge phase 不进入
      被测结果目录；
- [x] 每个具体结果文件有独立 `phase_summary.csv`，并有逐请求关联审计与首末日志行；
- [x] 提取 CLI 只需一个 run 目录参数，并能递归发现唯一 summary 和结果 CSV；
- [x] 所有原始输入均位于 `<run_id>` 内，Vanilla 无 phase 时仍可完整归档。

### 8.2 阶段一算法结果

- [x] 13 场景均有可配对的 Vanilla、DTB default、fixed 数据；
- [x] run_04、fixed_v1 和 fixed_v2 的 `RESULT.md` 已完成裁决审计，覆盖率、分支和配置计数全部闭合；
- [x] fixed_v1/fixed_v2 请求均命中 fixed 分支，且有效字段来源和值与候选文件一致；
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
| 2026-08-03 | 增加共享服务日志的 request-id 白名单隔离；结果 CSV 下移到 run；提取接口收敛为单一 `run_<id>` 目录参数，自动发现固定日志、唯一 summary 和全部结果 CSV |
| 2026-08-05 | 固化提取后的裁决审计：按测试集还原最终分支、scene、生效配置与字段来源；增加 Vanilla/default/fixed variant 断言，并以 run 内 `RESULT.md` 作为性能选择前置证据 |
| 2026-08-07 | 根据 fixed_v2 结果允许局部 fixed 搜索与离线聚类/分类调试并行；自动配置收口仍要求质量合格策略或已验证的 native reject，详见 [`run_05_fixed_v2/RESULT.md`](../log/perf/run_05_fixed_v2/RESULT.md) |
| 2026-08-07 | 实现 run_06 第一阶段：6 个 frozen v2 产品模板直达精确 scene 预算，增加 scene hard limit、required gate 和拒识回退 telemetry；633 条历史请求完成正负全量回放，聚类中心合并等待 fixed_v3 与交叉回放。 |
