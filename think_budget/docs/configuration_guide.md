# 配置参数参考 — Configuration Guide

## 概述

所有超参数集中定义在 `naie/config/settings.py` 的 `CONFIG` 字典中，格式为 `(value, description)` 元组，方便商业交接。`THINK_BUDGET_CONFIG_47` 提取纯值字典供运行时使用。

### CONFIG 结构

```python
CONFIG = {
    "enable_budget": (True, "[P0] BudgetForcing总开关 — False时模型自由生成不截断"),
    "check_length":  (200,  "[P1] 初始思考token数 — 第一次API调用请求的思考量"),
    "prob_th":       (0.95, "[P1] 置信度阈值 — decision_score>=此值且think充足时接受答案"),
    # ... 更多参数
}

# 提取纯值字典
THINK_BUDGET_CONFIG_47 = {k: v[0] for k, v in CONFIG.items()}
# 结果: {"enable_budget": True, "check_length": 200, "prob_th": 0.95, ...}
```

### 参数优先级

```
CLI 参数 > request body (API) > CONFIG 默认值
```

- **CLI 参数**：通过 `run_budget.py --prob_th 0.90` 传入，直接覆盖 `THINK_BUDGET_CONFIG_47`
- **request body**：通过 API 请求 JSON 字段传入，覆盖 CONFIG 但不修改全局状态
- **CONFIG 默认值**：`settings.py` 中定义的默认值

### 运行时 API

```python
from naie.config.settings import get_value, set_value, print_config

get_value('prob_th')          # 获取值 → 0.95
set_value('prob_th', 0.90)   # 运行时修改
print_config()                # 格式化显示所有配置
```

---

## 预算优先级链

调用 `call_model()` 时，预算按以下优先级计算（最高优先在前）：

| 优先级 | 来源 | 条件 | 说明 |
|--------|------|------|------|
| Step 0 | `BUDGET_ENABLED` | `=False` | **统一总开关**：关闭所有预算控制，模型自由生成 |
| Step 0.5 | `FIXED_BUDGET_TOKEN` | > 0 且 `fixed_budget_tokens=0` | 统一预算配置覆盖，将值赋给 `fixed_budget_tokens` |
| 1 | `fixed_budget_tokens` | > 0 | 固定覆盖，跳过所有动态计算 |
| 2 | `task_type_override` | 非空 | 强制使用指定任务类型的预算（未知类型自动注册） |
| 3 | 任务类型预算 | `enable_task_type_budget=True` | 按分类结果设置 token 预算（未知类型自动注册） |
| 4 | 难度估计器 | `enable_difficulty_estimator=True` | 14 维特征融合 → 分段预算（history 中不覆盖 check_length/num_thinking） |
| 5 | 历史轨迹预算 | `enable_history_budget=True` | 冷/温/热启动模式（与 difficulty_estimator 交互） |
| 6 | CONFIG 默认值 | — | 原始 CONFIG 值 |
| 7 | 固定子参数 | `fixed_*` > 0 | 覆盖特定子参数 |
| 8 | `CONFIDENCE_THRESHOLD` | ≠ 0.95 且 `fixed_prob_th=0` | 统一预算配置覆盖 prob_th |
| 8 | `PERCENTILE_STEP` | ≠ 0.75 且 `enable_history_budget` | 统一预算配置覆盖 history_stride_percentile |

**注意**：
- `fixed_*` 子参数（`fixed_num_thinking`, `fixed_think_stride` 等）无论预算链如何计算，都会在最后覆盖对应值。
- 当 `enable_difficulty_estimator=True` 时，history_budget 的 warm/hot 模式**不覆盖** `check_length` 和 `num_thinking`（由 difficulty_estimator 控制），仅 `think_stride` 可被历史百分位覆盖。
- 不支持在线动态预算调整。所有预算参数在 `call_model()` 入口一次性计算，循环内不再修改。

---

## 参数分组参考

