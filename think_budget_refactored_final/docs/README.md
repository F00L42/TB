# Think Budget Refactored — 项目完整说明

## 一、项目简介

**Think Budget Refactored** 是一个 Python 研究框架，实现了 **BudgetForcing** — 一种自适应思考预算机制，用于控制大语言模型 (LLM) 在推理过程中消耗的 token 数量。

传统 LLM 推理中，模型自由生成思考内容，无法控制推理深度与 token 消耗。BudgetForcing 通过**置信度门控早停**机制，在模型对答案足够自信时提前终止思考，在保证准确率的同时显著降低推理成本（延迟降低 55%，思考 token 降低 76%）。

### 核心创新：Value Min Confidence (P3.1)

**Value Min Confidence** 是本项目的核心创新点。在 LLM 生成的结构化答案（如 JSON）中，存在大量**结构 token**（花括号 `{}`、冒号 `:`、键名如 `"tool"`, `"IMSI"` 等），这些 token 的 logprob 天然极高（>0.99），会掩盖值 token（如参数值、诊断结论）的低置信度。

P3.1 的做法是：

1. **过滤结构 token** — 识别并排除 JSON 语法 token 和任务特定键名
2. **只看 Value token** — 仅对值 token 的 logprob 取最低 K 个求均值
3. **零容忍策略** — 值 token 中任何一个低置信度都会拉低整体评分，确保关键信息的可靠性

这一机制使得置信度评估真正反映了模型对**实际内容**的信心，而非对**语法结构**的信心。

---

## 二、评估场景：NAIE 工具调用准确率

NAIE (Network AI Evaluation) 评估 LLM 在电信运维场景下的工具调用能力：

- **任务**：选择正确的工具 + 填充正确的参数
- **数据集**：Excel `.xlsx` 文件（包含：问题、参考答案、二级能力等字段）
- **评分**：JSON 结构匹配 + LLM Judge 辅助评判
- **生产部署**：提供 FastAPI 服务器，兼容 OpenAI API 格式

### Baseline vs Budget 对比

| 维度 | Baseline（基线） | Budget（预算） |
|------|-----------------|---------------|
| **推理模式** | 自由生成，不截断思考 | BudgetForcing 循环，置信度门控早停 |
| **Token 消耗** | 无上限，模型自行决定 | 受预算控制，达到置信度阈值即停止 |
| **调用方式** | 单次 API 调用 (`enable_confidence=False`) | 多轮迭代：初始思考 → 试探答案 → 置信度评估 → 继续/接受 |
| **适用场景** | 基准性能测量 | 生产环境成本控制 |

---

## 三、系统架构

### 3.1 项目结构

