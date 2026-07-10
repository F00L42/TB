# DEER 思维链压缩模块 — Agent 参考文档

## 1. 模块概述

DEER (Dynamic Early Exit Reasoning) 是一个集成在 vLLM Server 进程中的思维链压缩模块。其核心思想是：让模型先自由思考一段长度，然后反复执行"短程思考 → 预回答 → 置信度校验"的迭代过程，一旦置信度达到阈值就提前退出，从而在不显著降低回答质量的前提下大幅减少推理 token 数。

模块代码位于 `deer/` 目录，侵入式修改了 `vllm/entrypoints/openai/` 下的两个文件。

---

## 2. 核心抽象

### 2.1 `DeerConfig` (`deer/context.py:16`)

纯数据类，封装 DEER 算法的全部超参。请求级参数（`enable_deer`, `deer_initial_thought_limit`, `deer_think_stride`, `deer_task_is_code`）由用户在请求 JSON 中指定，其余参数使用默认值且无请求级配置入口。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `is_code` | False | 是否为 code 任务，影响 trial answer 的 prompt 模板和置信度计算策略 |
| `think_ratio` | 0.9 | 最大思考比例（相对 max_model_len） |
| `token_budget` | 2500 | 总思考长度预算 |
| `trial_answers` | 10 | 最多重复"思考→校验"循环的次数 |
| `think_stride` | 128 | 每次迭代思考的 token 数 |
| `initial_thought_limit` | 512 | 首次自由思考的 token 数 |
| `trial_answer_length` | 50 | trial answer 最大长度 |
| `prob_th` | 0.95 | 置信度阈值，达到即提前退出 |
| `rep_exit` | 1 | 是否开启重复提前退出 |

### 2.2 `RequestStateContext` (`deer/context.py:30`)

单个请求的完整上下文，在状态机各状态之间传递。封装了：

- **外部依赖**：`ChatCompletionRequest`, `TokenizerLike`, `DeerConfig`, `OpenAIServingChat`（通过它访问 `engine_client`）
- **动态状态**：`request_id`, `engine_prompt`（可变 TypedDict，每次迭代追加 token）, `trial_count`, `result_queue`（asyncio.Queue，生产者-消费者模式）, `last_chunk`, `rep_count`, `last_stride_text`
- **关键方法**：
  - `engine_client` (property)：返回 `serving_chat.engine_client`，即 vLLM 的 AsyncLLM 引擎客户端
  - `stream_output(result)`：向 `result_queue` 放入推理结果（或 None 作为结束信号）
  - `update_prompts(token_ids, res)`：将本轮生成的 token_ids 追加到 `engine_prompt`，并记录 `last_chunk`

### 2.3 状态机 (`deer/state.py`)

四个状态类继承自 `_BaseState`（ABC），每个实现 `handle(context)` 静态方法，返回下一个状态的类或 None（流程结束）。

```
_InitialThinkState ──→ _IterativeThinkState ──→ _ConfidenceCheckState
       │                       │                        │
       │  (stop_reason ==      │  (stop_reason ==       │  (confidence >= prob_th)
       │   " response")        │   " response")         │
       ▼                       ▼                        ▼
   _AnswerState ←─────────────────────────────────────────
       │
       ▼ (return None)
   结束
```

#### `_InitialThinkState` — 首次长程思考
- 使用 `initial_thought_limit` 作为 max_tokens，设置 `stop=[" response"]`
- 调用 `engine_client.generate()` 提交推理
- 如果因 `" response"` 停止词结束 → 进入 `_AnswerState`（直接回答）
- 否则 → 进入 `_IterativeThinkState`

#### `_IterativeThinkState` — 迭代短程思考
- 检查 `trial_count` 是否达到上限，达到则直接回答
- 使用 `think_stride` 作为 max_tokens，同样设置 `stop=[" response"]`
- 如果因 `" response"` 停止词结束 → 进入 `_AnswerState`
- 否则 → 进入 `_ConfidenceCheckState`

#### `_ConfidenceCheckState` — 预回答与置信度校验
- 构造 trial answer prompt（code 和非 code 两种模板），设置 `logprobs=1`
- 调用 `engine_client.generate()` 生成 trial answer
- 计算生成 token 的平均最大概率（几何平均），与 `prob_th` 比较
- 达到阈值 → `_AnswerState`；否则回到 `_IterativeThinkState`

#### `_AnswerState` — 最终答案生成
- 根据上一轮停止原因构造不同的 prompt 后缀
- 使用剩余 max_model_len 调用 `engine_client.generate()` 生成最终答案
- 返回 None 结束状态机

### 2.4 `AsyncReasoningOrchestrator` (`deer/state.py:246`)

流程编排器。`run()` 方法执行状态机主循环，从 `_InitialThinkState` 开始，依次调用各状态的 `handle()` 并根据返回值跳转。异常时记录日志，`finally` 块确保发送 None 结束信号。

### 2.5 `CoTController` (`deer/cot_controller.py:22`)

对外暴露的唯一入口类。`handle_request()` 方法：

1. 从请求参数构建 `DeerConfig`
2. 创建 `RequestStateContext`
3. 创建 `AsyncReasoningOrchestrator` 并启动
4. 通过 `_build_managed_generator()` 包装为生产者-消费者模式
5. 调用 `serving_chat.chat_completion_stream_generator()` 或 `chat_completion_full_generator()` 将结果格式化为 OpenAI 兼容响应