### P0 总开关

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_budget` | `True` | BudgetForcing 总开关。`False` 时模型自由生成不截断，等效于基线模式。通过 `_cfg_to_params()` 映射为 `enable_confidence` 参数传入 `call_model()`。 |

---

### P1 核心参数

| Key | 默认值 | 说明 |
|-----|--------|------|
| `check_length` | `200` | 初始思考 token 数 — 第一次 API 调用请求的思考量。模型首次被要求生成的思考长度。 |
| `think_stride` | `100` | 每轮思考增量 token — BudgetForcing 循环中每轮追加的思考量。轮次越多，累积思考越长。 |
| `prob_th` | `0.95` | 置信度阈值 — `decision_score >= prob_th` 且 `think_sufficient` 时接受答案。越高越保守（需要更多轮思考），越低越激进（更早接受）。 |
| `num_thinking` | `10` | 最大尝试轮次 — BudgetForcing 循环最多跑几轮。达到此轮次后强制返回最后答案。 |
| `check_answer_len` | `4000` | 试探答案长度 — 每轮 induced 后请求多少 token 生成答案。必须足够写完完整 JSON。CONFIG 默认 4000，call_model 签名默认 20（生产必须用 CONFIG 覆盖）。 |
| `max_tokens` | `65536` | 最大总 token — 整体 budget 上限。最终答案始终给 `max_tokens_answer=65536` 确保 JSON 完整。 |

**调优建议**：

| 任务复杂度 | check_length | num_thinking | prob_th | 说明 |
|-----------|-------------|-------------|---------|------|
| 简单 | 100-150 | 5-6 | 0.90 | 快速通过，低预算 |
| 中等 | 200-300 | 8-10 | 0.93 | 默认配置 |
| 复杂 | 300-500 | 15+ | 0.95 | 充分推理，高阈值 |

---

### P2.2 连续相同答案

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_consecutive_same_stop` | `True` | 连续相同答案早停开关。 |
| `consecutive_same_stop` | `2` | 连续相同次数阈值 — 连续 N 轮 trial answer 一致则提前接受。 |

**CONFIG 映射注意**：`_cfg_to_params()` 中 `enable_consecutive_same_stop=False` 映射为 `consecutive_same_stop=999`（永不触发）。

---

### P3.1 Value 最低概率（核心创新）

| Key | 默认值 | call_model 签名默认 | 说明 |
|-----|--------|-------------------|------|
| `enable_value_min_confidence` | `True` | `True` | Value Min Confidence 开关 — 剔除结构 token，取答案 bottom-K value token 的 logprob 均值。这是核心创新，默认开启。 |
| `value_min_bottom_k` | `5` | `3` | 取最低 K 个 value token 的 logprob 求均值。K 越大越宽容（取更多 token 均值），K 越小越严格（只看最差的）。**CONFIG 默认 5 与签名默认 3 不同**，CONFIG 覆盖签名默认。 |

**效果**：开启后比纯平均置信度更准确反映模型对实际内容（而非 JSON 语法）的确定性。详见 [confidence_estimation.md](confidence_estimation.md)。

---

### P3.4 完整链置信度

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_full_chain_confidence` | `False` | 完整链置信度 — 思维链 Min-10%（宽容）+ 答案 Bottom-K（零容忍），AND 门取 min。需要思维链的完整 logprobs，优先级最高。 |
| `full_chain_min_percentile` | `0.1` | 思维链最差百分比 — 0.1 = 最差 10% Value Token 均值。 |

---

### P3.3 Clean Context

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_clean_context` | `True` | Clean Context — 继续思考时丢弃 induced 部分（含试探答案和闭合标签），节省 token。开启后每轮只保留原始思考内容，不累积试探答案的 token。 |

---

### 安全门

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_safety_gate` | `False` | 超时自动回退 — 超过 `1.5 × baseline_time_limit` 强制返回已有最佳答案。防止极端情况下单题耗时过长。 |
| `baseline_time_limit` | `90.0` | 基线单题平均延迟（秒）— soft gate 在 1x 触发提醒，hard gate 在 1.5x 触发强制回退。 |

---

### 路由参数

| Key | 默认值 | 说明 |
|-----|--------|------|
| `min_think_tokens` | `100` | 最低思考 token — `actual_len` 不足此值则不提前接受（软门控）。即使置信度达标，也必须思考够量。 |
| `max_api_calls` | `20` | 单题最大 API 调用次数 — 0=不限。BudgetForcing 每轮至少 1 次思考调用 + 1 次答案调用，加上重试，总调用数可能较高。 |

---

### 任务类型判断

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_task_type_budget` | `True` | 启用任务类型→token 预算映射。根据检索/编码/数学/推理/分类等类型设置不同 token 预算。 |
| `default_task_type` | `"general"` | 默认任务类型 — 无法判断时使用。 |
| `custom_task_types_path` | `None` | 自定义任务类型配置路径 — None 使用内置默认，指定 JSON 文件则加载覆盖。 |
| `task_type_override` | `""` | 手动覆盖任务类型 — 空串=自动判断，非空则强制使用指定类型（跳过分类）。 |

**10 种任务类型及默认预算**：