```
think_budget_refactored/
│
├── run_baseline.py              # 基线评估入口
├── run_budget.py                # BudgetForcing 评估入口（含全部 CLI 参数）
├── run_per_dataset.py           # 按数据集评估入口
├── run_compare.py               # 基线 vs Budget 对比报告
├── run_ablation.py              # 消融实验
├── run_one.py                   # 单题调试（详细诊断输出）
├── requirements.txt             # 依赖列表
├── CLAUDE.md                    # Claude Code 指引
│
├── naie/                        # 所有项目代码（统一目录）
│   ├── api_server.py            # FastAPI 服务器（生产版本，兼容 OpenAI 格式）
│   ├── config/
│   │   ├── settings.py          # CONFIG 统一配置中心（所有超参数）
│   │   ├── budget_config.yaml   # 统一预算配置文件（YAML 格式，人类可读）
│   │   └── unified_config.py    # YAML 加载器 + 枚举 + 计算函数
│   ├── evaluation/
│   │   ├── runner.py            # 评估编排器（基线/Budget 评估逻辑）
│   │   ├── parallel_runner.py   # 并行评估支持
│   │   ├── trajectory_logger.py # 推理轨迹记录
│   │   └── analyzer.py          # 结果分析器
│   ├── inference/
│   │   └── think_budget.py      # CONFIG → call_model 参数适配层
│   ├── models/
│   │   ├── budget_forcing.py    # 核心 BudgetForcing 循环
│   │   ├── confidence.py        # 置信度/困惑度计算
│   │   ├── model_client.py      # Completions API 客户端（连接池）
│   │   └── config.py            # MODEL_CONFIGS
│   ├── optimizations/           # 所有优化模块（PEP 562 懒加载 __init__）
│   │   ├── _struct_token_core.py  # 结构 token 核心逻辑（零依赖）
│   │   ├── token_filter.py      # P3.1 Value Min Confidence
│   │   ├── confidence_gates.py  # 决策分数计算
│   │   ├── early_stopping.py    # 接受/继续决策
│   │   ├── context_control.py   # P3.3 Clean Context
│   │   ├── safety_gate.py       # 安全门（超时回退）
│   │   ├── complexity_analyzer.py  # P4 复杂度分析
│   │   ├── difficulty_estimator.py # P5 难度估计器（14 维特征融合）
│   │   ├── trial_evaluator.py   # P6 DEER trial 评估
│   │   ├── scene_classifier.py  # 场景分类器（8 种场景）
│   │   ├── task_type_budget.py  # 10 种任务类型分类 + Token 预算
│   │   ├── history_budget.py    # 历史驱动预算（冷/暖/热启动）
│   │   └── history_module.py    # 推理历史记录与查询 + 滑动窗口 + 统计缓存 + 索引
│   ├── scoring/                 # 评分计算 + LLM judge
│   │   ├── json_utils.py        # JSON 解析
│   │   ├── answer_extractor.py  # 答案提取
│   │   ├── score.py             # 评分计算
│   │   └── llm_judge.py         # LLM 辅助评判
│   └── prompts/                 # Prompt 模板
│       ├── templates.py         # Prompt 模板
│       └── builder.py           # Prompt 构建器
│
├── tests/                       # 所有测试文件
│   ├── test_single_case.py      # 单题 API 测试
│   ├── test_budget_features.py  # 综合功能测试（无需 API）
│   ├── test_mock_budget.py      # Mock 单元测试
│   ├── test_refactoring.py      # 重构验证测试
│   ├── test_new_optimizations.py # 难度/试答案/复杂度测试
│   └── test_budget_system.py    # 预算系统 pytest 测试（YAML配置、温/热启动、注册）
│
├── data/naie/                   # 数据目录
│   ├── dataset/                 # 评估数据集（.xlsx）
│   ├── dataset_slow_thinking/   # 慢思考模式数据集
│   ├── tool_prompt/             # 工具 Prompt 模板
│   ├── Qwen3.6-27B/             # Qwen 分词器（Token 计数用）
│   ├── trajectory_logs/         # 推理轨迹日志（.jsonl）
│   ├── results/                 # 评估结果（.json）
│   ├── results_ablation/        # 消融实验结果
│   ├── results_per_dataset/     # 按数据集评估结果
│   └── custom_task_types.json   # 自定义任务类型配置
│
└── docs/                        # 文档目录
    ├── README.md                # 本文件 — 项目完整说明
    ├── architecture.md          # 架构详解
    ├── confidence_estimation.md # ★ 置信度估计详解（核心文档）
    ├── configuration_guide.md   # 配置参数参考
    ├── api_server_guide.md      # API 服务指南
    └── evaluation_guide.md      # 评估运行指南
```

### 3.2 三层调用链

系统的调用路径分为三层，从入口脚本到最终模型调用：

```
入口脚本 (run_baseline.py, run_budget.py, run_one.py, api_server.py)
  → naie/evaluation/runner.py         # evaluate_baseline(), evaluate_budget(), _evaluate_one()
    → naie/inference/think_budget.py   # call_model() — CONFIG→params 适配器 (_cfg_to_params)
      → naie/models/budget_forcing.py  # call_model() — 核心 BudgetForcing 循环
        → naie/models/model_client.py  # model_client — Completions API 客户端 + 连接池
        → naie/optimizations/*          # 所有优化模块
```

### 3.3 各层职责

| 层级 | 模块 | 职责 |
|------|------|------|
| **入口层** | `run_*.py`, `api_server.py` | CLI 参数解析、数据集加载、结果保存、API 服务器 |
| **编排层** | `naie/evaluation/runner.py` | 评估流程编排：重试逻辑、评分计算、历史记录、场景/任务类型分类 |
| **适配层** | `naie/inference/think_budget.py` | CONFIG → `call_model()` 参数映射 (`_cfg_to_params`)、NAIE 键名注入 |
| **核心层** | `naie/models/budget_forcing.py` | BudgetForcing 主循环、预算计算、置信度评估、早停决策 |
| **基础设施层** | `model_client.py`, `optimizations/` | API 调用、置信度计算、token 过滤、历史查询等 |

---

## 四、核心机制详解

### 4.1 BudgetForcing 主循环

