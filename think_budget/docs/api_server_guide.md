# API 服务指南

## 概述

`naie/api_server.py` 是生产版 API 服务器，提供兼容 OpenAI 格式的 BudgetForcing 代理接口。

**模型端点**：默认模型为 `mg2hg`；部署时通过 `MG2HG_URL` 和 `MG2HG_KEY` 环境变量注入 Completions API 端点与凭据。
**model 字段强制映射为 mg2hg**，不接受外部覆盖。

### v3 改进

- 集成 `scene_classifier` 自动推断商用场景
- 支持显式指定 `scene_type`（外部可覆盖自动推断）
- 历史查询集成 `task_type` 过滤（而非硬编码 `'naie'`）
- 并发控制：`asyncio.Semaphore` 限制同时推理请求数
- `asyncio.to_thread` 将同步推理卸载到线程池，不阻塞事件循环
- httpx 连接池扩容匹配并发度

---

## 启动服务器

### 基本启动

```bash
# 默认端口 8000，并发度 4
python naie/api_server.py

# 指定端口和并发度
python naie/api_server.py --port 8001 --concurrency 8

# 多 worker 进程（需确保后端 API 能承受 workers×concurrency 总并发）
python naie/api_server.py --workers 2 --concurrency 4

# 使用 uvicorn 直接启动
uvicorn naie.api_server:app --host 0.0.0.0 --port 8000
```

### 启动参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | `8000` | 服务端口 |
| `--host` | `0.0.0.0` | 监听地址 |
| `--concurrency` | `4` | 最大并发推理请求数。BudgetForcing 每请求内部 3~10 次 API 调用，concurrency=8 时后端最坏承受 ~80 并发 API 请求 |
| `--workers` | `1` | uvicorn worker 进程数。多 worker 需确保后端能承受 `workers × concurrency` 总并发 |

### 启动输出示例

```
[API Server] host=0.0.0.0 port=8000 concurrency=4 workers=1
  最大并发推理请求: 4
  预估后端API最大并发: ~40 (按num_thinking=8估算)
```

---

## 端点参考

### POST `/v1/chat/completions` — 主推理接口

兼容 OpenAI ChatCompletion 格式的 BudgetForcing 推理接口。支持 Budget 模式（默认）和自由生成模式。

**处理流程**：
1. 并发控制（Semaphore）
2. 场景分类（自动推断或外部指定）
3. 任务类型推断（自动推断或外部指定）
4. 历史统计查询（按 task_type 过滤）
5. 构建 budget_config（CONFIG + 请求覆盖）
6. 调用 `budget_call_model()`（通过 `asyncio.to_thread` 卸载到线程池）
7. 解析响应、分离思考/答案、计算 token 数
8. 返回 OpenAI 格式响应 + budget_info

### GET `/health` — 健康检查

返回服务状态和并发信息。

**响应**：
```json
{
  "status": "ok",
  "concurrency": 4,
  "semaphore_available": 3
}
```

### GET `/v1/config` — 读取当前运行配置

返回 `THINK_BUDGET_CONFIG_47` 的完整配置。

**响应**：
```json
{
  "config": {
    "enable_budget": true,
    "check_length": 200,
    "prob_th": 0.95,
    ...
  }
}
```

### POST `/v1/config` — 更新运行配置

运行时覆盖 CONFIG 值，立即生效（重启后失效）。

**请求**：
```json
{
  "prob_th": 0.90,
  "enable_value_min_confidence": false
}
```

**响应**：返回更新后的完整配置。

---

## 请求格式

### ChatCompletionRequest 字段

#### 基本字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | string | `"mg2hg"` | 模型名（强制映射为 mg2hg） |
| `messages` | List[Message] | **必填** | 消息列表，最后一条必须是 user 角色 |
| `temperature` | float | `0` | 温度 |
| `max_tokens` | int | `8192` | 最大 token 数 |
| `enable_budget` | bool | `True` | BudgetForcing 开关。`False` 时直接调用 model_client 不走 BudgetForcing |
| `reference` | string | `""` | 参考答案（用于评估） |
| `scene_type` | string | `None` | 显式指定场景类型（不指定则自动推断） |

