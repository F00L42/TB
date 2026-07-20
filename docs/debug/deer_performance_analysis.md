## 分析结论

### 核心问题：DEER 的多次 `generate()` 调用在 vLLM 调度器中表现为多个独立请求，导致并发场景下的调度冲突

### 详细分析

#### 1. 每个 `engine_client.generate()` 调用创建一个独立的调度器请求

在 `deer/state.py` 中，状态机的每个状态都使用**不同的 request_id** 调用 `engine_client.generate()`：

| 状态 | request_id | 行号 |
|------|-----------|------|
| `_InitialThinkState` | `{request_id}_initial_think` | 118 |
| `_IterativeThinkState` | `{request_id}_rethink_{trial_count}` | 148 |
| `_ConfidenceCheckState` | `{request_id}_trial_ans` | 203 |
| `_AnswerState` | `{request_id}_answer` | 242 |

每个调用都经过 `AsyncLLM.add_request()` → `_add_request()` → `engine_core.add_request_async()` 完整链路，在调度器中被视为**全新的独立请求**。

#### 2. 调度器按 `request_id` 管理请求，每个请求独立分配 KV Cache

在 `scheduler.py:1741-1759` 的 `add_request()` 中：
```python
def add_request(self, request: Request) -> None:
    existing = self.requests.get(request.request_id)
    if existing is not None:
        # ... streaming update path ...
    else:
        self._enqueue_waiting_request(request)
        self.requests[request.request_id] = request
```

由于 DEER 使用不同的 request_id，每个 `generate()` 调用都进入 `else` 分支，作为一个**全新的等待请求**入队。调度器不知道这些请求属于同一个用户请求。

#### 3. 关键瓶颈：`max_num_running_reqs`（= `max_num_seqs` = 8）

在 `scheduler.py:106`：
```python
self.max_num_running_reqs = self.scheduler_config.max_num_seqs  # = 8
```

在 `schedule()` 方法中（line 571-573）：
```python
while (self.waiting or self.skipped_waiting) and token_budget > 0:
    if len(self.running) == self.max_num_running_reqs:
        break
```

**当 4 个用户并发请求同时运行时，每个请求在 DEER 状态机中可能产生多个 in-flight 的调度器请求**，这些请求会竞争 `max_num_running_reqs=8` 的槽位。

#### 4. 性能归零的根因链

从日志 `deer_throughoutput.txt` 可以清晰看到问题演变：

**阶段 1：单请求正常（10:03:42 - 10:05:47）**
- 单个请求，Running=1，generation throughput 正常（~50 tokens/s）
- 状态机依次执行 `_InitialThinkState` → 多次 `_IterativeThinkState` → `_AnswerState`
- 每次 `generate()` 调用完成后，上一个请求在调度器中已释放，下一个请求可以立即调度

**阶段 2：4 并发请求同时到达（10:06:58）**
- 4 个请求同时进入 `_InitialThinkState`，每个调用 `generate()` 创建 4 个调度器请求
- Running=4，generation throughput 12.0 tokens/s（初始 prefill 阶段）
- 10:07:13 开始，4 个请求都在 decode 阶段，generation throughput ~50 tokens/s

**阶段 3：第一个请求进入 `_IterativeThinkState`（10:08:43）**
- 此时 4 个请求中，部分完成了 `_InitialThinkState`，开始进入 `_IterativeThinkState`
- 但注意：`_InitialThinkState` 的请求在调度器中**已经完成并释放**，而 `_IterativeThinkState` 的请求是**新创建的**
- 调度器需要为新请求重新做 prefill（因为 `update_prompts` 只是手动追加 token，调度器看到的是全新的 prompt）

**阶段 4：性能崩溃（10:08:53 之后）**
- 关键日志行：`Accepted throughput: 0.00 tokens/s, Drafted throughput: 30.40 tokens/s`
- **Speculative decoding 的 draft 全部被拒绝**（per-position acceptance rate 全部为 0.000）
- 这意味着模型产出的 draft token 与目标分布完全不匹配
- 从 10:09:03 到 10:13:03，持续 4 分钟，generation throughput 只有 5.6-5.8 tokens/s