`naie/models/budget_forcing.py` 的 `call_model()` 是系统核心，实现 BudgetForcing 推理循环：

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
│   └── API 调用: prompt + think_tag → 生成 check_length 个思考 token
│
├── 4. 循环: for round in range(num_thinking):
│   │
│   ├── 4a. 思考标签配对检测 + min_think_tokens 门控
│   │   ├── 检测开/闭标签是否配对
│   │   └── actual_len < min_think_tokens → 不接受，继续思考
│   │
│   ├── 4b. 生成诱导 Prompt (make_induced_prompt)
│   │   ├── 思考标签已配对: induced_prompt = prompt (直接使用)
│   │   └── 思考标签未配对: 复制 → 闭合标签 → 追加诱导语
│   │
│   ├── 4c. 试探答案生成
│   │   └── API 调用: induced_prompt → 生成 check_answer_len 个 token
│   │
│   ├── 4d. 置信度/困惑度计算
│   │   ├── confidence.py: 平均 exp(logprobs)
│   │   ├── token_filter.py: Value Min Confidence (P3.1)
│   │   └── confidence_gates.py: compute_decision_score()
│   │
│   ├── 4e. 接受/继续决策 (make_accept_decision)
│   │   ├── decision_score ≥ prob_th + think_sufficient → ACCEPT
│   │   ├── consecutive_same_count ≥ threshold → ACCEPT
│   │   └── 否则 → CONTINUE
│   │
│   ├── 4f. [ACCEPT] 返回答案
│   │
│   └── 4g. [CONTINUE] 继续思考
│       ├── apply_clean_context() → 丢弃 induced 部分
│       ├── 计算追加思考量 (think_stride)
│       ├── 安全门检查
│       └── API 调用: 继续生成思考 token
│           └── (soft_truncation: 截断时在自然断点闭合思考标签)
│
├── 5. 循环耗尽 → 最终回退
│   └── 从 answer_list 中选最高 confidence 的答案
│
└── 6. 格式化返回结果
    └── { content, confidence, perplexity, value_min_confidence,
          accept_reason, thinking_rounds, api_call_count, task_type, ... }
```

### 4.2 Value Min Confidence (P3.1) — 核心创新

#### 4.2.1 问题：结构 Token 掩盖 Value 不确定性

LLM 输出的 JSON 答案中，结构 token 与 Value token 的 logprob 分布截然不同：

| Token 类型 | 示例 | 典型 logprob | 典型 exp(prob) |
|-----------|------|-------------|---------------|
| JSON 语法 | `{`, `}`, `:`, `,` | -0.001 ~ -0.01 | 0.99 ~ 0.999 |
| JSON 键名 | `"faultNumber"`, `"IMSI"` | -0.01 ~ -0.05 | 0.95 ~ 0.99 |
| **Value token** | `13800138000`, `故障` | -0.5 ~ -3.0 | 0.05 ~ 0.6 |

如果将所有 token 的 logprob 平均，结构 token 的高置信度会**掩盖** Value token 的低置信度，导致模型对实际答案内容不确定时也被判定为"自信"。

#### 4.2.2 六步计算流程

```
Step 1: extract_value_logprobs(logprobs, tokens)
        → 过滤结构 token, 只保留 Value token

Step 2: value_lps.sort()
        → 最低 logprob 排在最前面

Step 3: k = min(bottom_k, len(value_lps))
        → 默认 K=5, 取最低 5 个 Value logprob

Step 4: value_min_raw = sum(value_lps[:k]) / k
        → logprob 域的 Bottom-K 均值

Step 5: decision_score = min(exp(value_min_raw), 1.0)
        → 概率域 [0, 1]

Step 6: decision_score >= prob_th ?
        → YES: 接受答案  NO: 继续推理
```

#### 4.2.3 结构 Token 判断逻辑

`naie/optimizations/_struct_token_core.py` 是整个结构化 Token 判断的**唯一权威实现**，无任何项目内导入，打破循环依赖：

```python
def is_structural_token(token: str) -> bool:
    if not token:                              # 空字符串 → 结构
        return True
    if token in _struct_tokens:                # 默认结构 token 集 → 结构
        return True
    if _WHITESPACE_RE.match(token):            # 纯空白 → 结构
        return True
    if token in _SPECIAL_TOKENS:               # 特殊标记 → 结构
        return True
    cleaned = token.strip().lower()
    if cleaned in _key_names:                  # 任务键名 (注入) → 结构
        return True
    if len(cleaned) <= 1 and cleaned in _SINGLE_PUNCT:  # 单字符标点 → 结构
        return True
    return False                               # 其他 → Value token
