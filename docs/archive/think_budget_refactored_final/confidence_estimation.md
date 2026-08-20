# 置信度估算全流程 — Confidence Estimation Guide

> **★ 核心文档** — BudgetForcing 置信度驱动的早停机制完整说明
>
> 本文档详细阐述 Think Budget Refactored 项目中置信度估计的完整技术链路，
> 从底层结构化 Token 判断到顶层早停决策，覆盖所有评估模式与消融实验结果。

---

## 目录

1. [置信度估计概述](#1-置信度估计概述)
2. [结构化 Token 判断](#2-结构化-token-判断)
3. [Value Min Confidence 计算流程 (P3.1)](#3-value-min-confidence-计算流程-p31)
4. [推理区置信度](#4-推理区置信度)
5. [单通道评估模式](#5-单通道评估模式)
6. [双通道评估模式](#6-双通道评估模式)
7. [完整链置信度 (Full Chain)](#7-完整链置信度-full-chain)
8. [决策评分 → 早停判定完整流程](#8-决策评分--早停判定完整流程)
9. [消融实验结果](#9-消融实验结果)
10. [循环依赖解决方案](#10-循环依赖解决方案)

---

## 1. 置信度估计概述

### 1.1 为什么需要置信度估计

BudgetForcing 的核心机制是**分轮次推理 + 置信度门控早停**：模型逐轮生成思考 token，每轮结束后生成一个试探答案 (trial answer)，系统评估模型对该答案的置信度——如果"足够自信"则接受答案并停止思考，否则继续推理。

**核心问题**：什么时候停止思考、接受答案？

- **过早接受**：模型尚未充分推理，给出错误答案 → 准确率下降
- **过晚接受**：模型已确信正确答案却继续浪费 token → 延迟和成本上升
- **误判置信度**：将结构上的"确定性"误认为内容上的"确定性" → 最危险的系统性偏差

### 1.2 核心挑战：结构 Token 掩盖 Value 不确定性

LLM 输出的 JSON 答案中，**结构 token** 与 **Value token** 的 logprob 分布截然不同：

| Token 类型 | 示例 | 典型 logprob | 典型 exp(prob) |
|-----------|------|-------------|---------------|
| JSON 语法 | `{`, `}`, `:`, `,` | -0.001 ~ -0.01 | 0.99 ~ 0.999 |
| JSON 键名 | `"faultNumber"`, `"IMSI"` | -0.01 ~ -0.05 | 0.95 ~ 0.99 |
| **Value token** | `13800138000`, `故障` | -0.5 ~ -3.0 | 0.05 ~ 0.6 |

**问题**：如果将所有 token 的 logprob 平均，结构 token 的高置信度会**掩盖** Value token 的低置信度，导致模型对实际答案内容不确定时也被判定为"自信"——这就是**过早接受错误答案**的根本原因。

**示例**：假设模型输出 `{"faultNumber": 19}`，其中 `19` 是模型猜测的故障编号（实际上不确定），而 `{`、`"faultNumber"`、`:` 等结构 token 几乎 100% 确定。

- 全局平均 exp(logprob) ≈ 0.85 → 看似自信 → **误判！**
- Value token 平均 exp(logprob) ≈ 0.30 → 实际不确定 → **正确判断**

### 1.3 解决方案：Value Min Confidence (P3.1)

**Value Min Confidence** 是本项目的核心创新，精确解决上述问题：

1. **剔除结构 token**：只保留反映实际内容确定性的 Value token
2. **取最低 K 个 Value token**：关注最不确定的部分，而非被平均值稀释
3. **logprob 域计算**：在 logprob 域排序、求均值，最后转换到概率域与阈值比较

这一创新使 BudgetForcing 能够：
- 在 Value token 确实自信时 → 正确接受，节省 55% 推理延迟
- 在 Value token 不自信时 → 正确拒绝，避免过早接受错误答案

---

## 2. 结构化 Token 判断

### 2.1 结构 Token vs Value Token 定义

| 分类 | 定义 | 判断规则 |
|------|------|---------|
| **结构 Token** | 不反映实际内容确定性的 token | JSON 语法符、代码结构符、数学符号、任务键名、空白符、特殊标记、单字符标点 |
| **Value Token** | 反映实际内容确定性的 token | 不满足上述任何条件的 token — 数字、中文值、英文值等 |

### 2.2 具体 Token 分类示例

#### 结构 Token 详表

| 子类 | 示例 | 说明 |
|------|------|------|
| JSON 语法 | `{`, `}`, `[`, `]`, `"`, `'`, `:`, `,`, ` ``` `, `json`, `JSON` | JSON 结构的骨架，模型几乎 100% 确定 |
| 代码结构符 | `=`, `;`, `!`, `#`, `%`, `@`, `&`, `^`, `~` | 编程语言高频结构符 |
| 数学/逻辑符 | `+`, `-`, `*`, `/`, `>`, `<`, `≥`, `≤`, `≈`, `≠`, `→`, `=>` | 数学推理中的固定符号 |
| 任务键名 | `faultNumber`, `IMSI`, `msisdn`, `ne_name`, `tool`, `alarm_id` | JSON 键名，通过 `set_task_key_names()` 注入 |
| 空白符 | 空格、换行、制表符 | 纯空白 token，不携带内容信息 |
| 特殊标记 | `<|im_start|>`, `<|im_end|>`, `<|im_sep|>` | 模型内部格式标记 |
| 单字符标点 | `.`, `_`, `(`, `)`, `\`, `|` | 长度 ≤ 1 的纯标点字符 |

#### Value Token 详表

| 子类 | 示例 | 说明 |
|------|------|------|
| 数字 | `138`, `38000`, `19`, `192.168` | 故障编号、IP 地址片段、计数器值 |
| 中文值 | `故障`, `查询`, `信令`, `退服` | 故障描述、操作类型、状态名称 |
| 英文值 | `query`, `active`, `RNC`, `LTE` | 设备类型、协议名、操作指令 |
| 复合值 | `RNC001`, `192.168.1.100` | 设备编号、完整 IP 地址 |

### 2.3 为什么结构 Token 有高 logprob

结构 token 的高置信度是语言模型的固有特性，而非缺陷：

1. **上下文强约束**：在 `{"faultNumber":` 之后，`19` 可能是不确定的值，但 `:` 和 `"` 的出现概率接近 1.0——因为 JSON 语法在此处必然需要这些符号
2. **重复模式**：`faultNumber` 作为键名在训练数据中高频出现，模型对"接下来应该是键名"非常确定
3. **确定性语法**：`{` 后必然跟 `}`，这是语法规则，不是模型对答案内容的判断

因此，结构 token 的 logprob 反映的是**语法确定性**，而非**内容确定性**。将二者混为一谈，就是过早接受错误答案的根源。

### 2.4 实现架构：`_struct_token_core.py`

```
_struct_token_core.py  ←── 独立模块，零项目内导入 (打破循环依赖)
     ↑          ↑
     │          │
token_filter.py   confidence.py (naie/models/)
     ↑
confidence_gates.py
     ↑
budget_forcing.py
```

**设计原则**：`_struct_token_core.py` 是整个结构化 Token 判断的**唯一权威实现**，无任何项目内导入，不会触发循环依赖。所有其他模块通过导入此模块获取 `is_structural_token()` 等函数。

### 2.5 `is_structural_token()` 判断逻辑

```python
# naie/optimizations/_struct_token_core.py

# 默认结构 token 集合
DEFAULT_STRUCT_TOKENS = {
    # JSON 语法
    '{', '}', '[', ']', '"', "'", ':', ',', '```', 'json', 'JSON',
    # 数学/代码高频结构符
    '=', '+', '-', '*', '/', '>', '<', '≥', '≤', '≈', '≠', '→', '=>',
    # 代码结构符
    ';', '!', '#', '%', '@', '&', '^', '~',
}

def is_structural_token(token: str) -> bool:
    """判断 token 是否为结构 token (不参与置信度计算)。"""
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

**判断优先级链**：空字符串 → 默认结构集 → 空白符 → 特殊标记 → 任务键名 → 单字符标点 → Value token

### 2.6 任务键名注入机制

NAIE 评估中的 JSON 答案包含大量领域特有键名（如 `faultNumber`、`IMSI`），这些键名在模型输出中几乎 100% 确定，必须排除。系统通过运行时注入机制实现：

```python
# naie/inference/think_budget.py — 模块加载时注入

_NAIE_KEY_NAMES = {
    'tool', 'faultNumber', 'faultIMSI', 'faultPort', 'faultType',
    'fault', 'Number', 'IMSI', 'Url', 'Ip', 'APN', 'Time', 'Range',
    'Caller', 'result', 'answer', 'name', 'type', 'value',
    'faultUrl', 'faultIp', 'faultAPN', 'faultTimeRange',
    'isCaller', 'status', 'code', 'message', 'id', 'data',
    'error', 'success', 'query', 'response',
}
set_task_key_names(_NAIE_KEY_NAMES)
```

注入后，`is_structural_token("faultNumber")` 返回 `True`（因为 `cleaned="faultnumber"` 在 `_key_names` 中），避免 JSON 键名的高 logprob 污染置信度。

**关键**：键名匹配使用 `strip().lower()`，因此大小写和前后空白不影响判断。

### 2.7 `extract_value_logprobs()` — 过滤函数

```python
# naie/optimizations/_struct_token_core.py

def extract_value_logprobs(logprobs, tokens):
    """过滤结构 Token logprob，只保留 Value token 的 logprob。"""
    if not logprobs or not tokens:
        return []
    value_lps = []
    for lp, tok in zip(logprobs, tokens):
        if lp is None:                # 跳过无效 logprob
            continue
        if is_structural_token(tok):  # 跳过结构 token
            continue
        value_lps.append(lp)          # 只保留 Value token 的 logprob
    return value_lps
```

**注意**：`logprobs` 和 `tokens` 必须等长（一一对应），函数使用 `zip()` 配对遍历。

---

## 3. Value Min Confidence 计算流程 (P3.1)

> **P3.1 是本项目的核心创新**，也是生产环境默认启用的评估模式。

### 3.1 六步计算流程

```
┌─────────────────────────────────────────────────────────────┐
│              Value Min Confidence 计算流程                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Step 1: 提取 Value logprobs                                 │
│  ┌─────────────────────────────────────┐                    │
│  │ extract_value_logprobs(logprobs,    │                    │
│  │                        tokens)      │                    │
│  │ → 过滤结构 token, 只保留 Value token │                    │
│  └──────────────┬──────────────────────┘                    │
│                 │                                           │
│  Step 2: 升序排列                                            │
│  ┌──────────────┴──────────────────────┐                    │
│  │ value_lps.sort()                    │                    │
│  │ → 最低 logprob 排在最前面             │                    │
│  └──────────────┬──────────────────────┘                    │
│                 │                                           │
│  Step 3: 取 Bottom-K                                        │
│  ┌──────────────┴──────────────────────┐                    │
│  │ k = min(bottom_k, len(value_lps))   │                    │
│  │ → 默认 K=3, 取最低 3 个 Value logprob│                    │
│  └──────────────┬──────────────────────┘                    │
│                 │                                           │
│  Step 4: 计算均值 → value_min_raw                            │
│  ┌──────────────┴──────────────────────┐                    │
│  │ value_min_raw = sum(value_lps[:k])/k│                    │
│  │ → logprob 域的 Bottom-K 均值         │                    │
│  └──────────────┬──────────────────────┘                    │
│                 │                                           │
│  Step 5: 转概率域 → decision_score                           │
│  ┌──────────────┴──────────────────────┐                    │
│  │ decision_score = min(exp(           │                    │
│  │   value_min_raw), 1.0)              │                    │
│  │ → 概率域 [0, 1], 上限截断到 1.0       │                    │
│  └──────────────┬──────────────────────┘                    │
│                 │                                           │
│  Step 6: 与阈值比较                                          │
│  ┌──────────────┴──────────────────────┐                    │
│  │ decision_score >= prob_th ?         │                    │
│  │ → 默认 prob_th = 0.95               │                    │
│  │ → YES: 接受答案  NO: 继续推理         │                    │
│  └─────────────────────────────────────┘                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 代码实现

```python
# naie/optimizations/token_filter.py

def cal_value_min_confidence(logprobs, tokens, bottom_k: int = 3) -> float:
    """Value 最低 K 个 token logprob 均值 — P3.1 核心创新。

    剔除结构 token (JSON 语法、键名等) 的高 logprob，
    只看 Value token (数字、中文值) 的 bottom-K，避免结构 token 掩盖低置信度。

    Args:
        logprobs: token logprob 列表
        tokens: token 文本列表 (与 logprobs 等长)
        bottom_k: 取最低 K 个 value token 的 logprob 均值, 默认 3
    Returns:
        bottom-K value token 的 logprob 均值 (logprob 域, 非概率域)
    """
    value_lps = extract_value_logprobs(logprobs, tokens)  # Step 1
    if not value_lps:
        # 无 Value token 时回退到全局最小 logprob
        valid = [lp for lp in logprobs if lp is not None] if logprobs else []
        return min(valid) if valid else 0.0
    value_lps.sort()                          # Step 2
    k = min(bottom_k, len(value_lps))         # Step 3
    return sum(value_lps[:k]) / k             # Step 4
```

**概率域转换在 `confidence_gates.py` 中完成**：

```python
# naie/optimizations/confidence_gates.py — 单通道模式

decision_score_raw = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k=value_min_bottom_k)
decision_score = min(math.exp(decision_score_raw), 1.0)  # Step 5
# Step 6: decision_score >= prob_th → accept
```

### 3.3 完整数值示例

#### 输入答案

```json
{"faultNumber": 19, "Ip": "192.168.1.100"}
```

#### Token-by-token Logprob 分析

| 序号 | Token | logprob | exp(logprob) | is_structural? | 判断依据 |
|------|-------|---------|-------------|----------------|---------|
| 1 | `{` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 2 | `"` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 3 | `faultNumber` | -0.02 | 0.980 | ✅ 结构 | 任务键名 |
| 4 | `"` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 5 | `:` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 6 | ` ` | -0.005 | 0.995 | ✅ 结构 | 空白符 |
| 7 | `19` | **-1.5** | **0.223** | ❌ Value | 数字 |
| 8 | `,` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 9 | ` ` | -0.005 | 0.995 | ✅ 结构 | 空白符 |
| 10 | `"` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 11 | `Ip` | -0.03 | 0.970 | ✅ 结构 | 任务键名 |
| 12 | `"` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 13 | `:` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 14 | ` "` | -0.005 | 0.995 | ✅ 结构 | 空白+引号 |
| 15 | `192` | **-0.8** | **0.449** | ❌ Value | IP 地址片段 |
| 16 | `.` | -0.01 | 0.990 | ✅ 结构 | 单字符标点 |
| 17 | `168` | **-1.2** | **0.301** | ❌ Value | IP 地址片段 |
| 18 | `.` | -0.01 | 0.990 | ✅ 结构 | 单字符标点 |
| 19 | `1` | **-0.5** | **0.607** | ❌ Value | IP 地址片段 |
| 20 | `.` | -0.01 | 0.990 | ✅ 结构 | 单字符标点 |
| 21 | `100` | **-2.8** | **0.061** | ❌ Value | IP 地址片段 |
| 22 | `"` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |
| 23 | `}` | -0.001 | 0.999 | ✅ 结构 | JSON 语法 |

#### Step 1：提取 Value logprobs

```
结构 token (已过滤): {, ", faultNumber, ", :, 空格, ,, 空格, ", Ip, ", :, ", ., ., ., ", }
Value token (保留):  19(-1.5), 192(-0.8), 168(-1.2), 1(-0.5), 100(-2.8)
```

**Value logprobs** = `[-1.5, -0.8, -1.2, -0.5, -2.8]`

#### Step 2：升序排列

```
排序后: [-2.8, -1.5, -1.2, -0.8, -0.5]
         ↑最低  ↑次低  ↑第三  ...
```

#### Step 3：取 Bottom-K (K=3)

```
Bottom-3: [-2.8, -1.5, -1.2]
```

#### Step 4：计算均值 → value_min_raw

```
value_min_raw = (-2.8 + (-1.5) + (-1.2)) / 3
             = -5.5 / 3
             = -1.833  (logprob 域)
```

#### Step 5：转概率域 → decision_score

```
decision_score = min(exp(-1.833), 1.0)
              = min(0.160, 1.0)
              = 0.160
```

#### Step 6：与阈值比较

```
prob_th = 0.95
decision_score (0.160) < prob_th (0.95)  →  CONTINUE (继续推理)
```

**结论**：模型对 `faultNumber=19` 和 `Ip=192.168.1.100` 这两个值不确定（尤其是 `100` 这个 IP 末段，logprob 仅 -2.8），因此不应接受当前答案。

#### 对比：如果不过滤结构 Token

```
全局平均 logprob = 所有 23 个 token 的 logprob 均值
                ≈ (-0.001×8 + -0.02 + -0.005×3 + -0.03 + -0.01×3 + (-1.5) + (-0.8) + (-1.2) + (-0.5) + (-2.8)) / 23
                ≈ (-0.008 + -0.02 + -0.015 + -0.03 + -0.03 + -1.5 + -0.8 + -1.2 + -0.5 + -2.8) / 23
                ≈ -6.903 / 23
                ≈ -0.300
exp(-0.300) = 0.741 → 仍然 < 0.95，但看起来"还行"

平均 exp(logprob) = (0.999×8 + 0.980 + 0.995×3 + 0.970 + 0.990×3 + 0.223 + 0.449 + 0.301 + 0.607 + 0.061) / 23
                  ≈ (7.992 + 0.980 + 2.985 + 0.970 + 2.970 + 0.223 + 0.449 + 0.301 + 0.607 + 0.061) / 23
                  ≈ 17.538 / 23
                  ≈ 0.762 → 看似"中等自信" → 可能被误判
```

**关键差异**：

| 方法 | 评分 | 判定 | 问题 |
|------|------|------|------|
| 全局平均 (不过滤) | 0.762 | 继续推理 (仍 < 0.95) | 评分偏高，对部分类型问题可能误判 |
| Value Min (K=3) | 0.160 | 继续推理 | 准确反映值不确定性 |
| **当结构 token 占比更高时** | | | |
| 全局平均 | 可能 > 0.95 | **误判接受！** | 最危险的系统性偏差 |
| Value Min (K=3) | 仍然低 | 正确拒绝 | 结构 token 无法掩盖 |

### 3.4 回退逻辑

当答案中所有 token 都是结构 token（极端情况，如纯 JSON 骨架 `{"key": ""}`）：

```python
if not value_lps:
    # 无 Value token 时回退到全局最小 logprob
    valid = [lp for lp in logprobs if lp is not None] if logprobs else []
    return min(valid) if valid else 0.0
```

这确保即使没有 Value token 也不会崩溃，而是使用最保守的全局最小 logprob。

### 3.5 Bottom-K 参数说明

| 参数 | CONFIG 键 | 默认值 | 效果 |
|------|----------|--------|------|
| `bottom_k` | `value_min_bottom_k` | 3 (CONFIG=5) | K 越小越敏感（更早检测到不确定性），K 越大越稳定（需要更多 Value token 低置信才触发） |

**K 值调优建议**：
- **K=1**：最敏感，但容易被单个连接词"毙掉"，不稳定
- **K=3**：平衡敏感性和稳定性，推荐默认值
- **K=5**：CONFIG 生产默认值，更保守，需要更多 Value token 共同低置信
- **K≥10**：趋近于 Value 平均置信度，失去"最差部分"检测能力

---

## 4. 推理区置信度

### 4.1 设计动机

推理区 (thinking/reasoning zone) 与答案区 (action/answer zone) 的置信度评估需要不同策略：

- **答案区**：零容忍 — 每个参数值都必须确信，一个参数错误 = 整个答案错误
- **推理区**：宽容 — 允许偶尔的"废话"（连接词、过渡词 logprob 天然较低），但不容忍严重逻辑断层

### 4.2 Min-Percentile 策略

```python
# naie/optimizations/token_filter.py

def cal_reasoning_zone_confidence(logprobs, tokens, min_percentile: float = 0.1) -> float:
    """推理区置信度 — 宽容度策略: 取最差 min_percentile(默认10%) Value Token 的 logprob 均值。

    逻辑: 推理链条允许偶尔废话(连接词低置信度), 但不允许出现严重逻辑断层。
    比纯全局平均更敏锐(能抓到严重幻觉), 又比 Bottom-1 更稳定(不会被单个连接词毙掉)。
    与动作区的零容忍 Bottom-K 策略互补, 构成双通道评估。
    """
    value_lps = extract_value_logprobs(logprobs, tokens)
    if not value_lps:
        valid = [lp for lp in logprobs if lp is not None] if logprobs else []
        return min(valid) if valid else 0.0
    value_lps.sort()
    k = max(1, int(len(value_lps) * min_percentile))  # 最差 10%
    k = min(k, len(value_lps))
    return sum(value_lps[:k]) / k
```

### 4.3 数值示例

假设推理链有 50 个 Value token，排序后最低 5 个 logprob 为：

```
[-4.5, -3.8, -3.2, -2.9, -2.5, ... (后续 45 个较高)]
```

Min-10% 策略（5 个 = 50 × 0.1）：

```
reasoning_score_raw = (-4.5 + (-3.8) + (-3.2) + (-2.9) + (-2.5)) / 5
                    = -16.9 / 5
                    = -3.38  (logprob 域)

reasoning_score = exp(-3.38) = 0.034
```

### 4.4 与其他策略的对比

| 策略 | 计算方式 | 敏感性 | 稳定性 | 适用区域 |
|------|---------|--------|--------|---------|
| **全局平均** | 所有 value token 的 exp 均值 | 低 | 高 | 不推荐 |
| **Bottom-1** | 最低单个 value token | 最高 | 低 (被单个连接词误杀) | 不推荐 |
| **Min-Percentile (宽容)** | 最差 10% value token 均值 | 中高 | 高 | ✅ 推理区 |
| **Bottom-K (零容忍)** | 最低 K 个 value token 均值 | 高 | 中高 | ✅ 答案区 |

**Min-Percentile 的优势**：
- 比全局平均**更敏锐**：能检测到严重幻觉（多个 Value token 低置信）
- 比 Bottom-1**更稳定**：不会被单个连接词的低 logprob 误杀
- 自适应 token 数量：10% 的 token 数量随推理链长度自然调整

### 4.5 为什么推理区不需要零容忍

推理链中的"低置信"token 有两种性质：

1. **良性低置信**：连接词（"然后"、"因此"）、过渡语 — logprob 低但推理方向正确
2. **恶性低置信**：逻辑跳跃、幻觉内容 — 多个连续 Value token 的 logprob 都很低

Min-Percentile 策略天然区分这两种情况：
- 良性：只有 1-2 个连接词低置信，10% 均值被其他正常 token 稀释
- 恶性：整段推理偏移，10% 的 token 集中在偏移区域，均值显著降低

---

## 5. 单通道评估模式

### 5.1 `compute_decision_score()` — 单通道决策评分

> 位于 `naie/optimizations/confidence_gates.py`

单通道模式是**最简评估模式**：只看试探答案 (trial answer) 的置信度，不考虑推理链。

```python
def compute_decision_score(ans_logprobs, ans_tokens, confidence, perplexity, *,
                           enable_value_min_confidence=False,
                           enable_full_chain_confidence=False,
                           full_chain_min_percentile=0.1,
                           value_min_bottom_k=3):
```

### 5.2 四种子模式

| 模式 | 触发条件 | 决策评分计算 | 状态 |
|------|---------|------------|------|
| **full_chain** | `enable_full_chain_confidence=True` | 退化为 value_min (此函数无法提供思维链 logprobs) | 退化 |
| **value_min** | `enable_value_min_confidence=True` | `exp(cal_value_min_confidence(bottom-K avg))` | ✅ 推荐 |
| **默认** | 以上均不满足 | `confidence` (平均 exp(logprobs)) | 基线 |

### 5.3 单通道流程图

```
                       ans_logprobs + ans_tokens
                                │
                    ┌───────────┴───────────┐
                    │                       │
              enable_value_min?         默认模式
                    │                       │
              ┌─────┴─────┐          ┌──────┴──────┐
              │ 结构过滤   │          │             │
              │ extract_   │          │ cal_        │
              │ value_     │          │ confidence()│
              │ logprobs() │          │ (全部平均)   │
              └─────┬─────┘          └──────┬──────┘
                    │                       │
              Value logprobs          confidence
                    │                  [0,1] 概率域
              ┌─────┴─────┐                │
              │ Bottom-K  │                │
              │ 排序+取低K │                │
              └─────┬─────┘                │
                    │                       │
              value_min_raw            decision_score
              (logprob 域)             = confidence
                    │
              exp(raw) → [0,1]
                    │
              decision_score
                    │
                    └───────────┬───────────┘
                                │
                          vs. prob_th
                                │
                    ┌───────────┴───────────┐
                    │                       │
               >= prob_th              < prob_th
                    │                       │
               (可能 ACCEPT)          (CONTINUE)
```

### 5.4 返回值

```python
return (decision_score, value_min_raw)
```

| 字段 | 域 | 用途 |
|------|----|------|
| `decision_score` | [0,1] 概率域 | 与 `prob_th` 比较，决定是否接受 |
| `value_min_raw` | logprob 域 | accept_reason 编码: `value_min_conf_{raw:.2f}` |

> **注意**：`compute_decision_score()` 是向后兼容接口。`budget_forcing.py` 的**实际主入口**是 `compute_dual_channel_score()`（见下一节），它统一了所有模式。

---

## 6. 双通道评估模式

### 6.1 `compute_dual_channel_score()` — 统一决策评分

> 位于 `naie/optimizations/confidence_gates.py`
> **这是 `budget_forcing.py` 调用的主入口**

双通道模式将推理区和答案区分开评估，然后用 AND 门聚合——这是最精细的评估方式。

```python
def compute_dual_channel_score(
    # 思维链参数
    reasoning_logprobs, reasoning_tokens,
    # 答案区参数
    ans_logprobs, ans_tokens,
    confidence, perplexity,
    *,
    # 完整链配置
    enable_full_chain_confidence=False,
    full_chain_min_percentile=0.1,
    # 答案区配置
    enable_value_min_confidence=False,
    value_min_bottom_k=3,
    # 双通道配置
    enable_dual_channel=False,
    reasoning_min_percentile=0.1,
):
```

### 6.2 优先级链

```
enable_full_chain_confidence > enable_dual_channel > enable_value_min_confidence > 默认
```

只有最高优先级的开启模式生效，低优先级被忽略。

### 6.3 四种模式详解

#### 模式 1: 完整链 AND 门 (`enable_full_chain_confidence=True`)

```
思维链 (所有历史思考) ──→ Min-10% (宽容策略) ──→ chain_score
答案区 (trial answer) ──→ Bottom-K (零容忍)  ──→ ans_score
                                    │
                         AND 门 = min(chain_score, ans_score)
                                    │
                              decision_score
```

- **思维链区**：取**所有历史轮次**累积思考的最差 10% Value token logprob 均值 → exp → chain_score
- **答案区**：取试探答案最低 K 个 Value token logprob 均值 → exp → ans_score
- **AND 门**：`min(chain_score, ans_score)` — 推理逻辑没有断层 **且** 最终参数确信，整条路径才算通过

#### 模式 2: 双通道 (`enable_dual_channel=True`)

```
推理区 (当前轮思考) ──→ Min-10% (宽容) ──→ reasoning_score
动作区 (trial answer) ──→ Bottom-K (零容忍) ──→ action_score
                                    │
                          AND 门 = min(reasoning_score, action_score)
                                    │
                              decision_score
```

与 full_chain 的区别：推理区只看**当前轮**思考，不是完整历史链。

#### 模式 3: 单通道 P3.1 (`enable_value_min_confidence=True`)

```
答案区 (trial answer) ──→ Bottom-K ──→ decision_score
```

最简模式：只看答案 Value token 的最低置信度，不考虑推理链。

#### 模式 4: 默认

```
平均 exp(logprobs) ──→ confidence ──→ decision_score
```

不区分结构/Value token，纯平均置信度。

### 6.4 双通道 AND 门的工作原理

```python
# 推理区: Min-10% (宽容度策略)
reasoning_score_raw = cal_reasoning_zone_confidence(
    reasoning_logprobs, reasoning_tokens,
    min_percentile=reasoning_min_percentile)
reasoning_score = min(math.exp(reasoning_score_raw), 1.0)

# 动作/答案区: Bottom-K (零容忍策略)
action_score_raw = cal_value_min_confidence(
    ans_logprobs, ans_tokens,
    bottom_k=value_min_bottom_k)
action_score = min(math.exp(action_score_raw), 1.0)

# 决策聚合: 严格 AND 门
decision_score = min(reasoning_score, action_score)
```

### 6.5 为什么 AND 门能同时捕获逻辑断层和参数错误

AND 门 `min(reasoning_score, action_score)` 的数学性质确保：

| 场景 | reasoning_score | action_score | AND 门结果 | 判定 |
|------|----------------|-------------|-----------|------|
| 推理正确 + 参数确信 | 0.97 | 0.98 | 0.97 | ✅ 接受 |
| 推理正确 + 参数不确定 | 0.96 | 0.40 | 0.40 | ❌ 继续 (参数错) |
| 推理断层 + 参数确信 | 0.30 | 0.97 | 0.30 | ❌ 继续 (逻辑错) |
| 推理断层 + 参数不确定 | 0.30 | 0.40 | 0.30 | ❌ 继续 (双重错) |

**关键洞察**：
- 如果只看答案区 (单通道)，场景 3 会被误判接受——推理逻辑已偏离但碰巧参数看似确定
- 如果只看推理区，场景 2 会被误判接受——推理方向对但最终填了错误参数
- AND 门确保两个条件**同时满足**，是最严格的评估

### 6.6 返回值

```python
return (decision_score, value_min_raw, reasoning_score_raw, action_score_raw)
```

| 字段 | 域 | 用途 |
|------|----|------|
| `decision_score` | [0,1] 概率域 | 与 `prob_th` 比较，决定是否接受 |
| `value_min_raw` | logprob 域 | accept_reason 编码 (兼容旧格式) |
| `reasoning_score_raw` | logprob 域 | 思维链/推理区原始分，诊断用 |
| `action_score_raw` | logprob 域 | 动作/答案区原始分，诊断用 |

### 6.7 `budget_forcing.py` 中的调用方式

```python
# call_model() 循环体 (line ~1231)

# 1. 基础置信度计算
confidence = cal_confidence(ans_logprobs)
perplexity = cal_perplexity(ans_logprobs)

# 2. 统一决策评分
decision_score, value_min_raw, reasoning_score_raw, action_score_raw = compute_dual_channel_score(
    reasoning_logprobs, reasoning_tokens,
    ans_logprobs, ans_tokens, confidence, perplexity,
    enable_full_chain_confidence=enable_full_chain_confidence,
    full_chain_min_percentile=full_chain_min_percentile,
    enable_dual_channel=enable_dual_channel,
    reasoning_min_percentile=reasoning_min_percentile,
    enable_value_min_confidence=enable_value_min_confidence,
    value_min_bottom_k=value_min_bottom_k,
)

# 3. 早停判断
should_accept, accept_reason = make_accept_decision(
    decision_score, prob_th, actual_len, min_think_tokens,
    is_think_tag_paired(prompt), consecutive_same_count, consecutive_same_stop,
    enable_value_min_confidence, value_min_raw,
    enable_full_chain_confidence=enable_full_chain_confidence,
    reasoning_score_raw=reasoning_score_raw,
    action_score_raw=action_score_raw,
    enable_dual_channel=enable_dual_channel,
)
```

---

## 7. 完整链置信度 (Full Chain)

### 7.1 设计思想

完整链置信度 (P3.4) 评估从思考到答案的**完整推理路径**，要求两个条件同时满足：

1. **推理路径没有断层**：思维链 Min-Percentile (宽容策略)
2. **最终动作参数确信**：答案区 Bottom-K (零容忍)

AND 门：`min(chain_score, ans_score)` — 任一不满足，整条路径不通过。

### 7.2 实现

```python
# naie/models/confidence.py

def cal_full_chain_confidence(
    chain_logprobs, chain_tokens,     # 完整思维链 (含所有历史思考)
    ans_logprobs, ans_tokens,          # 试探答案区
    *, chain_min_percentile: float = 0.1, bottom_k: int = 3,
) -> tuple:
    """完整思维链+答案置信度 — 思维链Min-%(宽容) + 答案Bottom-K(零容忍), AND门取min。"""
    # 思维链区: Min-10% (宽容度策略)
    chain_score_raw = cal_reasoning_zone_confidence(
        chain_logprobs, chain_tokens, min_percentile=chain_min_percentile)
    chain_score = min(math.exp(chain_score_raw), 1.0)

    # 答案区: Bottom-K (零容忍)
    ans_score_raw = cal_value_min_confidence(
        ans_logprobs, ans_tokens, bottom_k=bottom_k)
    ans_score = min(math.exp(ans_score_raw), 1.0)

    # AND门
    decision_score = min(chain_score, ans_score)
    return decision_score, chain_score_raw, ans_score_raw
```

### 7.3 与双通道的对比

| 维度 | 完整链 (P3.4) | 双通道 |
|------|-------------|--------|
| 思维链范围 | **所有历史轮次**的累积思考 | **当前轮**的思考 |
| 参数来源 | 需 budget_forcing 传入完整链 logprobs | 只需当前轮推理 logprobs |
| 灵敏度 | 更全面 (跨轮累积) | 更局部 (单轮诊断) |
| 优先级 | 最高 (`enable_full_chain_confidence`) | 次高 (`enable_dual_channel`) |
| 适用场景 | 需要全局推理质量评估 | 需要单轮精确诊断 |

### 7.4 `budget_forcing.py` 中的完整链 logprobs 传递

`budget_forcing.py` 在调用 `compute_dual_channel_score()` 时直接传入当轮的 `reasoning_logprobs` 和 `reasoning_tokens`，完整链模式下由 `compute_dual_channel_score()` 内部调用 `cal_full_chain_confidence()` 处理：

```python
decision_score, value_min_raw, reasoning_score_raw, action_score_raw = compute_dual_channel_score(
    reasoning_logprobs, reasoning_tokens,
    ans_logprobs, ans_tokens, confidence, perplexity,
    enable_full_chain_confidence=self.enable_full_chain_confidence,
    ...
)
```

### 7.5 启用方式

```python
# CONFIG 配置
set_value('enable_full_chain_confidence', (True, '完整链置信度开关'))

# CLI 启用
python run_budget.py --enable_full_chain_confidence on
```

---

## 8. 决策评分 → 早停判定完整流程

### 8.1 BudgetForcing 循环中的完整链路

```
┌─────────────────────────────────────────────────────────────────┐
│                     BudgetForcing call_model()                  │
│                                                                 │
│  1. 初始思考生成 (check_length tokens)                           │
│     → 获得 reasoning_logprobs + reasoning_tokens                │
│     │                                                           │
│     ▼                                                           │
│  ┌──────────── Loop (round = 1 to num_thinking) ─────────────┐  │
│  │                                                             │  │
│  │  2. Think tag 配对检查                                       │  │
│  │     is_think_tag_paired(think_content)?                     │  │
│  │     │                                                       │  │
│  │     ├── 未配对 + actual_len < min_think_tokens              │  │
│  │     │   → reopen_think_tag() + 继续思考 (不生成试探答案)       │  │
│  │     │                                                       │  │
│  │     └── 已配对 或 actual_len >= min_think_tokens            │  │
│  │         │                                                   │  │
│  │         ▼                                                   │  │
│  │  3. 生成 induced prompt (copy mechanism)                     │  │
│  │     ├── 思考标签已配对 → induced_prompt = prompt (直接使用)  │  │
│  │     └── 思考标签未配对 → make_induced_prompt(prompt) (复制)  │  │
│  │     │                                                       │  │
│  │     ▼                                                       │  │
│  │  4. 试探答案生成 (check_answer_len tokens)                    │  │
│  │     result = _call(induced_prompt, check_answer_len)         │  │
│  │     → 获得 ans_content + ans_logprobs + ans_tokens           │  │
│  │     │                                                       │  │
│  │     ▼                                                       │  │
│  │  5. 基础置信度计算                                            │  │
│  │     cal_confidence(ans_logprobs) → confidence                │  │
│  │     cal_perplexity(ans_logprobs) → perplexity                │  │
│  │     │                                                       │  │
│  │     ▼                                                       │  │
│  │  6. 统一决策评分                                              │  │
│  │     compute_dual_channel_score(                              │  │
│  │       reasoning_logprobs, reasoning_tokens,                  │  │
│  │       ans_logprobs, ans_tokens,                              │  │
│  │       confidence, perplexity,                                │  │
│  │       enable_full_chain_confidence,                          │  │
│  │       enable_dual_channel,                                   │  │
│  │       enable_value_min_confidence,                           │  │
│  │       value_min_bottom_k, ...                                │  │
│  │     ) → (decision_score, value_min_raw,                      │  │
│  │           reasoning_score_raw, action_score_raw)             │  │
│  │     │                                                       │  │
│  │     ▼                                                       │  │
│  │  7. 早停判断                                                  │  │
│  │     make_accept_decision(                                    │  │
│  │       decision_score, prob_th,                               │  │
│  │       actual_len, min_think_tokens,                          │  │
│  │       is_paired, consecutive_same_count, ...                 │  │
│  │     ) → (should_accept, accept_reason)                       │  │
│  │     │                                                       │  │
│  │     ├── ACCEPT ──→ 加入 answer_list, 选择最佳答案返回          │  │
│  │     │                                                       │  │
│  │     └── CONTINUE ──→                                         │  │
│  │          │                                                   │  │
│  │          ▼                                                   │  │
│  │  8. Clean Context (可选)                                      │  │
│  │     apply_clean_context() → 截断 induced 部分                │  │
│  │          │                                                   │  │
│  │          ▼                                                   │  │
│  │  9. 连续相同答案检测                                            │  │
│  │     check_consecutive_same(current, prev, count)             │  │
│  │          │                                                   │  │
│  │          ▼                                                   │  │
│  │  10. 计算步长 + 继续思考                                       │  │
│  │      current_prompt += "থ\n" + 请求 think_stride tokens     │  │
│  │      (soft_truncation: 截断时在自然断点闭合思考标签)           │  │
│  │          │                                                   │  │
│  │          └──────→ 回到步骤 2                                  │  │
│  │                                                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                 │
│  11. 最终兜底 (达到最大轮次 或 安全门超时)                         │  │
│      → 返回 answer_list 中最高 confidence 的答案                 │  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 `make_accept_decision()` 决策流程

> 位于 `naie/optimizations/early_stopping.py`

```python
def make_accept_decision(decision_score, prob_th, actual_len, min_think_tokens,
                         is_paired, consecutive_same_count, consecutive_same_stop,
                         enable_value_min_confidence, decision_score_raw,
                         enable_full_chain_confidence=False,
                         reasoning_score_raw=0.0,
                         action_score_raw=0.0,
                         enable_dual_channel=False,
                         is_final=False):
```

#### 输入参数

| 参数 | 类型 | 含义 |
|------|------|------|
| `decision_score` | float [0,1] | 置信度评分 |
| `prob_th` | float | 置信度阈值 (默认 0.95) |
| `actual_len` | int | 当前已思考 token 数 |
| `min_think_tokens` | int | 最低思考 token 门限 (默认 100) |
| `is_paired` | bool | 思考标签是否配对 |
| `consecutive_same_count` | int | 连续相同答案计数 |
| `consecutive_same_stop` | int | 连续相同早停阈值 (默认 999=禁用) |
| `enable_value_min_confidence` | bool | P3.1 开关 |
| `decision_score_raw` | float | logprob 域原始分 |
| `enable_full_chain_confidence` | bool | 完整链开关 |
| `reasoning_score_raw` | float | 推理区 logprob 原始分 |
| `action_score_raw` | float | 动作区 logprob 原始分 |
| `enable_dual_channel` | bool | 双通道开关 |
| `is_final` | bool | 是否最后一轮 |

#### 决策流程图

```
                        ┌─────────────────────────────┐
                        │ decision_score >= prob_th ?  │
                        └──────────┬──────────────────┘
                           │                │
                          YES              NO
                           │                │
                    ┌──────┴──────┐    ┌────┴─────────────────┐
                    │think_sufficient?│ │full_chain/dual_ch模式?│
                    └──────┬──────┘    └────┬─────────────────┘
                   │            │            │ (记录低分区诊断)
                  YES          NO           │
                   │            │            │
            ┌──────┴──────┐  continue   ┌────┴──────────────┐
            │   ACCEPT    │  (思考不足) │is_paired && think_ok?│
            │ reason by   │            └────┬──────────────┘
            │ active mode │                │            │
            └─────────────┘              YES          NO
                                              │            │
                                        ┌─────┴──────┐     │
                                        │is_final?   │     │
                                        └──┬──────┬──┘     │
                                      YES  │      │ NO     │
                                       │   │      │        │
                                  ACCEPT  │   continue  ┌──┴──────────────┐
                              (final_accept) │        │consecutive_same │
                                             │        │>= stop && ok?   │
                                             │        └──┬──────────┬──┘
                                             │        YES│          │NO
                                             │           │          │
                                             │       ACCEPT      continue
                                             │    (consecutive_same_N)
```

#### 核心判断逻辑 (代码)

```python
think_sufficient = check_min_think_gate(actual_len, min_think_tokens)
should_accept = False
accept_reason = ''

if decision_score >= prob_th and think_sufficient:
    # 置信度达标 + 思考充分 → 接受
    should_accept = True
    if enable_full_chain_confidence:
        accept_reason = f'full_chain_r{r_score:.2f}_a{a_score:.2f}'
    elif enable_dual_channel:
        accept_reason = f'dual_ch_r{r_score:.2f}_a{a_score:.2f}'
    elif enable_value_min_confidence:
        accept_reason = f'value_min_conf_{decision_score_raw:.2f}'
    else:
        accept_reason = 'confidence_above_threshold'

elif decision_score >= prob_th and not think_sufficient:
    # 置信度达标但思考不足 → 继续 (防止过早接受)
    accept_reason = f'conf_ok_think_insufficient_{actual_len}_need_{min_think_tokens}'

elif enable_full_chain_confidence and decision_score < prob_th and think_sufficient:
    # 完整链低置信 → 诊断哪个区低
    if r_score < a_score:
        accept_reason = f'chain_low_r{r_score:.2f}_a{a_score:.2f}'
    else:
        accept_reason = f'answer_low_r{r_score:.2f}_a{a_score:.2f}'

elif enable_dual_channel and decision_score < prob_th and think_sufficient:
    # 双通道低置信 → 诊断哪个区低
    if r_score < a_score:
        accept_reason = f'reasoning_zone_low_{r_score:.2f}_a{a_score:.2f}'
    else:
        accept_reason = f'action_zone_low_{r_score:.2f}_a{a_score:.2f}'

elif is_paired and think_sufficient:
    # 标签配对 + 思考充分 → 最终轮接受，否则继续
    if is_final:
        should_accept = True
        accept_reason = 'think_tag_paired_final_accept'
    else:
        accept_reason = f'think_tag_paired_conf_low_{decision_score:.2f}'

elif consecutive_same_count >= consecutive_same_stop and think_sufficient:
    # 连续相同答案 → 接受
    should_accept = True
    accept_reason = f'consecutive_same_{consecutive_same_count}'
```

### 8.3 Accept Reason 完整编码表

| 触发条件 | Accept Reason 格式 | should_accept | 中文含义 |
|---------|-------------------|:------------:|---------|
| P3.1 value_min 通过 + 思考充分 | `value_min_conf_{raw:.2f}` | ✅ | Value 最低概率达标 (logprob 域) |
| 双通道通过 + 思考充分 | `dual_ch_r{r:.2f}_a{a:.2f}` | ✅ | 双通道 AND 门通过 |
| 完整链通过 + 思考充分 | `full_chain_r{r:.2f}_a{a:.2f}` | ✅ | 完整链 AND 门通过 |
| 默认置信度通过 + 思考充分 | `confidence_above_threshold` | ✅ | 平均置信度达标 |
| 连续相同答案 + 思考充分 | `consecutive_same_{N}` | ✅ | 连续 N 次答案相同 |
| 思考标签配对 + 最终轮 | `think_tag_paired_final_accept` | ✅ | 思考标签自然闭合(最终轮) |
| 置信度达标但思考不足 | `conf_ok_think_insufficient_{actual}_need_{need}` | ❌ | 置信度达标但思考不足 |
| 双通道低置信 (推理区低) | `reasoning_zone_low_{r:.2f}_a{a:.2f}` | ❌ | 推理区低置信，需继续推理 |
| 双通道低置信 (动作区低) | `action_zone_low_{r:.2f}_a{a:.2f}` | ❌ | 动作区低置信，需继续推理 |
| 完整链低置信 (链低) | `chain_low_r{r:.2f}_a{a:.2f}` | ❌ | 思维链低置信 |
| 完整链低置信 (答案低) | `answer_low_r{r:.2f}_a{a:.2f}` | ❌ | 答案区低置信 |
| 思考标签配对但置信低 | `think_tag_paired_conf_low_{score:.2f}` | ❌ | 标签闭合但置信度不够 |
| 思考标签配对但不足 | `think_tag_paired_insufficient_{actual}` | ❌ | 标签闭合但思考 token 不足 |
| 安全门超时 | `safety_gate_fallback` | ✅ | 超时兜底 (非置信度判定) |
| 最终兜底 | `final_fallback` | ✅ | 达到最大轮次兜底 |
| 基线自由生成 | `baseline_free_generation` | ✅ | 非预算模式 (无置信度) |
| DEER 稳定试验 | `deer_trial_stable_{count}` | ✅ | DEER 试验答案稳定 |

### 8.4 min_think_tokens 门控

```python
def check_min_think_gate(actual_len, min_think_tokens):
    """最低思考 token 门控 — 防止模型过早闭合思考标签。"""
    if min_think_tokens <= 0:
        return True  # 门控关闭
    return actual_len >= min_think_tokens
```

**设计意图**：即使置信度达标，如果模型思考的 token 数不足 `min_think_tokens`（默认 100），也不会接受答案。这防止模型"敷衍"——生成少量思考后立即闭合标签给出答案。

### 8.5 think tag 配对的驱动作用

```python
# confidence.py
_THINK_OPEN_TAG = 'iesa'
_THINK_CLOSE_TAG = 'eiraot'

def is_think_tag_paired(text) -> bool:
    """判断开标签与闭标签是否配对出现。"""
    open_count = text.count(_THINK_OPEN_TAG)
    close_count = text.count(_THINK_CLOSE_TAG)
    return open_count == close_count
```

**在循环中的作用**：

1. **未配对 + stop_reason='stop' + actual_len < min_think_tokens**：
   - 模型过早闭合思考标签 → `reopen_think_tag()` 强制重新打开 + 继续思考
2. **已配对 + actual_len >= min_think_tokens**：
   - 模型自然完成思考 → 进入试探答案生成
3. **最终轮仍配对**：
   - 可作为接受条件之一 (`think_tag_paired_final_accept`)

---

## 9. 消融实验结果

### 9.1 各置信度模式对比

| 置信度模式 | 准确率 | 相对基线 | 延迟变化 | 说明 |
|-----------|--------|---------|---------|------|
| **基线 (无预算控制)** | 80% | — | — | 自由生成，无置信度门控 |
| **纯平均置信度 (不过滤结构 token)** | ~80% | ≈0 | -30% | 结构 token 掩盖 Value 不确定性，早停判定失准 |
| **Value Min Confidence + min_think_tokens** | **80%** | **≈0** | **-55%** | ✅ 核心创新：准确率不降，延迟大幅降低 |
| **Value Min Confidence (无 min_think_tokens)** | 75% | -5pp | -65% | 缺少最低思考门控，部分问题过早接受 |
| **双通道 (Dual Channel)** | 80% | ≈0 | -50% | 更精细诊断，但生产环境单通道已足够 |
| **完整链 (Full Chain)** | 80% | ≈0 | -48% | 跨轮累积评估，计算开销略高 |

### 9.2 关键发现

1. **Value Min Confidence 是唯一同时保持准确率并大幅降低延迟的优化**：
   - 结构 token 过滤是核心——不过滤时，平均置信度被结构 token 拉高，导致过早接受
   - Bottom-K 机制关注最不确定的部分，而非被平均值稀释

2. **min_think_tokens 门控是必要的**：
   - 没有门控时，部分简单问题在生成 < 100 token 时就闭合思考标签并给出答案
   - 虽然置信度高，但推理深度不足，导致准确率下降 5pp

### 9.3 Value Min Confidence 的延迟降低机制

```
延迟降低来源分解:

1. 更早正确接受 (贡献 ~35%):
   - 纯平均置信度：结构 token 拉高评分 → 即使 Value 不确定也继续推理
   - Value Min：准确识别 Value 确信 → 立即接受，节省后续轮次

2. 减少无效推理轮 (贡献 ~15%):
   - 纯平均置信度：需要多轮推理拉低结构 token 占比才能正确拒绝
   - Value Min：首轮即可识别 Value 不确信 → 正确拒绝 → 继续推理直至确信

3. min_think_tokens 防止过早接受 (贡献 ~5%):
   - 确保每题至少思考 100 token
   - 避免简单题"秒答"但答错
```

### 9.4 CONFIG 生产默认值

基于消融实验结果，生产环境 CONFIG 默认配置：

| 参数 | 默认值 | 来源 |
|------|--------|------|
| `enable_budget` | ON | 总开关 |
| `enable_value_min_confidence` | ON | P3.1 核心创新 |
| `value_min_bottom_k` | 5 | K=5 比 K=3 更稳定 |
| `min_think_tokens` | 100 | 必要门控 |
| `prob_th` | 0.95 | 严格阈值 |

---

## 10. 循环依赖解决方案

### 10.1 原始循环依赖链

重构前，项目存在以下循环导入链：

```
confidence.py  ──import──→  token_filter.py  ──import──→  __init__.py
     ↑                                                  │
     │                                                  │
     └──────────── import ←───────────────── context_control.py
```

**具体问题**：

1. `confidence.py` 需要 `is_structural_token()` → 从 `token_filter.py` 导入
2. `token_filter.py` 在 `naie/optimizations/` 包内 → 导入时触发 `__init__.py`
3. `__init__.py` 急切导入 `context_control.py` (用于 `apply_clean_context` 等函数)
4. `context_control.py` 需要 `cal_value_min_confidence()` → 从 `confidence.py` 导入
5. **循环**！Python 无法解析此导入链

### 10.2 第一次尝试：函数内懒导入

在 `confidence.py` 中使用函数内导入：

```python
# confidence.py — 函数内导入 (部分解决)

def cal_value_min_confidence(logprobs, tokens, bottom_k=3):
    """函数内导入，避免模块级循环。"""
    from naie.optimizations.token_filter import cal_value_min_confidence as _cal_vmc
    return _cal_vmc(logprobs, tokens, bottom_k=bottom_k)
```

**问题**：这只解决了 `confidence.py → token_filter.py` 方向的循环，但 `__init__.py` 的急切导入仍然触发循环。

### 10.3 最终解决方案：两层防护

#### 防护层 1：`_struct_token_core.py` — 提取核心逻辑到独立模块

```
_struct_token_core.py  ←── 零项目内导入 (真正的独立模块)
     ↑          ↑
     │          │
token_filter.py   confidence.py
```

将 `is_structural_token()` 及其所有依赖数据（`DEFAULT_STRUCT_TOKENS`、`DEFAULT_KEY_NAMES`、`_WHITESPACE_RE` 等）提取到 `_struct_token_core.py`。该模块**无任何项目内导入**（只有 `import re`），因此不会触发任何循环。

```python
# naie/optimizations/_struct_token_core.py

"""结构化Token判断核心 — 独立模块，无任何外部导入，打破循环依赖。

⚠️ 不要在本模块中添加任何项目内 import！
"""
import re  # 标准库，无循环风险

DEFAULT_STRUCT_TOKENS = { ... }
DEFAULT_KEY_NAMES = set()
_WHITESPACE_RE = re.compile(r'^\\s+$')
_SPECIAL_TOKENS = ('<|im_start|>', '<|im_end|>', '<|im_sep|>')

_struct_tokens = set(DEFAULT_STRUCT_TOKENS)
_key_names = set(DEFAULT_KEY_NAMES)

def set_task_key_names(key_names): ...
def set_extra_struct_tokens(tokens): ...
def is_structural_token(token: str) -> bool: ...
def extract_value_logprobs(logprobs, tokens): ...
```

**关键约束**：`_struct_token_core.py` 中**绝对不能**添加任何 `from naie.*` 的导入。这是整个循环依赖解决方案的基石。

#### 防护层 2：`__init__.py` — PEP 562 懒加载

```python
# naie/optimizations/__init__.py

"""naie.optimizations — 懒加载包 (PEP 562)

此包使用 __getattr__ 实现懒加载，避免循环导入问题。
通过懒加载，只有实际访问的符号才会触发对应子模块的导入，
不会因 __init__.py 的急切导入而触发循环依赖。
"""

_LAZY_IMPORTS = {
    # token_filter.py (P3.1)
    'is_structural_token': ('naie.optimizations.token_filter', 'is_structural_token'),
    'set_task_key_names': ('naie.optimizations.token_filter', 'set_task_key_names'),
    'cal_value_min_confidence': ('naie.optimizations.token_filter', 'cal_value_min_confidence'),
    'cal_reasoning_zone_confidence': ('naie.optimizations.token_filter', 'cal_reasoning_zone_confidence'),
    # confidence_gates.py
    'compute_decision_score': ('naie.optimizations.confidence_gates', 'compute_decision_score'),
    'compute_dual_channel_score': ('naie.optimizations.confidence_gates', 'compute_dual_channel_score'),
    # early_stopping.py
    'make_accept_decision': ('naie.optimizations.early_stopping', 'make_accept_decision'),
    # ... 其他模块 ...
}

def __getattr__(name):
    """PEP 562 — 按需懒加载，避免循环导入。"""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        value = getattr(mod, attr_name)
        # 缓存到模块命名空间，后续访问不再触发 __getattr__
        globals()[name] = value
        return value
    raise AttributeError(f"module 'naie.optimizations' has no attribute {name!r}")

def __dir__():
    """支持 dir() 和 IDE 自动补全。"""
    return list(_LAZY_IMPORTS.keys())
```

**PEP 562 的工作原理**：

1. 当 Python 执行 `from naie.optimizations import is_structural_token` 时
2. Python 先查找 `__init__.py` 的模块级命名空间 → 未找到
3. Python 调用 `__getattr__('is_structural_token')`
4. `__getattr__` 从 `_LAZY_IMPORTS` 找到映射 → `importlib.import_module('naie.optimizations.token_filter')`
5. 此时才触发 `token_filter.py` 的导入（而非包加载时急切导入）
6. 结果缓存到 `globals()`，后续访问不再触发 `__getattr__`

**为什么这能打破循环**：

- 原始问题：`import naie.optimizations` → `__init__.py` 急切导入 `context_control` → 导入 `confidence` → 导入 `token_filter` → 触发 `__init__.py`（已在加载中）→ 循环
- PEP 562 方案：`import naie.optimizations` → `__init__.py` 只定义 `__getattr__`（不导入任何子模块）→ **没有急切导入** → 循环打断
- 只有当代码实际使用 `from naie.optimizations import xxx` 时，才会按需导入对应子模块

### 10.4 最终模块依赖关系

```
                    _struct_token_core.py  (零项目内导入)
                         ↑         ↑
                         │         │
               token_filter.py   confidence.py
                    ↑                ↑         ↑
                    │                │         │
            confidence_gates.py      │   context_control.py
                    ↑                │         ↑
                    │                │         │
               __init__.py (PEP 562 懒加载) ────┘
                    ↑
                    │
            budget_forcing.py
```

**无循环**：
- `_struct_token_core.py` → 无项目内依赖
- `token_filter.py` → 只依赖 `_struct_token_core`
- `confidence.py` → 只依赖 `_struct_token_core` + 函数内懒导入 `token_filter`
- `confidence_gates.py` → 依赖 `confidence.py` + `token_filter.py`
- `context_control.py` → 依赖 `confidence.py`
- `__init__.py` → 无急切导入，通过 `__getattr__` 按需加载

### 10.5 维护注意事项

⚠️ **以下操作可能重新引入循环依赖**：

1. 在 `_struct_token_core.py` 中添加任何 `from naie.*` 导入
2. 在 `__init__.py` 中添加急切导入（如 `from .token_filter import *`）
3. 在 `token_filter.py` 中导入 `confidence_gates.py`
4. 在 `confidence.py` 模块级导入 `token_filter.py`（应使用函数内导入）

**验证方法**：

```python
# 测试循环依赖是否已解决
python -c "import sys; sys.path.insert(0, '.'); from naie.optimizations import is_structural_token; print('OK')"
python -c "import sys; sys.path.insert(0, '.'); from naie.models.confidence import cal_value_min_confidence; print('OK')"
python -c "import sys; sys.path.insert(0, '.'); from naie.optimizations.confidence_gates import compute_dual_channel_score; print('OK')"
```

---

## 附录 A：Logprob 域 vs 概率域

### A.1 两个计算域

| 域 | 范围 | 用途 |
|----|------|------|
| **Logprob 域** | (-∞, 0] | 内部计算: 排序、求和、取均值 |
| **概率域** | [0, 1] | 外部比较: 与 prob_th 比较、accept_reason 展示 |

**转换**：`probability = exp(logprob)`

### A.2 为什么在 logprob 域计算

1. **排序等价**：logprob 是单调递增函数，排序结果与概率域一致
2. **数值稳定**：概率域的极小值 (如 0.001) 在 logprob 域是 -6.9，避免浮点下溢
3. **加法替代乘法**：多个 token 的联合概率 = logprob 之和的 exp
4. **均值语义**：logprob 均值 ≈ 几何均值 (概率域)，比算术均值更合理

### A.3 返回值约定

| 返回值 | 域 | 说明 |
|--------|----|------|
| `decision_score` | [0,1] 概率域 | 始终在概率域，与 `prob_th` 直接比较 |
| `value_min_raw` | logprob 域 | 用于 accept_reason 编码: `value_min_conf_{raw:.2f}` |
| `reasoning_score_raw` | logprob 域 | 用于 accept_reason 编码 |
| `action_score_raw` | logprob 域 | 用于 accept_reason 编码 |
| `confidence` | [0,1] 概率域 | `cal_confidence()` = exp(logprobs) 的算术均值 |
| `perplexity` | [1, +∞) | `exp(-mean(logprobs))` |

### A.4 Accept Reason 中的数值解读

| Accept Reason | 数值域 | 解读方法 |
|--------------|--------|---------|
| `value_min_conf_-2.30` | logprob 域 | prob = exp(-2.30) = 0.10，Value 最低概率仅 10% |
| `value_min_conf_-0.05` | logprob 域 | prob = exp(-0.05) = 0.95，Value 最低概率 95% (刚好达标) |
| `dual_ch_r0.85_a0.72` | 概率域 | 推理区 85% + 动作区 72% → AND 门 72% |
| `full_chain_r0.91_a0.15` | 概率域 | 思维链 91% + 答案 15% → AND 门 15% (答案太不确定) |

---

## 附录 B：模块关系总结

```
naie/optimizations/_struct_token_core.py   ← 独立模块，零项目内导入
  ├── is_structural_token()                   # 结构 token 判断核心
  ├── set_task_key_names()                    # 注入任务键名
  ├── set_extra_struct_tokens()               # 追加结构 token
  ├── get_key_names()                         # 获取当前键名集合
  ├── extract_value_logprobs()                # 过滤结构 token，保留 Value logprobs
  └── DEFAULT_STRUCT_TOKENS                   # 默认结构 token 集合

naie/optimizations/token_filter.py          ← API 门面 (re-export + 扩展)
  ├── is_structural_token() (re-export)       # 结构 token 判断
  ├── set_task_key_names() (re-export)        # 注入键名
  ├── extract_value_logprobs() (re-export)    # 过滤函数
  ├── cal_value_min_confidence()              # Value Bottom-K (P3.1 核心创新)
  └── cal_reasoning_zone_confidence()         # 推理区 Min-Percentile (宽容策略)

naie/models/confidence.py                   ← 基础置信度计算
  ├── cal_confidence()                        # 平均 exp(logprobs) — 基础置信度
  ├── cal_perplexity()                        # 困惑度
  ├── cal_value_min_confidence()              # Value Bottom-K (函数内懒导入)
  ├── cal_reasoning_zone_confidence()         # 推理区 Min-% (函数内懒导入)
  ├── cal_full_chain_confidence()             # 完整链 AND 门 (P3.4)
  ├── is_think_tag_paired()                   # 思考标签配对检查
  ├── get_think_closing_tag()                 # 返回闭标签
  └── reopen_think_tag()                      # 重新打开思考标签

naie/optimizations/confidence_gates.py      ← 统一评分入口
  ├── compute_decision_score()                # 单通道评分 (向后兼容)
  └── compute_dual_channel_score()            # 统一评分 — 4 模式 (主入口)

naie/optimizations/early_stopping.py        ← 早停判断
  ├── make_accept_decision()                  # 核心早停逻辑
  ├── check_min_think_gate()                  # 最低思考门
  └── check_consecutive_same()                # 连续相同答案检测

naie/optimizations/__init__.py              ← PEP 562 懒加载包
  ├── __getattr__()                           # 按需导入，避免循环依赖
  └── __dir__()                               # 支持 IDE 自动补全
```

---

## 附录 C：常见问题排查

### C.1 置信度始终很低 (< 0.3)

**可能原因**：
1. `ans_tokens` 为 `None` → Value Min 退化回退逻辑 → 使用全局最小 logprob
2. `value_min_bottom_k` 过大 → 取了太多 Value token 的均值
3. 模型确实不确定 → 正常行为，应继续推理

**排查方法**：
```python
# 在 budget_forcing.py 中添加调试输出
print(f"ans_logprobs count: {len(ans_logprobs) if ans_logprobs else 0}")
print(f"ans_tokens: {ans_tokens[:20] if ans_tokens else 'None'}")
print(f"value_min_raw: {value_min_raw:.4f}")
```

### C.2 置信度始终很高但答案错误

**可能原因**：
1. `enable_value_min_confidence=OFF` → 使用纯平均置信度 → 结构 token 拉高评分
2. 任务键名未注入 → JSON 键名被视为 Value token → 高 logprob 污染
3. `prob_th` 过低 → 接受阈值太宽松

**排查方法**：
```python
# 检查任务键名是否已注入
from naie.optimizations.token_filter import get_key_names
print(f"Current key_names: {get_key_names()}")

# 检查 CONFIG
from naie.config.settings import get_value
print(f"enable_value_min_confidence: {get_value('enable_value_min_confidence')}")
print(f"prob_th: {get_value('prob_th')}")
```

### C.3 循环导入错误

**错误信息**：`ImportError: cannot import name 'xxx' from partially initialized module`

**排查方法**：
1. 检查是否在 `_struct_token_core.py` 中添加了项目内导入
2. 检查是否在 `__init__.py` 中添加了急切导入
3. 运行验证命令：
```bash
python -c "import sys; sys.path.insert(0, '.'); from naie.optimizations import is_structural_token; print('OK')"
```

---

## 附录 D：软截断 (Soft Truncation) 与答案完整性

### D.1 核心原则

**思考可以截断，答案必须完整。** `enable_soft_truncation=True`（默认）时，所有因 `stop_reason='length'` 导致的思考内容截断都在自然断点处闭合思考标签，绝不截断答案。

### D.2 截断生效点

`_truncate_thinking_at_natural_breakpoint()` 在以下 6 个位置生效：

| # | 截断位置 | 说明 |
|---|---------|------|
| 1 | 初始思考生成 (L431) | `check_length` 限制导致首次思考被截断 |
| 2 | 标签配对 + min_think_tokens 门控继续 (L471) | `reopen_think_tag()` 后追加思考被截断 |
| 3 | 标签未配对 + min_think_tokens 门控继续 (L491) | 继续思考被截断 |
| 4 | stride 追加思考 (L608) | `think_stride` 限制导致追加思考被截断 |
| 5 | ACCEPT 后追加完整答案 (L561) | 接受答案后，在推理内容上追加答案时截断 |
| 6 | 循环耗尽最终答案 (L657) | 最终兜底答案追加时截断 |

### D.3 自然断点字符

```
。 ！ ？ . ! ? \n } 】 」
```

从内容末尾向前搜索 500 字符范围内最近的自然断点，在该断点后截断并闭合 `_THINK_CLOSE_TAG` (`eiraot`) 标签。

### D.4 截断条件

仅当同时满足以下条件时才执行截断：
1. `enable_soft_truncation=True`
2. `stop_reason='length'`（思考被 max_tokens/stride 截断）
3. `_THINK_CLOSE_TAG` 不在内容中（推理仍在进行）

如果 `_THINK_CLOSE_TAG` (`eiraot`) 已存在于内容中，说明推理已完整结束，答案跟在后面，不截断。

### D.5 诱导 Prompt 与 Think Tag 配对的交互

`make_induced_prompt()` 的行为取决于思考标签状态：

| 条件 | 行为 | 诱导语 |
|------|------|--------|
| `is_think_tag_paired(prompt)=True` | `induced_prompt = prompt`（直接使用） | 无需追加——闭标签已存在，直接请求答案 |
| `is_think_tag_paired(prompt)=False` | `induced_prompt = make_induced_prompt(prompt)` | `"... \n OK, I have completed my thinking...\n<think_close_tag>\n"` |

---

*文档版本：v2.4 | 最后更新：2026-07-08 | 基于代码实际实现编写*
