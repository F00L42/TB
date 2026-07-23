# 架构详解 — Architecture

## 概述

Think Budget Refactored 采用分层架构设计，核心是 **BudgetForcing 循环** — 一个多轮迭代推理机制，每轮生成思考内容后试探答案并评估置信度，置信度达标即提前接受，否则追加思考继续推理。

---

## 三层调用链

系统的调用路径分为三层，从入口脚本到最终模型调用：

```
入口脚本 (run_baseline.py, run_budget.py, run_one.py, api_server.py)
  → naie/evaluation/runner.py         # evaluate_baseline(), evaluate_budget(), _evaluate_one()
    → naie/inference/think_budget.py   # call_model() — CONFIG→params 适配器 (_cfg_to_params)
      → naie/models/budget_forcing.py  # call_model() — 核心 BudgetForcing 循环 (~2040行)
        → naie/models/model_client.py  # model_client — Completions API 客户端 + 连接池
        → naie/optimizations/*          # 所有优化模块
```

### 各层职责

| 层级 | 模块 | 职责 |
|------|------|------|
| **入口层** | `run_*.py`, `api_server.py` | CLI 参数解析、数据集加载、结果保存、API 服务器 |
| **编排层** | `naie/evaluation/runner.py` | 评估流程编排：重试逻辑、评分计算、历史记录、场景/任务类型分类 |
| **适配层** | `naie/inference/think_budget.py` | CONFIG → `call_model()` 参数映射 (`_cfg_to_params`)、NAIE 键名注入 |
| **核心层** | `naie/models/budget_forcing.py` | BudgetForcing 主循环、预算计算、置信度评估、早停决策 |
| **基础设施层** | `model_client.py`, `optimizations/` | API 调用、置信度计算、token 过滤、历史查询等 |

---

## CONFIG → call_model 参数映射

### 映射机制

`naie/config/settings.py` 定义 `CONFIG` 为 `(value, description)` 元组字典。`THINK_BUDGET_CONFIG_47` 提取纯值字典。`naie/inference/think_budget.py::_cfg_to_params()` 将 CONFIG 键映射到 `budget_forcing.call_model()` 的参数名。

**映射不总是 1:1**，以下为特殊映射：

| CONFIG 键 | call_model 参数 | 映射规则 |
|-----------|----------------|---------|
| `enable_budget` | `enable_confidence` | 名称不同，语义相同 |
| `enable_consecutive_same_stop=False` | `consecutive_same_stop=999` | 布尔开关转为永不触发的阈值 |
| `enable_consecutive_same_stop=True` | `consecutive_same_stop=2` | 使用 CONFIG 中的阈值 |
| `model_name` | `model_name` | 默认 `Qwen3.6-27B`；请求或配置可覆盖 |
| （无对应 CONFIG 键） | `stop_condition` | 固定为 `'length'` |
| （无对应 CONFIG 键） | `reference` | 由调用方传入 |

### _cfg_to_params 完整映射表

