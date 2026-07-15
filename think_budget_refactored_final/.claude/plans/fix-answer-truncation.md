# Plan: 修复 budget_forcing.py 答案截断问题

## 问题分析

当前代码中存在多处答案可能被截断的位置，核心原则违反：**max_tokens 应只控制推理 token，答案永远不被截断**。

### 截断风险点 (budget_forcing.py)

| 位置 | 代码 | 问题 |
|------|------|------|
| L430 | `self._call_api(final_result[0], 4096)` | 硬编码 4096，长答案仍可截断 |
| L493 | `self._call_api(prompt, 4096)` | 最终 fallback 硬编码 4096 |
| L526 | `self._call_api(final_result[0], 4096)` | 最终答案补全硬编码 4096 |
| L1003 | `_call(final_result[0], 4096)` | inline 路径同上 |
| L1008 | `_call(final_result[0], 4096)` | inline 路径同上 |
| L1092 | `_call(prompt, check_answer_len)` | **严重 bug**: 最终 fallback 用 check_answer_len (可小至20) |
| L1125 | `_call(final_result[0], 4096)` | 最终答案补全 |
| L1130 | `_call(final_result[0], 4096)` | 最终答案补全 |
| L1567 | `await _acall(final_result[0], 4096)` | async 路径同上 |
| L1572 | `await _acall(final_result[0], 4096)` | async 路径同上 |
| L1656 | `await _acall(prompt, check_answer_len)` | **严重 bug**: async 最终 fallback |
| L1689 | `await _acall(final_result[0], 4096)` | async 路径 |
| L1694 | `await _acall(final_result[0], 4096)` | async 路径 |
| L822 | `chat_client.model_call(prompt, max_tokens)` | 无 confidence 路径：推理+答案共用 max_tokens 预算 |
| L1385 | `async_model_call(model_name, prompt, max_tokens)` | async 无 confidence 路径同上 |

### budget_forcing_optimized.py 的问题

| 位置 | 代码 | 问题 |
|------|------|------|
| L43 | `max_tokens_answer = max_tokens` | max_tokens_answer=推理预算，不对 |
| L191 | `check_answer_len = 4096` | 硬编码 |
| L229 | `chat_client.model_call(content_candidate, max_tokens_answer)` | max_tokens_answer=max_tokens 是推理预算 |

## 修复方案

### 核心思路

引入常量 `_UNLIMITED_ANSWER_TOKENS = 65536`（足够大，远超任何实际答案长度），所有"最终答案生成"位置统一使用此值，而不是硬编码 4096 或 check_answer_len。

**为什么选 65536 而非"无限"**：Completions API 的 max_tokens 参数有上限（取决于模型），设一个足够大的值既保证答案不被截断，又不会触发 API 错误。65536 是大多数 OpenAI 兼容 API 支持的上限。

### 修改详情

#### 1. budget_forcing.py — 三条路径统一修复

**文件顶部增加常量**:
```python
# 答案永远不被截断 — 最终答案生成使用此值，远超任何实际答案长度
# max_tokens 只控制推理 token，最终答案生成不受 max_tokens 限制
_UNLIMITED_ANSWER_TOKENS = 65536
```

**ReasoningRunner.run() (L417-442, L488-535)**:
- L430: `self._call_api(final_result[0], 4096)` → `self._call_api(final_result[0], _UNLIMITED_ANSWER_TOKENS)`
- L493: `self._call_api(prompt, 4096)` → `self._call_api(prompt, _UNLIMITED_ANSWER_TOKENS)`
- L526: `self._call_api(final_result[0], 4096)` → `self._call_api(final_result[0], _UNLIMITED_ANSWER_TOKENS)`

**call_model() inline 路径 (L992-1031, L1089-1149)**:
- L1003: `_call(final_result[0], 4096)` → `_call(final_result[0], _UNLIMITED_ANSWER_TOKENS)`
- L1008: `_call(final_result[0], 4096)` → `_call(final_result[0], _UNLIMITED_ANSWER_TOKENS)`
- L1092: `_call(prompt, check_answer_len)` → `_call(prompt, _UNLIMITED_ANSWER_TOKENS)` (**关键修复**)
- L1125: `_call(final_result[0], 4096)` → `_call(final_result[0], _UNLIMITED_ANSWER_TOKENS)`
- L1130: `_call(final_result[0], 4096)` → `_call(final_result[0], _UNLIMITED_ANSWER_TOKENS)`

**call_model_async() (L1556-1595, L1653-1713)**:
- L1567: `await _acall(final_result[0], 4096)` → `await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)`
- L1572: `await _acall(final_result[0], 4096)` → `await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)`
- L1656: `await _acall(prompt, check_answer_len)` → `await _acall(prompt, _UNLIMITED_ANSWER_TOKENS)` (**关键修复**)
- L1689: `await _acall(final_result[0], 4096)` → `await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)`
- L1694: `await _acall(final_result[0], 4096)` → `await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)`

**无 confidence 路径 (L821-837, L1384-1400)**:
- L822: `chat_client.model_call(prompt, max_tokens)` → `chat_client.model_call(prompt, _UNLIMITED_ANSWER_TOKENS)` — 无 confidence 时推理不受控，答案直接给足 token
- L1385: `await async_model_call(model_name, prompt, max_tokens)` → `await async_model_call(model_name, prompt, _UNLIMITED_ANSWER_TOKENS)` — async 同上

**_truncate_thinking_at_natural_breakpoint (L553-575)**:
- 此函数已经正确：只截断推理（未闭合 think tag 的内容），绝不截断答案
- 不需要修改

#### 2. budget_forcing_optimized.py — 同步修复

- L43: `max_tokens_answer = max_tokens` → `max_tokens_answer = 65536`
- L191: `check_answer_len = 4096` → `check_answer_len = 65536`
- L229: 保持使用 `max_tokens_answer`（值已改为 65536）
- 增加注释说明 max_tokens_answer 不再跟随 max_tokens

#### 3. 更新现有注释

L95-96 的注释已经正确指向了设计意图，更新为具体常量引用。

### 不修改的位置（确认正确）

- `check_answer_len` 在循环内试探答案时使用（L336, L934, L1498）— 这是正确的，试探答案用少量 token 看置信度
- `_truncate_thinking_at_natural_breakpoint()` — 只截断推理，不截断答案，正确
- safety_gate fallback — 只是关闭 think tag 并返回已有内容，不生成新 token，正确

## 验证方式

修改后运行 `python test_single_case.py` 确认功能正常。