| 任务类型 | 描述 | typical_tokens | check_length | think_stride | num_thinking | min_think_tokens | prob_th |
|---------|------|---------------|-------------|-------------|-------------|-----------------|---------|
| `retrieval` | 检索/查询 | 500 | 200 | 100 | 3 | 50 | 0.90 |
| `classification` | 选择题/归类 | 800 | 300 | 100 | 4 | 100 | 0.90 |
| `coding` | 编程/代码 | 12000 | 2000 | 500 | 15 | 2000 | 0.95 |
| `math` | 数学/证明 | 8000 | 1500 | 400 | 12 | 1500 | 0.95 |
| `reasoning` | 逻辑推理 | 5000 | 800 | 300 | 8 | 500 | 0.93 |
| `tool_call` | API/参数填充 | 1000 | 400 | 200 | 8 | 100 | 0.95 |
| `fault_diagnosis` | 故障诊断 | 1500 | 500 | 200 | 8 | 200 | 0.95 |
| `knowledge_qa` | 事实问答 | 3000 | 600 | 200 | 6 | 200 | 0.92 |
| `planning` | 任务规划 | 2500 | 600 | 200 | 8 | 300 | 0.93 |
| `general` | 兜底通用 | 3000 | 500 | 200 | 8 | 200 | 0.93 |

---

### 历史轨迹预算

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_history_budget` | `True` | 启用历史轨迹驱动的预算推算 — 基于同类问题历史消耗动态调整预算。 |
| `history_budget_multiplier` | `1.2` | warm-start 预算乘数 — 在历史均值基础上多 20% 余量避免低估。 |
| `history_low_success_boost` | `1.3` | 低成功率预算放大 — 成功率 < 0.7 时预算乘此系数。 |
| `history_stride_percentile` | `0.75` | 步长计算历史百分位 — 0.75 = 取 p75 token 消耗的 1/num_thinking。 |
| `new_task_unlimited` | `True` | 新任务类型不设 budget 限制 — 首次遇到新类型让模型自由发挥，收集历史后再启用 budget。 |
| `auto_cluster_threshold` | `5` | 自动聚类新任务类型的历史条数阈值 — 积累超过此数自动聚类并注册新类型。 |
| `history_warmup_count` | `3` | warm-start 条数阈值 — 历史 ≤3 条用 warm-start（1.2x 余量），>3 条用 hot-start（p75）。 |
| `history_warmup_strategy` | `"moderate"` | warm-start 策略：`conservative`（1.5x，高方差 1.95x）/ `moderate`（1.2x，高方差 1.32x）/ `aggressive`（1.0x，高方差 1.1x）。 |

**三阶段预算**：

| 阶段 | 条件 | 策略 |
|------|------|------|
| **冷启动** | 0 条历史 | 使用 `task_type_budget` 默认值。若 `new_task_unlimited=True` 则不设限制。 |
| **温启动** | 1~warmup_count 条 | `avg_tokens × multiplier`。方差感知：高 CV → 更大缓冲。 |
| **热启动** | >warmup_count 条 | `p75 token 消耗`，按成功率调整。低成功 → 乘 `low_success_boost`。 |

---

### 人工可控超参

> 这些参数覆盖所有动态计算，用于确定性控制或 A/B 测试

| Key | 默认值 | 说明 |
|-----|--------|------|
| `fixed_budget_tokens` | `0` | 固定 budget token 消耗 — 0=不固定（动态计算），>0 则覆盖所有动态计算，直接作为 think 预算上限。**最高优先级**。 |
| `fixed_num_thinking` | `0` | 固定最大轮次 — 0=不固定，>0 则覆盖 num_thinking。 |
| `fixed_think_stride` | `0` | 固定步长 — 0=不固定，>0 则覆盖 think_stride。 |
| `fixed_check_length` | `0` | 固定初始思考长度 — 0=不固定，>0 则覆盖 check_length。 |
| `fixed_prob_th` | `0.0` | 固定置信度阈值 — 0.0=使用 prob_th，>0 则覆盖 prob_th。 |
| `fixed_min_think_tokens` | `0` | 固定最低思考 token — 0=不固定，>0 则覆盖 min_think_tokens。 |

**使用场景**：

```bash
# 固定 2000 token 预算，测试特定预算下的表现
python run_budget.py --fixed_budget_tokens 2000

# 固定 5 轮思考，测试轮次影响
python run_budget.py --fixed_num_thinking 5

# 固定置信度阈值 0.90，测试阈值敏感度
python run_budget.py --fixed_prob_th 0.90