#### P1 核心参数

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `check_length` | int | `None` | 初始思考 token 数 |
| `think_stride` | int | `None` | 每轮思考增量 |
| `num_thinking` | int | `None` | 最大尝试轮次 |
| `check_answer_len` | int | `None` | 试探答案长度 |
| `prob_th` | float | `None` | 置信度阈值 |
| `min_think_tokens` | int | `None` | 最低思考 token |
| `max_api_calls` | int | `None` | 单题最大 API 调用次数 |
| `baseline_time_limit` | float | `None` | 基线延迟限制（秒） |

#### P3.1 Value 最低概率

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_value_min_confidence` | bool | `None` | Value Min Confidence 开关 |
| `value_min_bottom_k` | int | `None` | Bottom-K 取值数 |

#### P3.3 / P3.4 / 安全门

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_clean_context` | bool | `None` | Clean Context 开关 |
| `enable_full_chain_confidence` | bool | `None` | 完整链置信度 |
| `full_chain_min_percentile` | float | `None` | 思维链最差百分比 |
| `enable_safety_gate` | bool | `None` | 安全门开关 |
| `enable_soft_truncation` | bool | `None` | 软截断开关 |

#### 任务类型判断

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_task_type_budget` | bool | `None` | 任务类型预算开关 |
| `task_type` | string | `None` | 手动覆盖任务类型（映射为 `task_type_override`）。可选值：retrieval / coding / math / reasoning / classification / tool_call / fault_diagnosis / knowledge_qa / planning / general |
| `default_task_type` | string | `None` | 默认任务类型 |
| `custom_task_types_path` | string | `None` | 自定义任务类型配置路径 |

#### 历史轨迹预算

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_history_budget` | bool | `None` | 历史预算开关 |
| `history_budget_multiplier` | float | `None` | warm-start 预算乘数 |
| `history_low_success_boost` | float | `None` | 低成功率预算放大 |
| `history_stride_percentile` | float | `None` | 步长历史百分位 |
| `new_task_unlimited` | bool | `None` | 新任务不设限制 |
| `history_warmup_count` | int | `None` | warm-start 条数阈值 |
| `history_warmup_strategy` | string | `None` | warm-start 策略（conservative/moderate/aggressive） |
| `auto_cluster_threshold` | int | `None` | 自动聚类阈值 |

#### 人工可控超参

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `fixed_budget_tokens` | int | `None` | 固定 budget token 消耗（0=动态计算） |
| `fixed_num_thinking` | int | `None` | 固定最大轮次（0=动态计算） |
| `fixed_think_stride` | int | `None` | 固定步长（0=动态计算） |
| `fixed_check_length` | int | `None` | 固定初始思考长度（0=动态计算） |
| `fixed_prob_th` | float | `None` | 固定置信度阈值（0.0=使用 prob_th） |
| `fixed_min_think_tokens` | int | `None` | 固定最低思考 token（0=动态计算） |