```

**结构 token 分类**：

| 子类 | 示例 | 说明 |
|------|------|------|
| JSON 语法 | `{`, `}`, `[`, `]`, `"`, `'`, `:`, `,` | JSON 骨架，模型几乎 100% 确定 |
| 代码结构符 | `=`, `;`, `!`, `#`, `%`, `@` | 编程语言高频结构符 |
| 数学/逻辑符 | `+`, `-`, `*`, `/`, `>`, `<`, `→` | 数学推理中的固定符号 |
| 任务键名 | `faultNumber`, `IMSI`, `tool` | JSON 键名，通过 `set_task_key_names()` 注入 |
| 空白符 | 空格、换行、制表符 | 不携带内容信息 |
| 特殊标记 | `<|im_start|>`, `<|im_end|>` | 模型内部格式标记 |
| 单字符标点 | `.`, `_`, `(`, `)` | 长度 ≤ 1 的纯标点字符 |

**任务键名注入**：NAIE 评估中的 JSON 答案包含大量领域特有键名，通过 `naie/inference/think_budget.py` 在模块加载时注入：

```python
_NAIE_KEY_NAMES = {
    'tool', 'faultNumber', 'faultIMSI', 'faultPort', 'faultType',
    'fault', 'Number', 'IMSI', 'Url', 'Ip', 'APN', 'Time', 'Range',
    'Caller', 'result', 'answer', 'name', 'type', 'value', ...
}
set_task_key_names(_NAIE_KEY_NAMES)
```

#### 4.2.4 数值示例

输入答案 `{"faultNumber": 19, "Ip": "192.168.1.100"}`：

- **Value token logprobs**（过滤后）: `19`(-1.5), `192`(-0.8), `168`(-1.2), `1`(-0.5), `100`(-2.8)
- **排序后**: [-2.8, -1.5, -1.2, -0.8, -0.5]
- **Bottom-5 均值**: (-2.8 + -1.5 + -1.2 + -0.8 + -0.5) / 5 = -1.36
- **exp(-1.36) = 0.257** → **0.257 < 0.95** → **CONTINUE**（继续推理）

对比：如果不过滤结构 token，全局平均 ≈ 0.76 → 看似"中等自信" → 可能被误判。

### 4.3 置信度评估模式

系统支持 4 种置信度评估模式，按优先级从高到低：

| 模式 | 触发条件 | 计算方式 | 状态 |
|------|---------|---------|------|
| **完整链 AND 门** | `enable_full_chain_confidence=True` | min(思维链 Min-10%, 答案 Bottom-K) | 实验性 |
| **双通道** | `enable_dual_channel=True` | min(推理区 Min-10%, 动作区 Bottom-K) | 实验性 |
| **单通道 P3.1** | `enable_value_min_confidence=True` | 答案 Bottom-K value token logprob 均值 | **✅ 生产默认** |
| **默认平均** | 以上均不满足 | 平均 exp(logprobs) | 基线 |

#### 推理区 vs 答案区的评估策略差异

- **答案区**：零容忍 — Bottom-K 取最低 K 个 Value token，一个参数值低置信 = 整体低分
- **推理区**：宽容 — Min-Percentile 取最差 10% Value token 均值，允许偶尔连接词低置信

#### AND 门的工作原理

双通道/完整链使用 `min(reasoning_score, action_score)` 聚合：

| 场景 | reasoning | action | AND 门 | 判定 |
|------|-----------|--------|--------|------|
| 推理正确 + 参数确信 | 0.97 | 0.98 | 0.97 | ✅ 接受 |
| 推理正确 + 参数不确定 | 0.96 | 0.40 | 0.40 | ❌ 继续 |
| 推理断层 + 参数确信 | 0.30 | 0.97 | 0.30 | ❌ 继续 |
| 推理断层 + 参数不确定 | 0.30 | 0.40 | 0.30 | ❌ 继续 |

### 4.4 早停决策逻辑

`naie/optimizations/early_stopping.py` 的 `make_accept_decision()` 判断优先级：

| 优先级 | 条件 | 决策 | accept_reason |
|--------|------|------|---------------|
| 1 | `decision_score ≥ prob_th` AND `think_sufficient` | **ACCEPT** | `value_min_conf_XX` / `confidence_above_threshold` 等 |
| 2 | `decision_score ≥ prob_th` BUT `!think_sufficient` | CONTINUE | `conf_ok_think_insufficient_XX_need_YY` |
| 3 | 低分 + 双通道诊断 | CONTINUE | `reasoning_zone_low_XX` / `action_zone_low_XX` |
| 4 | 思考标签配对 + think_sufficient + is_final | **ACCEPT** | `think_tag_paired_final_accept` |
| 5 | `consecutive_same_count ≥ threshold` + think_sufficient | **ACCEPT** | `consecutive_same_N` |