```python
{
    # P0 总开关
    'enable_confidence':              cfg.get('enable_budget', True),

    # P1 核心参数
    'stop_condition':                 'length',                   # 固定值
    'check_length':                   cfg.get('check_length', 200),
    'think_stride':                   cfg.get('think_stride', 100),
    'num_thinking':                   cfg.get('num_thinking', 10),
    'check_answer_len':               cfg.get('check_answer_len', 4000),
    'model_name':                     cfg.get('model_name', 'Qwen3.6-27B'),
    'prob_th':                        cfg.get('prob_th', 0.95),
    'max_tokens':                     cfg.get('max_tokens', 30000),

    # P2.2 连续相同答案早停
    'consecutive_same_stop':          cfg.get('consecutive_same_stop', 2)
                                       if cfg.get('enable_consecutive_same_stop', True) else 999,

    # P3.1 Value最低概率
    'enable_value_min_confidence':    cfg.get('enable_value_min_confidence', True),
    'value_min_bottom_k':             cfg.get('value_min_bottom_k', 5),

    # P3.4 完整链置信度
    'enable_full_chain_confidence':   cfg.get('enable_full_chain_confidence', False),
    'full_chain_min_percentile':      cfg.get('full_chain_min_percentile', 0.1),

    # P3.3 Clean Context
    'enable_clean_context':           cfg.get('enable_clean_context', True),

    # 安全门
    'enable_safety_gate':             cfg.get('enable_safety_gate', False),
    'baseline_time_limit':            cfg.get('baseline_time_limit', 90.0),

    # 路由参数
    'min_think_tokens':               cfg.get('min_think_tokens', 100),
    'max_api_calls':                  cfg.get('max_api_calls', 20),

    # 任务类型判断
    'enable_task_type_budget':        cfg.get('enable_task_type_budget', True),
    'default_task_type':              cfg.get('default_task_type', 'general'),
    'custom_task_types_path':         cfg.get('custom_task_types_path', None),
    'task_type_override':             cfg.get('task_type_override', ''),

    # 历史轨迹预算
    'enable_history_budget':          cfg.get('enable_history_budget', True),
    'history_budget_multiplier':      cfg.get('history_budget_multiplier', 1.2),
    'history_low_success_boost':      cfg.get('history_low_success_boost', 1.3),
    'history_stride_percentile':      cfg.get('history_stride_percentile', 0.75),
    'new_task_unlimited':             cfg.get('new_task_unlimited', True),
    'auto_cluster_threshold':         cfg.get('auto_cluster_threshold', 5),
    'history_warmup_count':           cfg.get('history_warmup_count', 3),
    'history_warmup_strategy':        cfg.get('history_warmup_strategy', 'moderate'),

    # 人工可控超参
    'fixed_budget_tokens':            cfg.get('fixed_budget_tokens', 0),
    'fixed_num_thinking':             cfg.get('fixed_num_thinking', 0),
    'fixed_think_stride':             cfg.get('fixed_think_stride', 0),
    'fixed_check_length':             cfg.get('fixed_check_length', 0),
    'fixed_prob_th':                  cfg.get('fixed_prob_th', 0.0),
    'fixed_min_think_tokens':         cfg.get('fixed_min_think_tokens', 0),

    # P5 难度估计器 (6 对 base/max)
    'enable_difficulty_estimator':    cfg.get('enable_difficulty_estimator', False),
    'difficulty_check_length_base':   cfg.get('difficulty_check_length_base', 400),
    'difficulty_check_length_max':    cfg.get('difficulty_check_length_max', 6000),
    # ... 其余 5 对同理

    # P6 DEER trial
    'enable_deer_trial':              cfg.get('enable_deer_trial', False),
    'deer_trial_stable_rounds':       cfg.get('deer_trial_stable_rounds', 2),
    'deer_trial_answer_len':          cfg.get('deer_trial_answer_len', 30),
    'deer_confidence_boost':          cfg.get('deer_confidence_boost', 0.05),

    # 历史统计 (由 runner.py 注入)
    'history_avg_tokens':             cfg.get('history_avg_tokens', 0.0),
    'history_failure_rate':           cfg.get('history_failure_rate', 0.0),
    'history_category_avg_tokens':    cfg.get('history_category_avg_tokens', 0.0),
    'history_category_failure_rate':  cfg.get('history_category_failure_rate', 0.0),
    'history_count':                  cfg.get('history_count', 0),

    # 软截断
    'enable_soft_truncation':         cfg.get('enable_soft_truncation', True),

    # 双通道评估
    'enable_dual_channel':            cfg.get('enable_dual_channel', False),
    'reasoning_min_percentile':       cfg.get('reasoning_min_percentile', 0.1),
}
```

### NAIE 键名注入

`think_budget.py` 在模块加载时将 NAIE 特有的 JSON 键名注入 `token_filter`，确保 P3.1 能正确过滤结构 token：

```python
_NAIE_KEY_NAMES = {
    'tool', 'faultNumber', 'faultIMSI', 'faultPort', 'faultType',
    'fault', 'Number', 'IMSI', 'Url', 'Ip', 'APN', 'Time', 'Range',
    'Caller', 'result', 'answer', 'name', 'type', 'value',
    'faultUrl', 'faultIp', 'faultAPN', 'faultTimeRange',
    'isCaller', 'status', 'code', 'message', 'id', 'data',
    'error', 'success', 'query', 'response',
}
set_task_key_names(_NAIE_KEY_NAMES)  # 模块加载时即注入
```

---

## 统一预算优先级链

当 `call_model()` 被调用时，预算按以下优先级计算（**最高优先级在前**）：