# 组合使用：固定预算 + 固定步长
python run_budget.py --fixed_budget_tokens 3000 --fixed_think_stride 200
```

---

### P5 难度估计器

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_difficulty_estimator` | `False` | 难度估计器 — 14 维连续特征 + 场景先验输出 difficulty∈[0,1]，映射到分段预算。 |
| `difficulty_check_length_base` | `400` | 简单题 check_length。 |
| `difficulty_check_length_max` | `6000` | 困难题 check_length。 |
| `difficulty_min_think_base` | `100` | 简单题 min_think_tokens。 |
| `difficulty_min_think_max` | `6000` | 困难题 min_think_tokens。 |
| `difficulty_stride_base` | `400` | 简单题 think_stride。 |
| `difficulty_stride_max` | `6000` | 困难题 think_stride。 |
| `difficulty_num_thinking_base` | `10` | 简单题 num_thinking。 |
| `difficulty_num_thinking_max` | `20` | 困难题 num_thinking。 |
| `difficulty_answer_len_base` | `2000` | 简单题 check_answer_len。 |
| `difficulty_answer_len_max` | `6000` | 困难题 check_answer_len。 |

**映射公式**：`param = base + difficulty × (max - base)`

difficulty 由 14 维特征融合计算：问题长度、关键词密度、中文比例、数字密度、JSON 嵌套深度等。

---