#### min_think_tokens 门控

即使置信度达标，如果模型思考的 token 数不足 `min_think_tokens`（默认 100），也不会接受答案。这防止模型"敷衍"——生成少量思考后立即闭合标签给出答案。

双重门控实现：
1. `make_accept_decision()` 中的软门控：置信度达标但思考不足 → CONTINUE
2. `budget_forcing.py` 中的硬门控：标签配对 + 思考不足 → `reopen_think_tag()` 强制继续

### 4.5 Clean Context 机制 (P3.3)

当置信度不足需要继续推理时，诱导语和半成品 JSON 会粘在 prompt 上，污染下一轮思考。Clean Context 通过**副本机制**解决：

```python
# context_control.py
# 思考标签已配对时: induced_prompt = prompt (无需复制)
# 思考标签未配对时: induced_prompt = make_induced_prompt(prompt)  # 副本 = prompt + 诱导语 + 闭标签
# 在副本上试答案 → 如果接受, 用副本; 如果继续, 用原始 prompt (丢弃副本)
```

- `enable_clean_context=True`（默认）：继续推理时丢弃 induced 部分，不累积试探答案
- `enable_clean_context=False`：旧逻辑，诱导语粘上原始 prompt

### 4.6 安全门

BudgetForcing 循环可能失控（模型持续低置信，反复追加思考）。安全门提供双重保护：

- **Soft gate**：`elapsed > baseline_time_limit` → 记录警告
- **Hard gate**：`elapsed > 1.5 × baseline_time_limit` → 强制从已有 answer_list 中选最高分答案返回

### 4.7 答案完整性保护

**核心原则**：思考可以截断，答案必须完整。

| 阶段 | Token 预算 | 说明 |
|------|-----------|------|
| 思考生成 | 受 budget 控制 | `check_length` + `think_stride × round` |
| 试探答案 | `check_answer_len` (CONFIG=4000) | 每轮试探，必须足够写完完整 JSON |
| 最终答案 | `65536` (无限制) | **始终给最大 token**，确保 JSON 完整闭合 |

### 4.8 软截断 (Soft Truncation)

`enable_soft_truncation=True`（默认）时，`_truncate_thinking_at_natural_breakpoint()` 在**所有 6 个截断点**生效——任何因 `stop_reason='length'` 导致思考内容被截断的场合，都在自然断点（`。！？.!?`、换行、JSON 闭合括号）处闭合思考标签。

**绝不截断答案**：仅在 `_THINK_CLOSE_TAG` 不存在时执行（说明推理仍在进行中被截断）。如果闭标签已存在，说明推理已完整结束，答案跟在后面，不截断。

---

## 五、预算控制机制

### 5.1 统一预算优先级链

当 `call_model()` 被调用时，预算按以下优先级计算（**最高优先级在前**）：

```
┌─────────────────────────────────────────────────────────────┐
│ Step 0:   BUDGET_ENABLED = False                            │  ← 总开关
│           → 关闭所有预算控制，模型自由生成                      │
├─────────────────────────────────────────────────────────────┤
│ Step 0.5: FIXED_BUDGET_TOKEN > 0                            │  ← 统一预算配置
│           → 覆盖 fixed_budget_tokens (如果后者=0)             │
├─────────────────────────────────────────────────────────────┤
│ 1. fixed_budget_tokens > 0                                  │  ← 最高优先
│    → 固定覆盖，跳过所有动态计算                               │
├─────────────────────────────────────────────────────────────┤
│ 2. task_type_override 非空                                   │
│    → 强制使用指定任务类型的预算（未知类型自动注册）              │
├─────────────────────────────────────────────────────────────┤
│ 3. enable_task_type_budget = True                            │
│    → 按分类结果设置每类型 token 预算（未知类型自动注册）         │
├─────────────────────────────────────────────────────────────┤
│ 4. enable_difficulty_estimator = True                        │
│    → 14 维特征融合 → difficulty∈[0,1] → 分段预算映射          │
├─────────────────────────────────────────────────────────────┤
│ 5. enable_history_budget = True                              │
│    → 冷/暖/热启动模式，历史轨迹驱动预算                        │
│    → difficulty_estimator 启用时仅覆盖 think_stride           │
├─────────────────────────────────────────────────────────────┤
│ 6. CONFIG 默认值                                             │
│    → 使用原始 CONFIG 值                                      │
├─────────────────────────────────────────────────────────────┤
│ 7. 固定子参数 (fixed_num_thinking 等)                        │  ← 最后覆盖
│    → 无论预算链如何计算，覆盖特定子参数值                       │
├─────────────────────────────────────────────────────────────┤
│ 8. CONFIDENCE_THRESHOLD / PERCENTILE_STEP                    │  ← 统一预算配置
│    → 覆盖 prob_th / history_stride_percentile                 │
└─────────────────────────────────────────────────────────────┘
```