```
┌─────────────────────────────────────────────────────────────┐
│ Step 0:   BUDGET_ENABLED = False                            │  ← 总开关
│           → 关闭所有预算控制，模型自由生成                      │
│           → enable_confidence=False, task_type/history/      │
│             difficulty_estimator 全部关闭                     │
├─────────────────────────────────────────────────────────────┤
│ Step 0.5: FIXED_BUDGET_TOKEN > 0                            │  ← 统一预算配置
│           → 覆盖 fixed_budget_tokens (如果后者=0)             │
├─────────────────────────────────────────────────────────────┤
│ 1. fixed_budget_tokens > 0                                  │  ← 最高优先
│    → 固定覆盖，跳过所有动态计算                               │
│    → check_length=min(budget,check_length),                  │
│      min_think_tokens=budget, num_thinking=计算值            │
├─────────────────────────────────────────────────────────────┤
│ 2. task_type_override 非空                                   │
│    → 强制使用指定任务类型的预算（跳过分类）                     │
│    → 未知类型自动动态注册                                     │
├─────────────────────────────────────────────────────────────┤
│ 3. enable_task_type_budget = True                            │
│    → 按分类结果设置每类型 token 预算                           │
│    → 推断出的未知类型也自动注册                               │
├─────────────────────────────────────────────────────────────┤
│ 4. enable_difficulty_estimator = True                        │
│    → 14 维特征融合 → difficulty∈[0,1] → 分段预算映射          │
│    → 仅在 task_type_budget 之后生效                           │
├─────────────────────────────────────────────────────────────┤
│ 5. enable_history_budget = True                              │
│    → 冷/暖/热启动模式，历史轨迹驱动预算                        │
│    → warm/hot 模式下部分参数可被历史覆盖                       │
│    → difficulty_estimator 启用时，check_length/num_thinking   │
│      不被历史覆盖（仅 think_stride 可被覆盖）                  │
├─────────────────────────────────────────────────────────────┤
│ 6. CONFIG 默认值                                             │
│    → 使用原始 CONFIG 值（所有动态计算都未启用时的兜底）          │
├─────────────────────────────────────────────────────────────┤
│ 7. 固定子参数 (fixed_num_thinking 等)                        │  ← 最后覆盖
│    → 无论预算链如何计算，覆盖特定子参数值                       │
├─────────────────────────────────────────────────────────────┤
│ 8. CONFIDENCE_THRESHOLD / PERCENTILE_STEP                    │  ← 统一预算配置
│    → CONFIDENCE_THRESHOLD 覆盖 prob_th（如未被 fixed 覆盖）   │
│    → PERCENTILE_STEP 覆盖 history_stride_percentile          │
└─────────────────────────────────────────────────────────────┘
```

### 优先级说明

- **Step 0** `BUDGET_ENABLED`：**统一总开关**。为 `False` 时，`enable_confidence`、`enable_task_type_budget`、`enable_history_budget`、`enable_difficulty_estimator` 全部强制关闭，模型自由生成（等效基线模式）。
- **Step 0.5** `FIXED_BUDGET_TOKEN`：统一预算配置参数。当 `fixed_budget_tokens=0` 但 `FIXED_BUDGET_TOKEN>0` 时，将 `FIXED_BUDGET_TOKEN` 值赋给 `fixed_budget_tokens`，等效于 Step 1 的固定覆盖。
- **第 1 级** `fixed_budget_tokens`：一旦设置 (>0)，直接作为 think 预算上限，**跳过所有后续动态计算**。`check_length` 取 `min(fixed_budget_tokens, check_length)`，`min_think_tokens` 直接设为 `fixed_budget_tokens`，`num_thinking` 按 `max(3, budget // check_length)` 计算，`think_stride` 按 `max(50, budget // num_thinking)` 计算。但 `fixed_*` 子参数仍然生效（第 7 级）。
- **第 2 级** `task_type_override`：手动指定任务类型字符串（如 `"tool_call"`），跳过分类，直接使用该类型的预算参数。如果指定的类型在已知类型中不存在，会自动动态注册。
- **第 3-5 级**：动态计算路径，按优先级依次尝试。注意 **difficulty_estimator 与 history_budget 的交互**：
  - difficulty_estimator 在 history_budget 之前计算（优先级更高）
  - 当 difficulty_estimator 启用时，history_budget 的 warm/hot 模式**不覆盖** `check_length` 和 `num_thinking`（这两项由 difficulty_estimator 控制），仅 `think_stride` 可被历史百分位覆盖
  - 当 difficulty_estimator 未启用时，history_budget 的 warm/hot 模式可覆盖 `check_length`、`min_think_tokens`、`num_thinking` 和 `think_stride`
