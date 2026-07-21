# 评估运行指南 — Evaluation Guide

## 概述

Think Budget Refactored 提供多种评估模式，对比基线（自由生成）与 BudgetForcing（置信度早停）的精度和效率。

### 前置条件

```bash
# 激活虚拟环境 (Python 3.12)
# Windows:
..\.venv\Scripts\activate
# Linux/Mac:
source ../.venv/bin/activate

# 确认 NO_PROXY 已设置（脚本内部自动设置）
# 确认模型端点可达: curl "$env:MG2HG_URL/completions"
```

---

## 基线评估

基线模式：`enable_confidence=False`，模型自由生成不截断，单次 API 调用完成。

```bash
# 运行全部用例
python run_baseline.py

# 限制用例数（快速验证）
python run_baseline.py --max_cases 5

# 指定模型名（实际仍映射为 mg2hg）
python run_baseline.py --model_name mg2hg

# 并行评估
python run_baseline.py --parallel --concurrency 4
```

### CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max_cases N` | 全部 | 最多评估 N 条用例 |
| `--model_name` | `mg2hg` | 模型名（强制映射为 mg2hg） |
| `--parallel` | 关闭 | 启用并行评估 |
| `--concurrency N` | 4 | 并行线程数 |

### 输出

```
Baseline 评估完成:
  总用例: 39, 正确: 31, 准确率: 79.5%
  平均延迟: 12.3s, 总耗时: 480.5s
  平均思考 tokens: 350
  结果已保存: data/naie/results/result_baseline_20260601_120000.json
```

---

## Budget 评估

BudgetForcing 模式：多轮迭代推理，置信度门控早停。

```bash
# 基本运行
python run_budget.py

# 限制用例数 + 自定义阈值
python run_budget.py --max_cases 5 --prob_th 0.95

# 关闭 Value Min Confidence（回退到纯平均置信度）
python run_budget.py --enable_value_min_confidence off

# 开启难度估计器
python run_budget.py --enable_difficulty_estimator on

# 固定预算（确定性测试）
python run_budget.py --fixed_budget_tokens 2000 --fixed_num_thinking 5

# 指定任务类型
python run_budget.py --task_type tool_call

# 并行评估
python run_budget.py --parallel --concurrency 4 --max_cases 20
```

### 完整 CLI 参数

#### 核心参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max_cases N` | 全部 | 最多评估 N 条用例 |
| `--check_length N` | 200 | 初始思考 token 数 |
| `--think_stride N` | 100 | 每轮思考增量 |
| `--num_thinking N` | 10 | 最大尝试轮次 |
| `--prob_th FLOAT` | 0.95 | 置信度阈值 |
| `--min_think_tokens N` | 100 | 最低思考 token |
| `--max_api_calls N` | 20 | 单题最大 API 调用次数 |
| `--baseline_time_limit FLOAT` | 90.0 | 基线延迟限制（秒） |
| `--check_answer_len N` | 4000 | 试探答案长度 |
| `--max_tokens N` | 65536 | 最大总 token |

#### 开关参数（on/off）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--enable_budget on\|off` | on | BudgetForcing 总开关 |
| `--enable_value_min_confidence on\|off` | on | P3.1 Value Min Confidence |
| `--enable_clean_context on\|off` | on | P3.3 Clean Context |
| `--enable_safety_gate on\|off` | off | 安全门 |
| `--enable_soft_truncation on\|off` | on | 软截断 |
| `--enable_difficulty_estimator on\|off` | off | P5 难度估计器 |
| `--enable_deer_trial on\|off` | off | P6 DEER trial |

#### 任务类型参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--enable_task_type_budget on\|off` | on | 任务类型预算开关 |
| `--task_type NAME` | 自动 | 手动覆盖任务类型（映射为 task_type_override） |
| `--default_task_type NAME` | general | 默认任务类型 |
| `--custom_task_types_path PATH` | None | 自定义任务类型配置 |

**可选任务类型**：retrieval / coding / math / reasoning / classification / tool_call / fault_diagnosis / knowledge_qa / planning / general