**优先级说明**：
- **Step 0** `BUDGET_ENABLED`：统一总开关，`False` 时关闭所有预算控制
- **Step 0.5** `FIXED_BUDGET_TOKEN`：统一预算配置，在 `fixed_budget_tokens=0` 时覆盖
- **第 1 级** `fixed_budget_tokens`：一旦设置 (>0)，直接作为 think 预算上限，**跳过所有后续动态计算**
- **第 2 级** `task_type_override`：手动指定任务类型字符串，未知类型自动动态注册
- **第 3-6 级**：动态计算路径，按优先级依次尝试
- **difficulty_estimator 与 history_budget 交互**：difficulty_estimator 优先；启用时 history_budget 仅覆盖 `think_stride`，不覆盖 `check_length`/`num_thinking`
- **第 7 级** CONFIG 默认值：兜底
- **第 8 级** 固定子参数：**独立于预算链**，无论链如何计算，在最后覆盖对应值
- **第 9 级** `CONFIDENCE_THRESHOLD`/`PERCENTILE_STEP`：统一预算配置的最终覆盖

**注意**：不支持在线动态预算调整。所有预算参数在 `call_model()` 入口一次性计算，循环内不再修改。

### 5.2 任务类型分类与 Token 预算

`naie/optimizations/task_type_budget.py` 将问题分类为 **10 种任务类型**，每种类型有独立的 token 预算参数：

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

#### 分类流程

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
    └─ Step 5: 获取该类型的 budget 参数
```

#### 自定义任务类型

支持两种扩展方式：
1. **JSON 配置文件** — 通过 `custom_task_types_path` 指定
2. **运行时注册** — `register_custom_task_type()` 动态添加新类型，自动写入 `custom_task_types.json`

### 5.3 历史驱动预算三阶段

`naie/optimizations/history_budget.py` 基于历史推理记录动态调整 token 预算：

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

| 阶段 | 条件 | 预算计算 | 调整策略 |
|------|------|---------|---------|
| **冷启动/无限** | 0 条历史 + `new_task_unlimited=True` | 无限制 | 使用 task_type 默认的其他参数 |
| **冷启动/受限** | 0 条历史 + `new_task_unlimited=False` | `typical_tokens` | — |
| **温启动** | 1~warmup_count 条 | `avg_tokens × effective_multiplier` | **方差感知**：高 CV (>0.5) → 乘数×1.1~1.3；低 CV (<0.3) → 乘数×0.9 |
| **热启动** | >warmup_count 条 | `p75_tokens` | **成功率调整**：success_rate<0.7 → ×1.3；failure_rate>0.3 → stride×0.8 |

#### 温启动策略对比

| 策略 | 基础乘数 | 高方差乘数 | 特点 |
|------|---------|-----------|------|
| `conservative` | 1.5x | 1.95x | 保守，预算充裕 |
| `moderate` | 1.2x | 1.32x | 适中（默认） |
| `aggressive` | 1.0x | 1.1x | 激进，预算紧凑 |

#### 新任务泛化流程

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

### 5.4 难度估计器 (P5)

`naie/optimizations/difficulty_estimator.py` 使用 14 维连续特征融合输出 difficulty∈[0,1]，映射到分段预算：

- **特征**：问题长度、关键词密度、中文比例、数字密度、JSON 嵌套深度等
- **映射公式**：`param = base + difficulty × (max - base)`
- **难度等级乘数**：easy(0.8x) / medium(1.0x) / hard(1.5x) / very_hard(2.0x)，由 `budget_config.yaml` 的 `DIFFICULTY_TIERS` 控制

### 5.5 场景分类器

`naie/optimizations/scene_classifier.py` 将问题分类为 **8 种商用场景**，每种场景有 `typical_tokens` 先验，辅助任务类型分类和预算推算。

---

## 六、CONFIG 配置体系

### 6.1 配置结构

所有超参数集中定义在 `naie/config/settings.py` 的 `CONFIG` 字典中，格式为 `(value, description)` 元组：

```python
CONFIG = {
    "enable_budget": (True, "BudgetForcing总开关"),
    "check_length":  (200,  "初始思考token数"),
    "prob_th":       (0.95, "置信度阈值"),
    ...
}
THINK_BUDGET_CONFIG_47 = {k: v[0] for k, v in CONFIG.items()}
```

### 6.2 参数优先级

```
CLI 参数 > request body (API) > CONFIG 默认值
```

### 6.3 CONFIG → call_model 参数映射

`naie/inference/think_budget.py::_cfg_to_params()` 将 CONFIG 键映射到 `budget_forcing.call_model()` 的参数名。**映射不总是 1:1**：

| CONFIG 键 | call_model 参数 | 映射规则 |
|-----------|----------------|---------|
| `enable_budget` | `enable_confidence` | 名称不同，语义相同 |
| `enable_consecutive_same_stop=False` | `consecutive_same_stop=999` | 布尔开关转为永不触发的阈值 |
| `model_name` | `model_name` | 始终强制为 `mg2hg` |
| — | `stop_condition` | 固定为 `'length'` |

### 6.4 统一配置文件

预算相关配置已集中到：
- **YAML**: `naie/config/budget_config.yaml` — 人类可读的完整配置
- **Python**: `naie/config/unified_config.py` — YAML 加载器 + 枚举 + 计算函数
- **CONFIG**: `naie/config/settings.py` — 运行时覆盖接口（保持向后兼容）

### 6.5 当前 CONFIG 默认值

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enable_budget` | True | BudgetForcing 总开关 |
| `check_length` | 200 | 初始思考 token 数 |
| `think_stride` | 100 | 每轮思考增量 token |
| `prob_th` | 0.95 | 置信度阈值 |
| `num_thinking` | 10 | 最大尝试轮次 |
| `check_answer_len` | 4000 | 试探答案长度 |
| `max_tokens` | 65536 | 最大总 token |
| `min_think_tokens` | 100 | 最低思考 token |
| `max_api_calls` | 20 | 单题最大 API 调用次数 |
| `enable_value_min_confidence` | True | P3.1 核心创新 |
| `value_min_bottom_k` | 5 | Bottom-K 取值数 |
| `enable_clean_context` | True | Clean Context |
| `enable_task_type_budget` | True | 任务类型预算 |
| `enable_history_budget` | True | 历史轨迹预算 |
| `enable_safety_gate` | False | 安全门（默认关闭） |
| `baseline_time_limit` | 90.0 | 安全门基线延迟（秒） |
| `enable_soft_truncation` | True | 软截断 |
| `new_task_unlimited` | True | 新任务不设限制 |
| `history_warmup_strategy` | moderate | 温启动策略 |