- **第 6 级** CONFIG 默认值：所有动态计算都未启用时的兜底。
- **第 7 级** 固定子参数：**独立于预算链**，无论链如何计算，`fixed_num_thinking`、`fixed_think_stride`、`fixed_check_length`、`fixed_prob_th`、`fixed_min_think_tokens` 在最后覆盖对应值。
- **第 8 级** `CONFIDENCE_THRESHOLD` / `PERCENTILE_STEP`：统一预算配置的两个覆盖参数。`CONFIDENCE_THRESHOLD` 在 `fixed_prob_th` 未设置时覆盖 `prob_th`；`PERCENTILE_STEP` 在 `enable_history_budget` 时覆盖 `history_stride_percentile`。

### 历史预算可用性

history_budget 的实际效果取决于历史数据是否可用：

| 条件 | budget_mode | 行为 |
|------|-------------|------|
| 0 条历史 + `new_task_unlimited=True` | `unlimited` | `num_thinking=99`，不设预算限制 |
| 0 条历史 + `new_task_unlimited=False` | `cold_start` | 使用 task_type 的 `typical_tokens` |
| 1~warmup_count 条历史 | `warm_start` | `avg_tokens × multiplier` + 方差感知 |
| >warmup_count 条历史 | `hot_start` | `p75_tokens` + 成功率调整 |

**关键**：`runner.py` 记录的是分类后的 `task_type`（而非硬编码 `'naie'`），使 `history_budget.py` 能按任务类型精确过滤历史。

---

## 任务类型分类与 Token 预算

`task_type_budget.py` 将问题分类为 **10 种任务类型**，每种类型有独立的 token 预算参数：

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

### 分类流程

```
问题文本
  → scene_classifier.classify_scene(question)           # 场景分类 → 8 种场景
  → task_type_budget.classify_task_type(question, scene_info)  # 任务类型分类
    ├─ Step 1: 获取 scene_info (或调用 classify_scene)
    ├─ Step 2: 计算关键词评分
    │   ├─ retrieval: 强/弱关键词 + 短文本加分
    │   ├─ classification: 选择题标记 (A./B./C./D.) 加分
    │   └─ reasoning: 如果...那么... 模式匹配
    ├─ Step 3: 场景映射 → task_type (加 scene_confidence × 3.0)
    ├─ Step 4: 按优先级选最高分类型
    │   coding > math > reasoning > planning > fault_diagnosis
    │   > tool_call > classification > retrieval > knowledge_qa > general
    └─ Step 5: 获取该类型的 budget 参数
```

### 评分规则

| 信号 | 加分 |
|------|------|
| 场景映射 | `scene_confidence × 3.0` 加到映射类型 |
| 强关键词 | 每个 +3.0 |
| 弱关键词 | 每个 +1.0 |
| 检索加分 | 检索关键词命中 + 问题 < 200 字符 → +2.0 |
| 分类加分 | ≥3 个选择题标记 (A./B./C./D.) → +5.0 |
| 推理抑制 | coding/math 评分 > 3.0 时，reasoning 评分 × 0.3 |

### 自定义任务类型

支持两种扩展方式：

1. **JSON 配置文件** — 通过 `custom_task_types_path` 指定，覆盖内置默认值
2. **运行时注册** — `register_custom_task_type()` 动态添加新类型，自动写入 `data/naie/custom_task_types.json`

---

## 历史驱动预算三阶段

`history_budget.py` 基于历史推理记录动态调整 token 预算，分为三个阶段：

### 三阶段预算逻辑

