# 混合双通道评估方案 (Hybrid Dual-Channel Strategy) — 实施计划

## 问题诊断摘要

当前 TTC 管线存在两个核心缺陷导致置信度评估失真：

### 缺陷1：Key Name 过滤失效（污染源未阻断）
- `token_filter.py` 的 `_DEFAULT_KEY_NAMES = set()` — **空的！**
- `set_task_key_names()` 从未在生产代码路径中被调用（仅在 test_refactoring.py 中出现）
- 而 `confidence_gates.py` 使用的正是 `token_filter.cal_value_min_confidence()`（public版）
- 结果：`faultNumber`、`faultIMSI`、`tool`、`status` 等 JSON 键名的高 logprob 直接参与置信度计算，虚高 decision_score

### 缺陷2：单通道评估（推理与动作不分）
- 当前仅在 **answer zone** 计算置信度（induced prompt 生成 trial answer 的 logprobs）
- **reasoning zone** 的 logprobs 被收集但完全未用于决策
- 推理链条中出现严重逻辑断层时，只要最终 answer 的结构化 token 够多（键名、括号），decision_score 依然能过阈值
- 纯数学推理和工具调用式输出共用同一个 Bottom-K=3 惩罚函数，无法兼顾

---

## 实施计划

### 第一阶段：基础设施修复（阻断污染源）

#### Step 1.1: 修复 Key Name 过滤 — 在 think_budget.py 初始化阶段注入 NAIE 键名
**文件**: `naie/inference/think_budget.py`
**操作**: 在 `call_model()` 和 `call_model_async()` 函数顶部，调用 `set_task_key_names()` 注入 NAIE 任务特有的键名集合

```python
from shared.optimizations.token_filter import set_task_key_names

_NAIE_KEY_NAMES = {
    'tool', 'faultNumber', 'faultIMSI', 'faultPort', 'faultType',
    'fault', 'Number', 'IMSI', 'Url', 'Ip', 'APN', 'Time', 'Range',
    'Caller', 'result', 'answer', 'name', 'type', 'value',
    'faultUrl', 'faultIp', 'faultAPN', 'faultTimeRange',
    'isCaller', 'status', 'code', 'message', 'id', 'data',
    'error', 'success', 'query', 'response',
}
# 在 call_model() 和 call_model_async() 入口处:
set_task_key_names(_NAIE_KEY_NAMES)
```

#### Step 1.2: 扩展 Structural Token 词典
**文件**: `shared/optimizations/token_filter.py` 和 `shared/models/confidence.py`（双源同步）
**操作**: 将数学/代码高频结构符硬编码进入过滤黑名单

新增结构 token:
- 数学: `=`, `+`, `-`, `*`, `/`, `>`, `<`, `≥`, `≤`, `≈`, `≠`, `→`, `=>`
- 代码: `\n`, `;`, `!`, `#`, `%`, `@`, `&`, `^`, `~`
- 推理标签: `<|im_start|>`, `<|im_end|>`, `<|im_sep|>`, `ашки`, `ски` (think tags 的常见 tokenization 碎片)

注意：**数字和英文单词保留为 Value Token**，不移入黑名单。

### 第二阶段：双通道置信度计算逻辑

#### Step 2.1: 新增推理区置信度函数 — Min-10% 均值
**文件**: `shared/optimizations/token_filter.py`（public版）+ `shared/models/confidence.py`（private版）

```python
def cal_reasoning_zone_confidence(logprobs, tokens, min_percentile: float = 0.1) -> float:
    """推理区置信度 — 宽容度策略: 取最差 10% Value Token 的 logprob 均值。

    逻辑: 推理链条允许偶尔废话(连接词低置信度), 但不允许出现严重逻辑断层。
    比纯全局平均更敏锐(能抓到严重幻觉), 又比 Bottom-1 更稳定(不会被单个连接词毙掉)。
    """
    value_logprobs = extract_value_logprobs(logprobs, tokens)
    if not value_logprobs:
        valid = [lp for lp in logprobs if lp is not None] if logprobs else []
        return min(valid) if valid else 0.0
    value_logprobs.sort()
    k = max(1, int(len(value_logprobs) * min_percentile))
    k = min(k, len(value_logprobs))
    return sum(value_logprobs[:k]) / k
```

#### Step 2.2: 修改动作/答案区置信度函数 — 零容忍 Bottom-K
**文件**: `shared/optimizations/token_filter.py`（public版）+ `shared/models/confidence.py`（private版）

当前 `cal_value_min_confidence` 已经实现了 Bottom-K，无需修改算法本身。
但需要在 `confidence_gates.py` 中区分调用时使用不同的 `bottom_k`:
- 推理区: `min_percentile=0.1` (Min-10%)
- 动作区: `bottom_k=3` (Bottom-3，如果 Value Token 不足 3 个则 Bottom-1)

#### Step 2.3: 新增双通道决策聚合函数
**文件**: `shared/optimizations/confidence_gates.py`

```python
def compute_dual_channel_score(
    # 推理区参数
    reasoning_logprobs, reasoning_tokens,
    # 动作/答案区参数
    ans_logprobs, ans_tokens,
    confidence, perplexity,
    *,
    # 推理区配置
    enable_dual_channel=False,
    reasoning_min_percentile=0.1,
    reasoning_conf_th=0.5,      # 推理区阈值 (logprob 域, 对应 ~0.60 概率)
    # 动作区配置
    enable_value_min_confidence=False,
    value_min_bottom_k=3,
    # 通用配置
    prob_th=0.95,
    alpha=0.7, max_ppl=50.0,
    enable_joint_confidence=False,
):
```