#### 历史轨迹参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--enable_history_budget on\|off` | on | 历史轨迹预算开关 |
| `--new_task_unlimited on\|off` | on | 新任务不设限制 |
| `--history_warmup_strategy STR` | moderate | warm-start 策略（conservative/moderate/aggressive） |
| `--auto_cluster_threshold N` | 5 | 自动聚类阈值 |
| `--history_budget_multiplier F` | 1.2 | warm-start 乘数 |
| `--history_low_success_boost F` | 1.3 | 低成功率放大 |
| `--history_stride_percentile F` | 0.75 | 步长百分位 |
| `--history_warmup_count N` | 3 | warm-start 条数阈值 |

#### 人工固定参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--fixed_budget_tokens N` | 0 | 固定 budget token（0=动态） |
| `--fixed_num_thinking N` | 0 | 固定最大轮次（0=动态） |
| `--fixed_think_stride N` | 0 | 固定步长（0=动态） |
| `--fixed_check_length N` | 0 | 固定初始思考长度（0=动态） |
| `--fixed_prob_th FLOAT` | 0.0 | 固定置信度阈值（0.0=使用 prob_th） |
| `--fixed_min_think_tokens N` | 0 | 固定最低思考 token（0=动态） |

### 输出

```
Budget 评估完成:
  总用例: 39, 正确: 33, 准确率: 84.6%
  平均延迟: 8.5s, 总耗时: 331.5s
  平均思考 tokens: 180
  采纳原因分布:
    Value最低概率=0.95+: 20 (51.3%)
    连续2次答案相同: 8 (20.5%)
    思考标签自然闭合: 5 (12.8%)
    最终轮强制返回: 6 (15.4%)
  结果已保存: data/naie/results/result_budget_20260601_120500.json
```

---

## 按数据集评估

自动遍历 `data/naie/dataset_slow_thinking/` 下所有 .xlsx 文件，分别运行基线和 BudgetForcing 评估，输出单个 CSV + 汇总 JSON。

```bash
# 对每个数据集分别运行（默认 both 模式：baseline + budget）
python run_per_dataset.py --max_cases 5

# 仅运行 baseline
python run_per_dataset.py --mode baseline --max_cases 5

# 仅运行 budget
python run_per_dataset.py --mode budget --max_cases 5

# 自定义 budget 参数
python run_per_dataset.py --prob_th 0.90 --check_length 300
```

### CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--mode` | `both` | 评估模式：`baseline` / `budget` / `both` |
| `--max_cases N` | 全部 | 每个数据集最多评估 N 条 |
| 其他 | — | 与 `run_budget.py` 相同的 budget 参数 |

### 输出

- **CSV**：`data/naie/results_per_dataset/latency_*.csv`（dataset, id, mode, elapsed_time）
- **JSON 汇总**：`data/naie/results_per_dataset/summary_*.json`
- **控制台表格**：每个数据集的准确率/延迟

---

## 对比报告

读取之前保存的基线和 Budget 结果 JSON 文件，生成对比表格和统计摘要。

```bash
# 运行对比报告（自动查找 data/naie/results/ 下的 baseline 和 budget 结果文件）
python run_compare.py
```

### 输出示例

```
╔══════════════════════════════════════════════════════════╗
║          Baseline vs BudgetForcing 对比报告            ║
╠══════════════════════════════════════════════════════════╣
║  指标          │ Baseline  │ Budget    │ 变化          ║
║  样本数        │ 39        │ 39        │ —             ║
║  准确率        │ 79.5%     │ 84.6%     │ +5.1%         ║
║  平均思考tok   │ 350       │ 180       │ -48.6%        ║
║  平均延迟      │ 12.3s     │ 8.5s      │ -4.0s         ║
╚══════════════════════════════════════════════════════════╝
```

---

## 消融实验

逐个开启/关闭优化模块，对比每个优化的单独贡献。

```bash
# 运行全部 10 个消融实验
python run_ablation.py --max_cases 5

# 跳过 baseline（已运行过）
python run_ablation.py --skip_baseline

# 跳过已完成的实验
python run_ablation.py --skip_completed pure_budget,consecutive_same
```