```
┌────────────────────────────────────────────────────┐
│                历史记录查询                          │
│  _get_task_type_stats(task_type, scene_type)       │
│  → count, avg_tokens, p75_tokens, success_rate     │
├────────────────────────────────────────────────────┤
│                                                    │
│  ┌──────────────┐  0 条历史                        │
│  │  冷启动       │ ──────────────────────┐         │
│  │              │  new_task_unlimited?   │         │
│  └──────────────┘    ├─ Yes → 不设限制     │         │
│                      │  (num_thinking=99)  │         │
│                      └─ No → task_type    │         │
│                           默认 typical_tokens       │
│                                                    │
│  ┌──────────────┐  1~warmup_count 条               │
│  │  温启动       │ ──────────────────────┐         │
│  │              │  avg_tokens × multiplier│         │
│  │              │  + 方差感知调整          │         │
│  └──────────────┘  高CV → 更大缓冲        │         │
│                      低CV → 更紧凑        │         │
│                                                    │
│  ┌──────────────┐  >warmup_count 条               │
│  │  热启动       │ ──────────────────────┐         │
│  │              │  p75_tokens 为基础      │         │
│  │              │  + 成功率调整           │         │
│  └──────────────┘  低成功 → ×1.3 放大     │         │
│                      高失败 → 步长×0.8    │         │
└────────────────────────────────────────────────────┘
```

### 各阶段详细策略

| 阶段 | 条件 | 预算计算 | 调整策略 |
|------|------|---------|---------|
| **冷启动/无限** | 0 条历史 + `new_task_unlimited=True` | 无限制（`think_budget=0`, `num_thinking=99`） | 使用 task_type 默认的其他参数 |
| **冷启动/受限** | 0 条历史 + `new_task_unlimited=False` | `typical_tokens` from task_type | — |
| **温启动** | 1~warmup_count 条 | `avg_tokens × effective_multiplier` | **方差感知**：高 CV (>0.5) → 乘数×1.1~1.3；低 CV (<0.3) → 乘数×0.9 |
| **热启动** | >warmup_count 条 | `p75_tokens`（若 p75=0 则回退 avg） | **成功率调整**：success_rate<0.7 → ×`low_success_boost`(1.3)；failure_rate>0.3 → stride×0.8 |

### 温启动策略对比

| 策略 | 基础乘数 | 高方差乘数 | 特点 |
|------|---------|-----------|------|
| `conservative` | 1.5x | 1.95x | 保守，预算充裕 |
| `moderate` | 1.2x | 1.32x | 适中（默认） |
| `aggressive` | 1.0x | 1.1x | 激进，预算紧凑 |

### 新任务泛化流程

```
1. 检测: detect_new_task_type()
   → Jaccard 相似度 (前200字符字符集)
   → max_sim < 0.3 → 判定为新任务

2. 无限观察: new_task_unlimited=True
   → 不设 budget 限制，自由推理
   → 收集 token 消耗数据

3. 温启动估计: 1~3 次观察后
   → avg_tokens × warmup_multiplier
   → 方差感知调整

4. 自动注册: auto_cluster_threshold (默认5) 条历史积累后
   → register_task_type_from_history()
   → 在 custom_task_types.json 中创建新条目
   → 后续同类型问题使用注册的预算

5. 热启动预算控制: >warmup_count 条历史后
   → p75 预算 + 成功率调整
```

**关键设计**：`runner.py` 记录的是**分类后的 task_type**（而非硬编码 `'naie'`），使 `history_budget.py::_get_task_type_stats()` 能按任务类型精确过滤历史。

---

## 核心 BudgetForcing 循环

`naie/models/budget_forcing.py` 的 `call_model()` 是系统的核心，实现 BudgetForcing 主循环。该文件包含两个实现：

| 实现 | 函数 | 架构 | 用途 |
|------|------|------|------|
| **内联同步** | `call_model()` | 所有逻辑直接在函数内 | 默认路径 |
| **内联异步** | `call_model_async()` | 同步逻辑的异步版本 | 并行评估 |

### 主循环流程（内联路径）