**决策聚合逻辑**:
```
IF enable_dual_channel:
    reasoning_raw = cal_reasoning_zone_confidence(reasoning_logprobs, reasoning_tokens, min_percentile)
    reasoning_score = min(exp(reasoning_raw), 1.0)

    action_raw = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k=value_min_bottom_k)
    action_score = min(exp(action_raw), 1.0)

    # 严格 AND 门: 推理逻辑无断层 AND 动作参数确信
    decision_score = min(reasoning_score, action_score)
    # 也可以用几何平均: decision_score = sqrt(reasoning_score * action_score)

ELSE IF enable_value_min_confidence:
    # 原有逻辑 (向后兼容)
    ...
```

#### Step 2.4: 修改 budget_forcing.py — 传递推理区 logprobs
**文件**: `shared/models/budget_forcing.py`

当前在 budget 循环中，每轮推理生成的 `logprobs` 和 `tokens` 已被收集（lines 872-873, 1088-1089），但仅用于 perplexity 计算。需要将**最近一轮推理的 logprobs/tokens** 传递给 `compute_decision_score`（或新的 `compute_dual_channel_score`）。

关键修改点:
1. 在循环开始处初始化 `reasoning_logprobs = None; reasoning_tokens = None`
2. 每轮推理 API 调用后，保存: `reasoning_logprobs = logprobs; reasoning_tokens = tokens`
3. 在 trial answer 评估处，将 `reasoning_logprobs, reasoning_tokens` 传入双通道函数
4. `call_model()` 和 `call_model_async()` 都需要同步修改

#### Step 2.5: 修改 early_stopping.py — 扩展 make_accept_decision
**文件**: `shared/optimizations/early_stopping.py`

增加双通道信息到 accept_reason:
- 如果双通道启用且推理区不通过: `reasoning_zone_low_{reasoning_score:.2f}`
- 如果双通道启用且动作区不通过: `action_zone_low_{action_score:.2f}`

#### Step 2.6: 配置层扩展
**文件**: `naie/config/settings.py` + `naie/inference/think_budget.py` + `shared/models/budget_forcing.py`

新增配置项:
```python
"enable_dual_channel":           (False,   "[双通道] 混合双通道评估开关"),
"reasoning_min_percentile":      (0.1,     "[双通道] 推理区 Min-% 均值百分位"),
"reasoning_conf_th_logprob":     (-0.5,    "[双通道] 推理区 logprob 阈值 — 低于此值视为逻辑断层"),
```

参数透传: settings → _cfg_to_params → call_model → compute_dual_channel_score

---

## 实施顺序与依赖关系

```
Step 1.1 (Key Name 注入)  ← 最高优先级, 独立可部署
Step 1.2 (扩展 Structural Token) ← 独立可部署, 与1.1无依赖
  │
  ├─→ Step 2.1 (推理区置信度函数) ← 依赖1.1+1.2的过滤逻辑
  ├─→ Step 2.2 (确认动作区逻辑不变) ← 依赖2.1确认接口一致
  │
  ├─→ Step 2.3 (双通道聚合函数) ← 依赖2.1+2.2
  ├─→ Step 2.4 (budget_forcing传参) ← 依赖2.3
  ├─→ Step 2.5 (early_stopping扩展) ← 依赖2.3
  └─→ Step 2.6 (配置层扩展) ← 依赖2.3+2.4+2.5
```

**第一阶段(Step 1.1+1.2)可独立上线，预计立即改善现有 P3.1 的 decision_score 准确性。**

---

## 需修改文件清单

| 文件 | 修改内容 | 阶段 |
|------|---------|------|
| `naie/inference/think_budget.py` | 添加 set_task_key_names() 调用 | 1.1 |
| `shared/optimizations/token_filter.py` | 扩展 _DEFAULT_STRUCT_TOKENS | 1.2 |
| `shared/models/confidence.py` | 同步扩展 _STRUCT_TOKENS (双源) | 1.2 |
| `shared/optimizations/token_filter.py` | 新增 cal_reasoning_zone_confidence() | 2.1 |
| `shared/models/confidence.py` | 新增 cal_reasoning_zone_confidence() (双源) | 2.1 |
| `shared/optimizations/confidence_gates.py` | 新增 compute_dual_channel_score() | 2.3 |
| `shared/models/budget_forcing.py` | 传递 reasoning_logprobs, 调用双通道函数 | 2.4 |
| `shared/optimizations/early_stopping.py` | 扩展 make_accept_decision accept_reason | 2.5 |
| `naie/config/settings.py` | 新增 enable_dual_channel 等配置项 | 2.6 |
| `naie/inference/think_budget.py` | 新增配置项透传 | 2.6 |
| `shared/optimizations/__init__.py` | 导出新增函数 | 2.1 |

---

## 风险与约束

1. **循环导入**: `confidence.py` 不能导入 `token_filter.py`，两个 `_is_structural_token` / `cal_reasoning_zone_confidence` 必须手动同步
2. **向后兼容**: `enable_dual_channel=False` 时行为完全不变，现有 P3.1 逻辑不受影响
3. **同步/异步双版本**: `call_model` 和 `call_model_async` 必须同步修改
4. **ReasoningRunner 类**: `budget_forcing.py` 中的 `ReasoningRunner` 类也需要同步修改
5. **Token 碎片化**: think tags (шка/ски) 在 tokenizer 中可能被拆分为多个 token，需要确认过滤是否覆盖所有碎片