#### 高级参数

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_dual_channel` | bool | `None` | 双通道评估开关 |
| `reasoning_min_percentile` | float | `None` | 推理区 Min-% 百分位 |
| `consecutive_same_stop` | int | `None` | 连续相同次数阈值 |
| `enable_difficulty_estimator` | bool | `None` | 难度估计器开关 |
| `difficulty_check_length_base` | int | `None` | 难度估计器：简单题 check_length |
| `difficulty_check_length_max` | int | `None` | 难度估计器：困难题 check_length |
| `difficulty_min_think_base` | int | `None` | 难度估计器：简单题 min_think |
| `difficulty_min_think_max` | int | `None` | 难度估计器：困难题 min_think |
| `difficulty_stride_base` | int | `None` | 难度估计器：简单题 stride |
| `difficulty_stride_max` | int | `None` | 难度估计器：困难题 stride |
| `difficulty_num_thinking_base` | int | `None` | 难度估计器：简单题 num_thinking |
| `difficulty_num_thinking_max` | int | `None` | 难度估计器：困难题 num_thinking |
| `difficulty_answer_len_base` | int | `None` | 难度估计器：简单题 answer_len |
| `difficulty_answer_len_max` | int | `None` | 难度估计器：困难题 answer_len |
| `enable_deer_trial` | bool | `None` | DEER trial 开关 |

**所有 `None` 字段**：不传则使用 CONFIG 默认值。

---

## 响应格式

### Budget 模式响应（enable_budget=True，默认）

```json
{
  "id": "chatcmpl-123456",
  "object": "chat.completion",
  "created": 1719800000,
  "model": "mg2hg",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "iesa思考内容eiraot\n{\"tool\": \"queryUser\", \"IMSI\": \"460001234567890\"}",
      "reasoning_content": "思考内容"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 500,
    "reasoning_tokens": 200
  },
  "budget_info": {
    "accept_reason": "value_min_conf_-0.23",
    "confidence": 0.92,
    "perplexity": 1.5,
    "value_min_confidence": -0.23,
    "api_call_count": 4,
    "thinking_rounds": 3,
    "thinking_tokens": 200,
    "elapsed_time": 5.32,
    "difficulty": null,
    "scene_type": "tool_call",
    "scene_confidence": 0.85,
    "task_type": "tool_call"
  }
}
```

### budget_info 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `accept_reason` | string | 接受原因机器码（详见 [置信度文档](confidence_estimation.md)） |
| `confidence` | float | 平均 exp(logprobs) 置信度 [0,1] |
| `perplexity` | float | 困惑度 |
| `value_min_confidence` | float | Value Min Confidence（logprob 域原始值，负数） |
| `api_call_count` | int | 本次推理的总 API 调用次数 |
| `thinking_rounds` | int | BudgetForcing 循环轮次 |
| `thinking_tokens` | int | 思考 token 数（Qwen tokenizer 计算） |
| `elapsed_time` | float | 推理耗时（秒） |
| `difficulty` | dict/null | 难度估计结果（仅 enable_difficulty_estimator 时） |
| `scene_type` | string | 推断/指定的商用场景类型 |
| `scene_confidence` | float | 场景推断置信度（显式指定时为 1.0） |
| `task_type` | string | 推断/指定的任务类型 |

### 非 Budget 模式响应（enable_budget=False）

```json
{
  "id": "chatcmpl-123456",
  "object": "chat.completion",
  "created": 1719800000,
  "model": "mg2hg",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "答案内容",
      "reasoning_content": "思考内容"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 500,
    "reasoning_tokens": 200
  }
}
```

非 Budget 模式不返回 `budget_info` 字段。

---

## 并发控制

### 机制

| 组件 | 说明 |
|------|------|
| **asyncio.Semaphore** | 限制同时进行的推理请求数 |
| **asyncio.to_thread** | 将同步 `budget_call_model` 卸载到线程池，避免阻塞事件循环 |
| **httpx 连接池扩容** | `max_connections = concurrency × 4`，`max_keepalive = concurrency × 2` |

### 并发度选择

| concurrency | 预估后端 API 最大并发 | 适用场景 |
|-------------|---------------------|---------|
| 2 | ~20 | 低负载测试 |
| 4 | ~40 | 生产环境（默认） |
| 8 | ~80 | 高并发场景 |
| 16 | ~160 | 压力测试（需确保后端能承受） |

> BudgetForcing 每个请求内部发起 3~10 次 API 调用（num_thinking 轮 + 1 次最终调用）

### 超出并发上限

超出并发上限的请求会**排队等待**，而非直接拒绝。等待时间取决于正在进行的推理请求耗时。

---

## 任务类型推断

API 服务器自动集成场景分类和任务类型推断：

```
请求到达
  ├─ scene_type 字段非空?
  │   └─ Yes → 使用指定的 scene_type（confidence=1.0）
  │   └─ No  → classify_scene(prompt) → 自动推断
  │
  ├─ task_type 字段非空?
  │   └─ Yes → 使用指定的 task_type（映射为 task_type_override）
  │   └─ No  → classify_task_type(prompt, scene_info) → 自动推断
  │
  └─ 历史统计查询
      → get_category_stats(prompt, task_type, scene_type)
      → get_scene_stats(scene_type, task_type)
      → 注入 budget_config