```
call_model(prompt, max_tokens, ...)
│
├── 1. 参数初始化
│   ├── _init_params_from_config()  → 60+ 参数
│   └── 预算优先级链计算  → check_length, think_stride, num_thinking, prob_th, ...
│
├── 2. 预算检查
│   └── enable_confidence=False → 跳过循环，单次无限 token 生成
│
├── 3. 初始思考生成
│   └── API 调用: prompt + <think_open_tag>\n → 生成 check_length 个思考 token
│
├── 4. 循环: for round in range(num_thinking):
│   │
│   ├── 4a. 思考标签配对检测 + min_think_tokens 门控
│   │   ├── 检测 <open_tag> / <close_tag> 是否配对
│   │   └── actual_len < min_think_tokens → 不接受，继续思考
│   │
│   ├── 4b. 生成诱导 Prompt (make_induced_prompt)
│   │   ├── 思考标签已配对: 直接使用原 prompt (无需复制)
│   │   └── 思考标签未配对: 复制机制 → 保留原始思考 → 闭合标签 → 追加诱导语
│   │
│   ├── 4c. 试探答案生成
│   │   └── API 调用: induced_prompt → 生成 check_answer_len 个 token
│   │
│   ├── 4d. 置信度/困惑度计算
│   │   ├── confidence.py: 平均 exp(logprobs)
│   │   ├── token_filter.py: Value Min Confidence (P3.1)
│   │   └── confidence_gates.py: compute_decision_score()
│   │
│   ├── 4e. 双通道评分 (如果启用)
│   │   ├── 推理区: Min-10% 宽容评估
│   │   └── 动作区: Bottom-K 零容忍评估
│   │
│   ├── 4f. 接受/继续决策 (make_accept_decision)
│   │   ├── decision_score ≥ prob_th + think_sufficient → ACCEPT
│   │   ├── consecutive_same_count ≥ threshold → ACCEPT
│   │   └── 否则 → CONTINUE
│   │
│   ├── 4g. [ACCEPT] 返回答案
│   │
│   └── 4h. [CONTINUE] 继续思考
│       ├── apply_clean_context() → 丢弃 induced 部分
│       ├── 计算追加思考量 (think_stride)
│       ├── 安全门检查
│       └── API 调用: 继续生成思考 token
│
├── 5. 循环耗尽 → 最终回退
│   └── 30000 token 上限内生成最终答案 (max_tokens_answer=30000)
│
└── 6. 格式化返回结果
    └── { content, confidence, perplexity, value_min_confidence,
          accept_reason, thinking_rounds, api_call_count, task_type, ... }
```

### 诱导 Prompt (make_induced_prompt) 行为详解

`context_control.py` 的 `make_induced_prompt()` 生成诱导提示，引导模型输出答案。其行为取决于思考标签状态：

| 条件 | 行为 | 代码位置 |
|------|------|---------|
| **思考标签已配对** | `induced_prompt = prompt`（直接使用原 prompt，无需复制） | `budget_forcing.py` L480 |
| **思考标签未配对** | `induced_prompt = make_induced_prompt(prompt)`（复制 + 闭合标签 + 诱导语） | `budget_forcing.py` L498 |

**诱导语内容**：`"... \n OK, I have completed my thinking and can now provide the final answer.\n</think>\n"`

**关键设计**：
- 当 `is_think_tag_paired(prompt)=True` 时，模型已自然完成思考（闭标签已存在），直接在原 prompt 上请求答案即可，无需复制。此时 `induced_prompt = prompt`。
- 当 `is_think_tag_paired(prompt)=False` 时，模型仍在思考中（闭标签未出现），需要构造副本：保留当前思考 → 追加省略号 + 诱导语 + 闭合标签。此时 `induced_prompt = make_induced_prompt(prompt)`。
- `enable_clean_context=True` 时，继续推理丢弃 induced 部分，保留原始 prompt。

### 软截断 (enable_soft_truncation) 详解

当 `enable_soft_truncation=True`（默认），`_truncate_thinking_at_natural_breakpoint()` 在**所有截断点**生效——任何因 `stop_reason='length'` 导致思考内容被截断的场合，都在自然断点处闭合思考标签：

| 截断位置 | 代码位置 | 说明 |
|---------|---------|------|
| 初始思考生成 | L431-433 | `check_length` 限制导致首次思考被截断 |
| 标签配对后 min_think_tokens 门控继续思考 | L471-472 | `reopen_think_tag()` 后追加思考被截断 |
| 标签未配对 min_think_tokens 门控继续思考 | L491-492 | 继续思考被截断 |
| stride 追加思考 | L608-609 | `think_stride` 限制导致追加思考被截断 |
| ACCEPT 后最终答案生成 | L561-563 | 接受答案后追加完整答案时截断 |
| 循环耗尽最终答案生成 | L657-659 | 最终兜底答案追加时截断 |

**自然断点字符**：`。` `！` `？` `.` `!` `?` `\n` `}` `】` `」`

**截断逻辑**：从内容末尾向前搜索 500 字符范围内最近的自然断点，在该断点后截断并闭合 `</think>` 标签。如果找不到自然断点，保留原文不截断。

