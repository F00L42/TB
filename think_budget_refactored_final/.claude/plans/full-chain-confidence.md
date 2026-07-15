# 计划：新增完整思维链+答案置信度 + 清理废弃 CONFIG 项

## 一、需求

1. 新增 `enable_full_chain_confidence` — 计算完整思维链+答案的置信度
2. 策略：思维链用 Min-10% (宽容)，答案用 Bottom-K (零容忍)，AND门取 min
3. 保留 `enable_value_min_confidence`（仅答案区 Bottom-K）
4. 移除已废弃 CONFIG 项到 REMOVED_SWITCHES

## 二、废弃项移入 REMOVED_SWITCHES (11项)

| CONFIG key | 原因 |
|-----------|------|
| `enable_joint_confidence` | P2.4，被P3.1替代 |
| `alpha` | P2.4参数，随主开关移除 |
| `max_ppl` | P2.4参数，随主开关移除 |
| `enable_length_norm_entropy` | P2.7，消融无效 |
| `entropy_alpha` | P2.7参数，随主开关移除 |
| `enable_adaptive_min_think` | P4，被P5替代 |
| `adaptive_min_think_base` | P4参数 |
| `adaptive_min_think_max` | P4参数 |
| `enable_adaptive_check_length` | P4，被P5替代 |
| `adaptive_check_length_base` | P4参数 |
| `adaptive_check_length_max` | P4参数 |
| `enable_self_consistency` | P2.8，3x成本 |
| `sc_samples` | P2.8参数 |
| `sc_agreement_th` | P2.8参数 |

## 三、核心改动：完整思维链 logprobs 累积

**问题**：当前 `reasoning_logprobs` 只保存最近一轮 stride 的 logprobs（每轮覆盖）。
**解决**：新增 `accumulated_chain_logprobs` 和 `accumulated_chain_tokens` 列表，每轮追加而非覆盖。

数据流：
```
初始思考(4000 tokens) → logprobs[0..3999]     → 追加到 accumulated
stride 1 (200 tokens)  → logprobs[0..199]      → 追加到 accumulated
stride 2 (200 tokens)  → logprobs[0..199]      → 追加到 accumulated
...
试探答案 (150 tokens)  → ans_logprobs[0..149]  → 不追加，单独传入

full_chain 计算时:
  chain_logprobs = accumulated_chain_logprobs (所有历史思考)
  chain_tokens   = accumulated_chain_tokens
  ans_logprobs   = 本轮试探答案
  ans_tokens     = 本轮试探答案

  chain_score = Min-10%(chain Value tokens)  → 宽容
  ans_score   = Bottom-K(ans Value tokens)   → 零容忍
  decision_score = min(exp(chain_score), exp(ans_score))
```

注意：initial thinking 的 logprobs 也需要纳入 accumulated（当前代码在循环外就已获取）。

## 四、代码修改清单

### 1. `shared/models/confidence.py`
新增函数：
```python
def cal_full_chain_confidence(chain_logprobs, chain_tokens, 
                               ans_logprobs, ans_tokens,
                               *, chain_min_percentile=0.1, bottom_k=3):
    """完整思维链+答案置信度 — 思维链Min-10%(宽容) + 答案Bottom-K(零容忍), AND门"""
    chain_score = cal_reasoning_zone_confidence(chain_logprobs, chain_tokens, chain_min_percentile)
    ans_score = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k)
    decision_score = min(math.exp(chain_score), math.exp(ans_score))
    return decision_score, chain_score, ans_score
```

### 2. `shared/optimizations/confidence_gates.py`
修改 `compute_dual_channel_score()`：
- 新增参数 `enable_full_chain_confidence=False`, `full_chain_min_percentile=0.1`
- 在分支最前面增加 full_chain 分支：
```python
if enable_full_chain_confidence and reasoning_logprobs and ans_logprobs:
    from shared.models.confidence import cal_full_chain_confidence
    decision_score, chain_score_raw, ans_score_raw = cal_full_chain_confidence(
        reasoning_logprobs, reasoning_tokens,
        ans_logprobs, ans_tokens,
        chain_min_percentile=full_chain_min_percentile,
        bottom_k=value_min_bottom_k)
    value_min_raw = ans_score_raw  # 兼容旧 accept_reason
```
- 返回值新增 `chain_score_raw`

### 3. `shared/models/budget_forcing.py` — ReasoningRunner

**3a. _init_params_from_config()**
- 新增 `('enable_full_chain_confidence', False)`, `('full_chain_min_percentile', 0.1)`
- 移除废弃项的默认值读取（joint_confidence, length_norm_entropy, adaptive, self_consistency）

**3b. call_model() 主循环**
- 新增变量 `accumulated_chain_logprobs = []`, `accumulated_chain_tokens = []`
- 初始思考后：`accumulated_chain_logprobs.extend(logprobs or [])`, `accumulated_chain_tokens.extend(tokens or [])`
- 每次 stride 追加思考后：同样 extend
- 软中断 peek 后：同样 extend
- 调用 `compute_dual_channel_score()` 时，当 `enable_full_chain_confidence=True`：
  - `reasoning_logprobs = accumulated_chain_logprobs`
  - `reasoning_tokens = accumulated_chain_tokens`
- 移除旧的 joint_confidence / length_norm_entropy 相关代码

**3c. 最终答案部分**
- 同步修改 final fallback 处的 compute_dual_channel_score 调用

**3d. 异步版 call_model_async()**
- 同步做相同的修改

### 4. `naie/inference/think_budget.py`
- `_cfg_to_params()` 新增映射 `enable_full_chain_confidence` + `full_chain_min_percentile`
- 移除废弃项映射 (joint_confidence, alpha, max_ppl, length_norm_entropy, entropy_alpha, adaptive, sc)

### 5. `naie/config/settings.py`
- CONFIG 新增：
  ```python
  "enable_full_chain_confidence": (False, "[P3.4] 完整链置信度 — 思维链Min-10%(宽容)+答案Bottom-K(零容忍), AND门"),
  "full_chain_min_percentile":    (0.1,  "[P3.4] 思维链最差百分比 — 0.1=最差10% Value Token均值"),
  ```
- CONFIG 移除 14 项 → 加入 REMOVED_SWITCHES
- `enable_value_min_confidence` 默认改为 True（之前是 False，因为用户一直在用）

### 6. `shared/optimizations/early_stopping.py`
- `make_accept_decision()` 新增 full_chain 的 accept_reason 处理
- 移除 joint_confidence 和 sc 相关分支

## 五、不需要改的文件

- `shared/optimizations/budget_controller.py` — 不涉及置信度计算
- `shared/optimizations/trial_evaluator.py` — DEER trial，独立功能
- `shared/optimizations/difficulty_estimator.py` — 难度估计，独立功能

## 六、风险控制

1. logprobs 累积可能很大(数千~数万个) → `cal_reasoning_zone_confidence` 已有排序逻辑，Python 排序数千元素<1ms
2. Clean Context 模式下 prompt 被截断，但 logprobs 仍累积 — 这是正确行为，因为即使 prompt 被清理，之前思考的 logprobs 仍反映模型推理质量
3. 废弃项移除需确保 budget_forcing.py 中不再引用