```

**覆盖优先级**：外部显式指定（`scene_type`/`task_type` 字段）> 自动推断

**关键改进**：历史查询使用推断的 `task_type`（而非硬编码 `'naie'`），使历史统计能按任务类型精确过滤。

---

## 客户端示例

### curl 示例

#### 基本 Budget 模式请求

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mg2hg",
    "messages": [{"role": "user", "content": "查询IMSI为460001234567890的用户信息"}]
  }'
```

#### 非 Budget 模式（自由生成）

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mg2hg",
    "messages": [{"role": "user", "content": "查询用户信息"}],
    "enable_budget": false
  }'
```

#### 自定义置信度阈值

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mg2hg",
    "messages": [{"role": "user", "content": "查询用户信息"}],
    "prob_th": 0.90,
    "enable_value_min_confidence": true,
    "value_min_bottom_k": 3
  }'
```

#### 固定预算

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mg2hg",
    "messages": [{"role": "user", "content": "诊断故障"}],
    "fixed_budget_tokens": 2000,
    "fixed_num_thinking": 5
  }'
```

#### 指定场景和任务类型

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mg2hg",
    "messages": [{"role": "user", "content": "查询故障单"}],
    "scene_type": "fault_diagnosis",
    "task_type": "fault_diagnosis"
  }'
```

#### 健康检查

```bash
curl http://localhost:8000/health
```

返回：
```json
{"status": "ok", "concurrency": 4, "semaphore_available": 3}
```

#### 读取配置

```bash
curl http://localhost:8000/v1/config
```

#### 运行时修改配置

```bash
curl -X POST http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{"prob_th": 0.90, "enable_value_min_confidence": false}'
```

### Python requests 示例

```python
import requests

# 基本 Budget 请求
response = requests.post(
    "http://localhost:8000/v1/chat/completions",
    json={
        "model": "mg2hg",
        "messages": [{"role": "user", "content": "查询IMSI为460001234567890的用户信息"}],
    }
)
result = response.json()

# 提取答案
answer = result["choices"][0]["message"]["content"]
thinking = result["choices"][0]["message"]["reasoning_content"]
budget_info = result["budget_info"]

print(f"答案: {answer}")
print(f"思考 tokens: {budget_info['thinking_tokens']}")
print(f"接受原因: {budget_info['accept_reason']}")
print(f"任务类型: {budget_info['task_type']}")
print(f"场景类型: {budget_info['scene_type']}")
print(f"耗时: {budget_info['elapsed_time']}s")
```

```python
# 自定义参数请求
response = requests.post(
    "http://localhost:8000/v1/chat/completions",
    json={
        "model": "mg2hg",
        "messages": [{"role": "user", "content": "诊断告警原因"}],
        "prob_th": 0.90,
        "task_type": "fault_diagnosis",
        "fixed_budget_tokens": 2000,
        "enable_value_min_confidence": True,
        "value_min_bottom_k": 5,
    }
)
```

```python
# 非 Budget 模式（自由生成）
response = requests.post(
    "http://localhost:8000/v1/chat/completions",
    json={
        "model": "mg2hg",
        "messages": [{"role": "user", "content": "查询用户信息"}],
        "enable_budget": False,
    }
)
```

---

## 注意事项

- **model 默认映射为 mg2hg**：端点由 `MG2HG_URL` 提供，不再在源码或文档中固定内部地址。
- **NO_PROXY 已内置**：服务器自动设置 `NO_PROXY` 环境变量
- **思考标签格式**：标准格式 `iesa` / `eiraot`，API 返回中 `reasoning_content` 字段包含纯思考内容
- **错误处理**：内部异常返回 HTTP 500 + 异常类型和消息
- **请求字段覆盖**：仅影响当前请求，不修改全局 CONFIG；`None` 值字段使用 CONFIG 默认值
- **消息要求**：`messages` 不能为空，最后一条必须是 `user` 角色