P4-P6 实验性功能默认 OFF。

---

## 七、优化模块概览

| 模块 | 标签 | 状态 | 角色 |
|------|------|------|------|
| `_struct_token_core.py` | — | 底层 | 结构 token 核心逻辑（零项目依赖，打破循环导入） |
| `token_filter.py` | P3.1 | ✅ 启用 | 结构 token 过滤 + Value Min Confidence 计算 |
| `confidence_gates.py` | P3.1 | ✅ 启用 | `compute_decision_score()` / `compute_dual_channel_score()` |
| `early_stopping.py` | P2.2 | ✅ 启用 | `make_accept_decision()`, 连续相同答案检测 |
| `context_control.py` | P3.3 | ✅ 启用 | `make_induced_prompt()` (复制机制), `apply_clean_context()` |
| `safety_gate.py` | — | ⚙️ 可配 | 超时回退 + 软截断回退 |
| `complexity_analyzer.py` | P4 | ⚙️ 可配 | 问题复杂度估计 — 在 budget_forcing.py 中导入使用 |
| `difficulty_estimator.py` | P5 | ⚙️ 可配 | 14 维特征融合 → difficulty∈[0,1] → 分段预算映射 |
| `trial_evaluator.py` | P6 | ⚙️ 可配 | DEER 式 trial answer 稳定性评估 — 在 budget_forcing.py 中导入使用，默认关闭 |
| `scene_classifier.py` | — | ✅ 启用 | 场景分类（8 种场景 + typical_tokens 先验） |
| `task_type_budget.py` | — | ✅ 启用 | 10 种任务类型分类 + 每类型 token 预算 |
| `history_budget.py` | — | ✅ 启用 | 历史驱动预算：冷/暖/热启动 + 新任务检测 + 自动注册 |
| `history_module.py` | — | ✅ 启用 | 记录推理历史到 JSONL，提供按任务类型/场景的统计查询 |

---

## 八、消融实验结果