**重要**：软截断**绝不截断答案**——仅在 `_THINK_CLOSE_TAG` 不存在时才执行（说明推理还在进行中被 max_tokens 截断）。如果 `</think>` 已存在，说明推理已完整结束，答案跟在后面，不截断。

### 决策评分优先级

`compute_decision_score()` / `compute_dual_channel_score()` 按以下优先级计算决策分数：

```
1. 完整链 AND 门 (enable_full_chain_confidence=True)
   → min(思维链 Min-10%, 答案 Bottom-K)
   → 最严格：推理区和动作区都必须通过

2. 双通道 (enable_dual_channel=True)
   → min(推理区 Min-percentile, 动作区 Bottom-K)
   → 推理区宽容 + 动作区零容忍

3. 单通道 Value Min (enable_value_min_confidence=True, 默认)
   → 答案区 Bottom-K value token logprob 均值
   → 核心创新：过滤结构 token

4. 默认平均置信度 (以上均未启用)
   → 平均 exp(logprobs)
   → 最宽松
```

### 接受决策逻辑

`make_accept_decision()` 的判断优先级：

| 优先级 | 条件 | 决策 | accept_reason |
|--------|------|------|---------------|
| 1 | `decision_score ≥ prob_th` AND `think_sufficient` | **ACCEPT** | `value_min_conf_XX` / `confidence_above_threshold` 等 |
| 2 | `decision_score ≥ prob_th` BUT `!think_sufficient` | CONTINUE | `conf_ok_think_insufficient_XX_need_YY` |
| 3 | 低分 + full_chain/dual_channel 诊断 | CONTINUE | `reasoning_zone_low_XX` / `action_zone_low_XX` |
| 4 | 思考标签配对 + think_sufficient + is_final | **ACCEPT** | `think_tag_paired_final_accept` |
| 5 | `consecutive_same_count ≥ consecutive_same_stop` + think_sufficient | **ACCEPT** | `consecutive_same_N` |

---

## 答案完整性保护原则

BudgetForcing 的核心设计原则：**思考可以截断，答案必须完整**。

| 阶段 | Token 预算 | 说明 |
|------|-----------|------|
| 思考生成 | 受 budget 控制 | `check_length` + `think_stride × round` |
| 试探答案 | `check_answer_len` | 每轮试探，必须足够写完完整 JSON (CONFIG 默认 4000) |
| 最终答案 | `30000` | **在 32768 上下文限制内给足 token**，确保 JSON 完整闭合 |

这意味着：
- 思考内容可能被截断在自然断点（句号、换行、JSON 闭合括号）
- 试探答案需要足够 token 写完完整 JSON，否则该轮置信度评估无意义
- 最终答案生成**永远不会被截断**，避免生产环境中出现不完整的 JSON

---

## 数据流

### 评估模式数据流

```
Excel (.xlsx) → pandas 加载 → (问题, 参考答案) 对
  → runner._evaluate_one()
    → scene_classifier → task_type_budget → history_module (查询历史)
    → think_budget.call_model() → budget_forcing.call_model()
      → model_client → Completions API → 原始响应
      → 置信度评估 → 早停决策 → 最终答案
    → answer_extractor → score.py / llm_judge.py → 评分
    → history_module.record_inference() (记录历史, 含 task_type)
  → 结果汇总 → results/*.json
```

### API 服务器数据流

```
HTTP POST /v1/chat/completions
  → FastAPI → ChatCompletionRequest
  → 并发控制 (asyncio.Semaphore)
  → asyncio.to_thread(budget_call_model)
    → think_budget.call_model() (同上)
  → parse_think_content() → 分离思考/答案
  → compute_thinking_tokens() → Qwen tokenizer 计数
  → 返回 OpenAI 格式响应 + budget_info
```

### 历史记录流

```
推理完成
  → runner._evaluate_one() → record_inference(task_type=classified_type)
    → history_module.py → JSONL 追加写入
      → trajectory_logs/*.jsonl

历史查询
  → history_budget._get_task_type_stats(task_type, scene_type)
    → 读取 JSONL → 按 task_type 过滤 → 计算 avg/p75/success_rate
  → API 服务器 → get_category_stats(prompt, task_type, scene_type)
    → 同上
```

---