### P6 DEER Trial Answer

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_deer_trial` | `False` | DEER trial answer — 连续稳定则提前接受 + 置信度加成。 |
| `deer_trial_stable_rounds` | `2` | trial 稳定轮次 — 连续 N 轮 trial answer 一致则触发。 |
| `deer_trial_answer_len` | `30` | trial answer 比较长度。 |
| `deer_confidence_boost` | `0.05` | 稳定时置信度加成。 |

---

### 实验性参数

| Key | 默认值 | 说明 |
|-----|--------|------|
| `enable_soft_truncation` | `True` | 软截断 — 思考被截断时在自然断点（句号/换行/JSON 闭合）处闭合，**所有 6 个截断点**均生效（初始思考/中间轮次/最终答案/stride追加/accept追加/兜底追加）。详见 [architecture.md](architecture.md)。 |
| `enable_dual_channel` | `False` | 混合双通道评估 — 推理区（宽容 Min-10%）+ 动作区（零容忍 Bottom-K）。 |
| `reasoning_min_percentile` | `0.1` | 推理区 Min-% 均值百分位 — 0.1 = 取最差 10% 的 Value Token 均值。 |
| `consecutive_same_stop` | `2` | 连续相同次数阈值（API 服务器参数）。 |

---

### 统一预算配置参数

> 这 4 个参数是面向商业交付的统一配置接口，在 `budget_forcing.py` Step 0/0.5/8 生效

| Key | 默认值 | 生效位置 | 说明 |
|-----|--------|---------|------|
| `BUDGET_ENABLED` | `True` | Step 0 | **统一总开关**。`False` 时关闭所有预算控制（enable_confidence, task_type_budget, history_budget, difficulty_estimator），模型自由生成。 |
| `FIXED_BUDGET_TOKEN` | `0` | Step 0.5 | 统一固定预算 token。当 `fixed_budget_tokens=0` 但本参数 >0 时，将本参数值赋给 `fixed_budget_tokens`，等效于最高优先级固定覆盖。 |
| `CONFIDENCE_THRESHOLD` | `0.95` | Step 8 | 统一置信度阈值。当 `fixed_prob_th=0.0` 且本参数 ≠ 0.95 时，覆盖 `prob_th`。 |
| `PERCENTILE_STEP` | `0.75` | Step 8 | 统一百分位步长。当 `enable_history_budget=True` 且本参数 ≠ 0.75 时，覆盖 `history_stride_percentile`。 |

---

## CLI 参数对照表

`run_budget.py` 支持的所有 CLI 参数及其对应的 CONFIG key：

### 核心参数

| CLI 参数 | CONFIG Key | 类型 | 默认值 |
|---------|-----------|------|--------|
| `--max_cases N` | — | int | 全部 |
| `--check_length N` | `check_length` | int | 200 |
| `--think_stride N` | `think_stride` | int | 100 |
| `--prob_th FLOAT` | `prob_th` | float | 0.95 |
| `--num_thinking N` | `num_thinking` | int | 10 |
| `--check_answer_len N` | `check_answer_len` | int | 4000 |
| `--max_tokens N` | `max_tokens` | int | 65536 |
| `--min_think_tokens N` | `min_think_tokens` | int | 100 |
| `--max_api_calls N` | `max_api_calls` | int | 20 |
| `--baseline_time_limit FLOAT` | `baseline_time_limit` | float | 90.0 |

### 开关参数（on/off）

| CLI 参数 | CONFIG Key | 默认 |
|---------|-----------|------|
| `--enable_budget on\|off` | `enable_budget` | on |
| `--enable_value_min_confidence on\|off` | `enable_value_min_confidence` | on |
| `--enable_clean_context on\|off` | `enable_clean_context` | on |
| `--enable_safety_gate on\|off` | `enable_safety_gate` | off |
| `--enable_soft_truncation on\|off` | `enable_soft_truncation` | on |
| `--enable_difficulty_estimator on\|off` | `enable_difficulty_estimator` | off |
| `--enable_deer_trial on\|off` | `enable_deer_trial` | off |

### 任务类型 & 历史预算

| CLI 参数 | CONFIG Key | 类型 | 默认值 |
|---------|-----------|------|--------|
| `--enable_task_type_budget on\|off` | `enable_task_type_budget` | bool | on |
| `--task_type NAME` | `task_type_override` | str | "" |
| `--default_task_type NAME` | `default_task_type` | str | general |
| `--custom_task_types_path PATH` | `custom_task_types_path` | str | None |
| `--enable_history_budget on\|off` | `enable_history_budget` | bool | on |
| `--new_task_unlimited on\|off` | `new_task_unlimited` | bool | on |
| `--history_warmup_strategy STR` | `history_warmup_strategy` | str | moderate |
| `--auto_cluster_threshold N` | `auto_cluster_threshold` | int | 5 |
| `--history_budget_multiplier F` | `history_budget_multiplier` | float | 1.2 |
| `--history_low_success_boost F` | `history_low_success_boost` | float | 1.3 |
| `--history_stride_percentile F` | `history_stride_percentile` | float | 0.75 |
| `--history_warmup_count N` | `history_warmup_count` | int | 3 |

### 人工固定参数

| CLI 参数 | CONFIG Key | 类型 | 默认值 |
|---------|-----------|------|--------|
| `--fixed_budget_tokens N` | `fixed_budget_tokens` | int | 0 |
| `--fixed_num_thinking N` | `fixed_num_thinking` | int | 0 |
| `--fixed_think_stride N` | `fixed_think_stride` | int | 0 |
| `--fixed_check_length N` | `fixed_check_length` | int | 0 |
| `--fixed_prob_th FLOAT` | `fixed_prob_th` | float | 0.0 |
| `--fixed_min_think_tokens N` | `fixed_min_think_tokens` | int | 0 |

### 并行参数

| CLI 参数 | 说明 | 默认值 |
|---------|------|--------|
| `--parallel` | 启用并行评估 | 关闭 |
| `--concurrency N` | 并行线程数 | 4 |

---

## call_model 签名默认值 vs CONFIG 默认值对照

`budget_forcing.call_model()` 的函数签名有保守的默认值（适合作为独立库使用），生产（NAIE）通过 CONFIG + `_cfg_to_params()` 覆盖。两套默认值**有意不同**：

| 参数 | CONFIG 默认值 | call_model 签名默认值 | 说明 |
|------|-------------|---------------------|------|
| `check_answer_len` | **4000** | 20 | CONFIG 确保写完完整 JSON |
| `value_min_bottom_k` | **5** | 3 | CONFIG 更宽容 |
| `enable_confidence` | **True** (via enable_budget) | True | 一致 |
| `enable_value_min_confidence` | **True** | True | 一致 |
| `enable_clean_context` | **True** | True | 一致 |
| `enable_safety_gate` | **False** | False | 一致 |
| `enable_difficulty_estimator` | **False** | False | 一致 |

**原则**：CONFIG 始终覆盖签名默认值。当 `_cfg_to_params()` 被使用时，所有参数从 CONFIG 读取，签名默认值不生效。

---

## API 服务器运行时配置

### 通过端点修改

```bash
# 查看当前配置
curl http://localhost:8000/v1/config

# 修改配置（全局生效，重启后失效）
curl -X POST http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{"prob_th": 0.90, "enable_value_min_confidence": false}'
```

### 通过请求字段覆盖

```json
{
  "model": "mg2hg",
  "messages": [{"role": "user", "content": "..."}],
  "prob_th": 0.90,
  "enable_value_min_confidence": true,
  "value_min_bottom_k": 5,
  "fixed_budget_tokens": 2000
}
```

请求字段覆盖仅影响当前请求，不修改全局 CONFIG。`None` 值的字段使用 CONFIG 默认值。

---

## 配置验证命令

```bash
# 打印当前配置
python -c "import sys;sys.path.insert(0,'.');from naie.config.settings import print_config;print_config()"

# 验证所有模块导入正常
python -c "import sys;sys.path.insert(0,'.');from naie.optimizations import *;from naie.optimizations.history_module import get_task_type_stats;print('ALL OK')"
```