### 消融实验配置

| # | 名称 | 描述 | 配置 |
|---|------|------|------|
| 0 | `baseline` | Chat API 自然调用（无截断） | enable_budget=False |
| 1 | `pure_budget` | BudgetForcing + 所有优化 OFF | 基础 budget 参数 |
| 2 | `consecutive_same` | +P2.2 连续相同答案早停 | enable_consecutive_same_stop=True |
| 3 | `value_min_conf` | +P3.1 Value 最低概率 | enable_value_min_confidence=True |
| 4 | `clean_context` | P3.3 Clean Context OFF（与 pure_budget 对比） | enable_clean_context=False |
| 5 | `safety_gate` | +安全门超时回退 | enable_safety_gate=True |
| 6 | `all_on` | 所有有效优化启用 | 全部有效 enable_*=True |

**基础 budget 配置**：`check_length=500, prob_th=0.85, num_thinking=6, max_tokens=2000`

### 输出

- **JSON 结果**：`data/naie/results_ablation/ablation_N_name_*.json`
- **汇总 JSON**：`data/naie/results_ablation/ablation_summary.json`
- **控制台表格**：每个实验的准确率/延迟/思考 token

---

## 单题调试

### run_one.py — 详细诊断输出

```bash
# 使用默认问题运行
python run_one.py

# 指定问题
python run_one.py --question "查询IMSI为460001234567890的用户信息"

# 自定义参数
python run_one.py --prob_th 0.90 --num_thinking 5 --check_length 300

# 开启双通道评估
python run_one.py --enable_dual_channel on

# 从数据集加载指定行
python run_one.py --row 3
```

### CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--model_name` | `mg2hg` | 模型名 |
| `--prob_th` | 0.95 | 置信度阈值 |
| `--enable_dual_channel` | off | 双通道评估 |
| `--enable_value_min_confidence` | on | Value Min Confidence |
| `--value_min_bottom_k` | 5 | Bottom-K 取值数 |
| `--question` | 内置电信故障题 | 自定义问题文本 |
| `--row` | 0 | 从数据集加载指定行 |
| `--num_thinking` | 10 | 最大轮次 |
| `--check_length` | 200 | 初始思考长度 |

### 输出

`run_one.py` 通过 monkey-patch 注入诊断函数，输出每轮的详细信息：

```
═══ Round 1 ═══
  平均置信度: 0.92
  困惑度: 1.5
  Answer token 分析: 总=45, 结构=12, value=33
  Bottom-5 value tokens: [-0.10, -0.08, -0.05, -0.03, -0.01]
  Value Min Confidence: -0.054 (exp=0.947)
  ── 决策 ──
  decision_score=0.947 >= prob_th=0.95? NO
  → CONTINUE (置信度不足)

═══ Round 2 ═══
  平均置信度: 0.96
  Value Min Confidence: -0.023 (exp=0.977)
  ── 决策 ──
  decision_score=0.977 >= prob_th=0.95? YES
  think_sufficient: 250 >= 100? YES
  → ACCEPT (Value最低概率=0.977)

═══ 最终结果 ═══
  正确: ✓ | 分数: 10
  接受原因: Value最低概率=0.977
  思考 tokens: 250 | API 调用: 4 | 轮次: 2
  置信度: 0.96 | 困惑度: 1.3
  Value Min: -0.023 (exp=0.977)
```

---

## 功能测试

### test_budget_features.py — 综合功能测试（无需 API）

16 个测试套件，验证所有模块逻辑正确性：

```bash
python tests/test_budget_features.py
```