| 配置 | 准确率 | 延迟变化 | 结论 |
|------|--------|---------|------|
| **基线 (无预算)** | 75-80% | — | 自由生成，无置信度门控 |
| **纯平均置信度 (不过滤)** | ≈0 vs 基线 | -30% | 结构 token 掩盖 Value 不确定性，早停失准 |
| **Value Min + min_think_tokens** | **≈0 vs 基线** | **-55%** | ✅ 核心创新：准确率不降，延迟大幅降低 |
| **双通道** | ≈0 | -50% | 更精细诊断，单通道已足够 |

### 最佳组合效果

| 指标 | Baseline | Budget (最佳组合) | 差异 |
|------|----------|-------------------|------|
| 准确率 | 75% | **80%** | **+5pp** |
| 延迟 | 74.4s | **33.4s** | **-55%** |
| 思考 token | 1746 | 419 | **-76%** |

---

## 九、数据流

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
```

---

## 十、关键约束

- **模型强制为 mg2hg**：任何 `model_name` 参数被静默覆盖，唯一端点 `http://141.73.2.25:87/v1/completions`（Completions API，Chat Completions 返回 404）
- **NO_PROXY 已内置**：所有脚本内部自动设置 `os.environ["NO_PROXY"]` 绕过企业代理
- **sys.path.insert 必须**：所有脚本手动添加项目根到 `sys.path`，无 `setup.py` 或 `pyproject.toml`
- **思考标签**：`confidence.py` 和 `budget_forcing.py` 使用标准 XML 标签 `iesa`/`eiraot` 进行标签配对检测
- **答案完整性**：思考可以截断，但最终答案生成始终给 `max_tokens_answer=65536` 确保 JSON 完整
- **循环导入已解决**：`_struct_token_core.py` 零项目内导入 + `__init__.py` PEP 562 懒加载
- **始终使用 `naie/api_server.py`**：生产版本，含完整 CONFIG 映射、任务类型支持、历史预算集成、并发控制
- **CONFIG 默认值可能不同于 call_model 签名默认值**：`_cfg_to_params()` 从 CONFIG 读取覆盖签名默认值
- **软截断在所有截断点生效**：`enable_soft_truncation=True` 时，6 个位置（初始思考/中间轮次/stride追加/accept追加/兜底追加/最终答案）均调用 `_truncate_thinking_at_natural_breakpoint()`
- **不支持在线动态预算调整**：所有预算参数在 `call_model()` 入口一次性计算完毕，循环内不再修改 budget 参数

---

## 十一、运行指南

### 环境准备

```bash
# 激活虚拟环境 (Python 3.12)
# Windows:
C:\Users\r50058444\Desktop\work\workspace\.venv\Scripts\activate
# Linux/Mac:
source ../.venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 评估命令

```bash
# 快速验证
python tests/test_single_case.py
python tests/test_budget_features.py          # 综合功能测试（无需 API）

# 评估运行
python run_baseline.py --max_cases 5           # 基线评估
python run_budget.py --max_cases 5 --prob_th 0.95  # Budget 评估
python run_per_dataset.py --max_cases 5        # 按数据集评估
python run_compare.py                          # 对比报告
python run_ablation.py --max_cases 5           # 消融实验
python run_one.py                              # 单题调试

# API 服务器
python naie/api_server.py --port 8000
uvicorn naie.api_server:app --host 0.0.0.0 --port 8000
```

### 配置验证

```bash
python -c "import sys;sys.path.insert(0,'.');from naie.config.settings import print_config;print_config()"
python -c "import sys;sys.path.insert(0,'.');from naie.optimizations import *;from naie.optimizations.history_module import get_task_type_stats;print('ALL OK')"
```

---

## 十二、依赖

```
openai, httpx, pandas, tqdm, transformers, python-dotenv,
openpyxl, fastapi, uvicorn, scikit-learn, pyyaml
```

安装：`pip install -r requirements.txt`

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [README.md](README.md) | 项目完整说明（本文件） |
| [architecture.md](architecture.md) | 架构详解：三层调用链、预算优先级链、核心循环流程 |
| [confidence_estimation.md](confidence_estimation.md) | ★ 置信度估计详解：结构 token 过滤、决策评分、早停逻辑 |
| [configuration_guide.md](configuration_guide.md) | 配置参数参考：CONFIG 结构、参数分组、CLI 对照 |
| [api_server_guide.md](api_server_guide.md) | API 服务指南：端点、请求/响应格式、并发控制 |
| [evaluation_guide.md](evaluation_guide.md) | 评估运行指南：基线/Budget/消融/调试命令 |