`_build_managed_generator()` 的关键设计：
- 在 async generator 中 `create_task(orchestrator.run())` 作为后台生产者
- 消费者从 `result_queue` 中读取结果并 yield
- `finally` 块中取消生产者任务，确保客户端断开时释放 vLLM 推理资源

---

## 3. 绑架 vLLM 的过程（v0.20.2）

### 3.1 请求级参数注入 (`vllm/entrypoints/openai/chat_completion/protocol.py:360`)

在 `ChatCompletionRequest` 模型中增加四个可选字段：

```python
enable_deer: bool | None = Field(default=False, ...)
deer_initial_thought_limit: int | None = Field(default=None, ...)
deer_think_stride: int | None = Field(default=None, ...)
deer_task_is_code: bool | None = Field(default=None, ...)
```

这些字段通过 Pydantic 模型随 HTTP 请求一起解析，无需修改路由层。

### 3.2 入口劫持 (`vllm/entrypoints/openai/chat_completion/serving.py:273-278`)

在 `OpenAIServingChat.create_chat_completion()` 中，完成 prompt 编码、request_id 分配、`lora_request` 和 `model_name` 计算后，**在调用原生 `engine_client.generate()` 之前**插入判断：

```python
if request.enable_deer:
    logger.info(f">>>>>Request {request_id} is using CoT Controller.")
    return await self.cot_controller.handle_request(
        self, conversation, engine_inputs,
        request, request_id, tokenizer, request_metadata,
        model_name)
```

当 `enable_deer=True` 时，请求被完全劫持到 `CoTController.handle_request()`，**不执行**后续的原生推理流程。

### 3.3 初始化 (`vllm/entrypoints/openai/chat_completion/serving.py:176`)

在 `OpenAIServingChat.__init__()` 中创建 `CoTController` 实例：

```python
self.cot_controller = CoTController()
```

### 3.4 多次"重新提交"的机制

DEER 模块通过状态机循环实现多次有条件的推理提交，核心机制是：

1. **每次状态转换都调用 `context.engine_client.generate()`** — 这实际上是 `serving_chat.engine_client`，即 vLLM 的 AsyncLLM 引擎客户端。每次调用都是一次独立的推理请求提交。
2. **`engine_prompt` 是可变 TypedDict** — 类型为 `TokensInput`（`vllm.inputs.engine`），每次迭代后通过 `update_prompts()` 将新生成的 token_ids 追加到 `engine_prompt['prompt_token_ids']` 中，实现"续写"效果。
3. **`result_queue` 作为输出通道** — 所有状态的推理结果都通过 `context.stream_output()` 放入队列，由 `_build_managed_generator()` 中的消费者统一 yield 给上层。
4. **`stop=[" response"]` 控制思考边界** — 通过设置停止词，让模型在思考结束时自然停顿，状态机据此判断是否进入回答阶段。

### 3.5 取消/断开处理

`_build_managed_generator()` 的 `finally` 块确保：
- 客户端断开连接时，`async for` 循环抛出 `CancelledError`
- 生产者任务（`orchestrator.run()`）被取消
- `await producer_task` 等待 vLLM 的 `abort()` 完成，释放引擎资源

---

## 4. 与 v0.20.2 的适配要点

### 4.1 文件路径变化

| v0.9.1 路径 | v0.20.2 路径 |
|---|---|
| `vllm/entrypoints/openai/serving_chat.py` | `vllm/entrypoints/openai/chat_completion/serving.py` |
| `vllm/entrypoints/openai/protocol.py` | `vllm/entrypoints/openai/chat_completion/protocol.py` |
| `vllm/entrypoints/openai/serving_engine.py` | `vllm/entrypoints/openai/engine/serving.py` |

### 4.2 类型变化

| v0.9.1 | v0.20.2 | 说明 |
|---|---|---|
| `TokensPrompt` (`vllm.inputs.data`) | `TokensInput` (`vllm.inputs.engine`) | 编码后的 prompt 类型，TypedDict 子类 |
| `AnyTokenizer` (`vllm.transformers_utils.tokenizer`) | `TokenizerLike` (`vllm.tokenizers`) | tokenizer 接口类型 |
| `engine_prompts: list[TokensPrompt]` | `engine_inputs: list[EngineInput]` | `create_chat_completion` 中 prompt 参数名和类型均变化 |

### 4.3 API 签名变化

| API | v0.9.1 | v0.20.2 |
|---|---|---|
| `to_sampling_params` | `(max_tokens, logits_processor_pattern, default_sampling_params)` | `(max_tokens, default_sampling_params)` — 中间参数已移除 |
| `model_name` 获取 | `self._get_model_name(request.model, lora_request)` | `self.models.model_name(lora_request)` |
| `max_model_len` 访问 | `self.max_model_len`（在 `OpenAIServing.__init__` 中缓存） | `self.model_config.max_model_len`（不再缓存） |
| `engine_client.generate` | 3 个位置参数 | 3 个位置参数 + 多个关键字参数（`priority`, `reasoning_ended`, `reasoning_parser_kwargs` 等） |

### 4.4 保持不变的接口

- `CompletionOutput.stop_reason` — 字段名未变
- `CompletionOutput.logprobs` — 类型 `SampleLogprobs` 未变
- `engine_client.generate(prompt, sampling_params, request_id)` — 前三个位置参数语义不变
- `chat_completion_stream_generator` / `chat_completion_full_generator` — 前 7 个位置参数签名不变
- `ChatCompletionRequest.priority` — 字段存在且语义不变