| 测试 # | 名称 | 验证内容 |
|--------|------|---------|
| 1 | 任务类型分类 | 10 种类型正确分类；预算参数完整；coding > retrieval；自定义 JSON 覆盖 |
| 2 | 场景分类器 | 5 种场景正确分类；typical_tokens 存在 |
| 3 | 历史预算 | 冷启动、新任务检测、无限模式、温启动（3 条）、热启动（8 条） |
| 4 | 温启动策略 | 低方差 → moderate；高方差 → aggressive + CV 跟踪 |
| 5 | 自动注册 | 未达阈值 → 不注册；达阈值 → 注册 + typical_tokens > 0 + auto_registered 标记 |
| 6 | CONFIG→call_model 映射 | 所有必要参数存在；特殊映射（enable_budget→enable_confidence）；CONFIG 键均在 THINK_BUDGET_CONFIG_47 中 |
| 7 | 预算优先级链 | fixed_budget_tokens 覆盖全部；task_type_override 强制类型；fixed 子参数覆盖 |
| 8 | 难度估计器 | 简单题 → 低 difficulty；math/code → 高 difficulty；预算随 difficulty 缩放 |
| 9 | CONFIG 完整性 | 所有 CONFIG 项有描述；人工可控参数存在；print_config() 无错误 |
| 10 | Runner task_type 记录 | record_inference() 使用推断的 task_type（如 'tool_call'），而非硬编码 'naie' |
| 11 | 新任务泛化 | 4 阶段流程：无限 → 温 → 自动注册 → 热启动预算控制 |
| 12 | 导入完整性 | 14 个模块路径成功导入，符号正确 |
| 13 | API 服务器 task_type | 源码中无 task_type='naie'；使用 classify_task_type |
| 14 | call_model 自动注册 | call_model 包含 register_task_type_from_history；函数签名包含 history_warmup_strategy |
| 15 | budget_params 非 None | _evaluate_one 不再有 budget_params=None；使用 budget_params_record 字典 |
| 16 | CLI/API 新参数 | CLI 有 --default_task_type, --custom_task_types_path；API 请求模型有匹配字段；budget_info 包含 task_type |

### test_single_case.py — 单例 API 测试

运行一条问题通过完整的 BudgetForcing pipeline（需要 API 连接）：

```bash
python tests/test_single_case.py

# 自定义参数
python tests/test_single_case.py --prob_th 0.90 --enable_value_min_confidence on
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `--prob_th FLOAT` | 置信度阈值 |
| `--check_length N` | 初始思考长度 |
| `--enable_value_min_confidence on\|off` | Value Min Confidence |
| `--enable_clean_context on\|off` | Clean Context |

### 输出

报告：正确性、分数、思考 tokens、置信度、困惑度、Value Min Confidence、接受原因、延迟、答案内容。

---

## 验证命令

无需 API 连接，验证模块导入和配置完整性：

```bash
# 验证 CONFIG 配置
python -c "import sys;sys.path.insert(0,'.');from naie.config.settings import print_config;print_config()"