#### 5. 根本原因：DEER 的多次提交破坏了 speculative decoding 的连续性

**Speculative decoding 的工作原理**：draft model（这里是 Qwen3 的 MTP head）基于当前 KV cache 状态预测后续 token，target model 验证这些 token。当 draft 被接受时，一次 forward pass 可以产出多个 token。

**DEER 的破坏机制**：
1. 每次 `generate()` 调用创建一个**全新的调度器请求**，KV cache 从零开始分配
2. 虽然 `engine_prompt` 包含了之前生成的 token，但调度器看到的是**全新的 prefill 请求**
3. 对于 speculative decoding，这意味着：
   - 上一个请求的 KV cache 已被释放（`_free_request` → `_free_blocks` → `del self.requests[request.request_id]`）
   - 新请求需要重新计算 KV cache（prefill）
   - **MTP head 的 draft token 预测基于不完整的 KV cache 状态**

**更严重的问题**：当多个请求并发时，调度器在 running 队列中混合了来自不同用户请求的 `_InitialThinkState`、`_IterativeThinkState`、`_ConfidenceCheckState` 的请求。这些请求的 prompt 长度不同、状态不同，导致：
- KV cache 碎片化
- Prefix cache 命中率下降（从 70%+ 降到 68%）
- Speculative decoding 的 draft 质量急剧下降（acceptance rate 从 0.5-0.8 降到 0.0）

#### 6. 与\"用户外部逐次调用\"的差异

| 方面 | 用户外部调用 | DEER CoTController |
|------|------------|-------------------|
| 请求生命周期 | 每个 HTTP 请求一个 `generate()` 调用，完成后释放 | 一个 HTTP 请求产生多个 `generate()` 调用 |
| request_id | 唯一，不重复 | 每次调用使用不同的 request_id |
| KV cache | 连续，请求完成后释放 | 每次新调用重新分配 KV cache，旧 cache 被释放 |
| 调度器视角 | 独立的完整请求 | 多个独立的短请求，但实际属于同一逻辑请求 |
| SpecDec 连续性 | 连续 decode，draft 质量稳定 | 每次新请求重新 prefill，draft 质量下降 |
| 并发竞争 | 请求间公平竞争 | 同一请求的多个子请求与其它请求竞争，加剧冲突 |

#### 7. 结论

**不是异步透传实现的问题**。`_build_managed_generator` 的生产者-消费者模式本身是正确的。

**根本问题是 DEER 状态机每次调用 `engine_client.generate()` 都创建一个全新的调度器请求**，这导致：
1. **KV cache 无法复用**：每次状态转换都释放旧 cache、重新 prefill
2. **Speculative decoding 被打断**：draft model 无法利用之前的 decode 状态
3. **并发时调度器槽位竞争加剧**：4 个用户请求 × 每个请求 N 个状态 = 最多 4N 个调度器请求竞争 8 个 running 槽位

**最直接的证据**：日志中 `Accepted throughput: 0.00 tokens/s` 且 `Per-position acceptance rate: 0.000` — speculative decoding 的 draft 全部被拒绝，说明模型状态完全不连续。

### 建议修复方向

1. **使用 vLLM 的 streaming input 机制**：vLLM 支持 `AsyncGenerator[StreamingInput, None]` 作为 prompt 输入（`async_llm.py:316-331` 的 `_add_streaming_input_request`），可以让 DEER 状态机通过流式输入追加 token，而不是创建新请求
2. **或者使用 `request.resumable` 机制**：调度器支持 `resumable` 请求（`_handle_stopped_request` 中 `WAITING_FOR_STREAMING_REQ` 状态），可以让请求暂停后继续，而不是重新创建
3. **或者禁用 speculative decoding**：在 DEER 模式下关闭 MTP，避免 draft 质量下降导致的性能损失