# 验证所有模块导入正常
python -c "import sys;sys.path.insert(0,'.');from naie.optimizations import *;from naie.optimizations.history_module import get_task_type_stats;print('ALL OK')"
```

---

## 结果文件

### 保存位置

| 类型 | 路径 |
|------|------|
| 基线结果 | `data/naie/results/result_baseline_*.json` |
| Budget 结果 | `data/naie/results/result_budget_*.json` |
| 消融实验 | `data/naie/results_ablation/ablation_N_name_*.json` |
| 按数据集 | `data/naie/results_per_dataset/summary_*.json` + `latency_*.csv` |
| 推理轨迹 | `data/naie/trajectory_logs/trajectory_*.jsonl` |

### 结果文件结构

```json
{
  "hyperparams": {
    "enable_budget": true,
    "check_length": 200,
    "prob_th": 0.95,
    "num_thinking": 10,
    "enable_value_min_confidence": true,
    "value_min_bottom_k": 5
  },
  "mode": "think_budget",
  "timestamp": "2026-06-01T12:00:00",
  "total_cases": 39,
  "correct": 33,
  "accuracy": 0.846,
  "cases": [
    {
      "id": 1,
      "question": "...",
      "predicted": {"tool": "queryUser", "IMSI": "..."},
      "reference": {"tool": "queryUser", "IMSI": "..."},
      "correct": true,
      "score": 10,
      "thinking_tokens": 250,
      "accept_reason": "value_min_conf_-0.05",
      "accept_reason_cn": "Value最低概率=0.95",
      "value_min_probability": 0.95,
      "task_type": "tool_call",
      "scene_type": "query",
      "budget_params": {
        "task_type": "tool_call",
        "scene_type": "query",
        "accept_reason": "value_min_conf_-0.05",
        "thinking_rounds": 2
      }
    }
  ]
}
```

---

## 历史记录

每次推理自动记录到 `data/naie/trajectory_logs/` 目录下的 JSONL 文件，包含：

- 问题文本
- 推断的 task_type 和 scene_type
- thinking_tokens、total_tokens
- 评分结果（score、is_correct）
- 预算参数（task_type, scene_type, accept_reason, thinking_rounds 等）

历史记录用途：

| 用途 | 模块 |
|------|------|
| 冷/温/热启动预算计算 | `history_budget.py` |
| 统计查询（按任务类型/场景） | `history_module.py` → `get_category_stats()`, `get_scene_stats()` |
| 自动聚类新任务类型 | `task_type_budget.py` → `register_task_type_from_history()` |

---

## 理解输出

### Accept Reason 解读

| 机器码 | 中文 | 含义 |
|--------|------|------|
| `value_min_conf_-0.05` | Value最低概率=0.95 | P3.1 Value Min 通过，最低 K 个 value token 均 logprob=-0.05 |
| `dual_ch_r0.92_a0.96` | 双通道通过(R=0.92,A=0.96) | 双通道 AND 门通过 |
| `full_chain_r0.91_a0.96` | 完整链通过(R=0.91,A=0.96) | 完整链 AND 门通过 |
| `confidence_above_threshold` | 平均置信度达标 | 默认模式纯平均置信度通过 |
| `think_tag_paired_final_accept` | 思考标签自然闭合(最终轮) | 模型自然结束思考 + 最终轮 |
| `consecutive_same_2` | 连续2次答案相同 | 连续 2 轮答案一致 |
| `conf_ok_think_insufficient_80_need_100` | 置信度达标但思考不足 | 置信度够但思考量 < min_think_tokens |
| `reasoning_zone_low_0.70_a0.95` | 推理区低置信 | 双通道中推理区不达标 |
| `action_zone_low_0.93_a0.72` | 动作区低置信 | 双通道中动作区不达标 |

---

## 常见问题

### Q: 运行报错 "Connection refused"

**A**: 检查模型端点是否可达：

```bash
curl "$env:MG2HG_URL/completions"
```

确保脚本内部设置了 `NO_PROXY`（已自动设置，无需手动操作）。

### Q: 评估速度很慢

**A**: BudgetForcing 每条用例需要多次 API 调用（默认最多 10 轮 × 2 调用 = 20 次）。建议：

```bash
# 先小规模测试
python run_budget.py --max_cases 5

# 使用并行评估加速
python run_budget.py --parallel --concurrency 4 --max_cases 20
```

### Q: Budget 模式准确率比基线低

**A**: 检查以下配置：

1. **prob_th 设置**：0.95 可能过于保守，尝试 0.90
2. **Value Min Confidence**：确认 `--enable_value_min_confidence on`
3. **check_answer_len**：确认足够（默认 4000），太小会导致 JSON 截断

### Q: 如何测试特定任务类型？

**A**:

```bash
# 手动指定任务类型
python run_budget.py --task_type tool_call

# 固定预算消除动态影响
python run_budget.py --fixed_budget_tokens 2000
```

### Q: 如何清理历史记录？

**A**: 删除 `data/naie/trajectory_logs/` 下的 JSONL 文件，或在代码中调用 `clear_history()`。

### Q: 如何查看当前运行配置？

**A**:

```bash
python -c "import sys;sys.path.insert(0,'.');from naie.config.settings import print_config;print_config()"
```

### Q: 如何对比两组实验结果？

**A**:

```bash
# 先运行基线和 budget 评估
python run_baseline.py --max_cases 20
python run_budget.py --max_cases 20

# 然后运行对比
python run_compare.py
```
