# Public API 和生命周期

导出入口：`thinking_observer`。稳定范围是 0.1 的 frozen dataclasses、Detector/DetectorState Protocol 和 CONTINUE/StopAndAnswer；engine 适配仅支持固定 vLLM 版本。

可运行的文本/Token 获取与窗口示例见 [检测器开发](detector_development.md)。

## 调用次序和线程

服务启动一次构造 Detector 工厂、复用 tokenizer。每个支持的请求调用 `start(context)`；同一请求内 `observe` 有序、无并发重入；返回有效 state 后结束时 `close(outcome)` 一次。不同请求各有 state，工厂不应把可变算法状态放在服务共享字段中。

所有回调同步执行在请求事件循环，必须是增量、有界 CPU 计算；不做文件/网络 I/O，不调用模型，不自行调 engine 或 parser。不接受 awaitable。`callback_limit_ms` 是 start/observe 返回后的软成本检查：超限会忽略决策并禁用本次检测，不能抢占卡死的 Python 回调。close 同样要求快速返回，普通 close 错误不会改写已经生成的答案。强隔离/异步 worker、回压与过期决策协议留待后续扩展。

普通 start/observe 错误、无效返回/位置/cue：记录 `detector_error`，继续本次原生 child。`CancelledError`、框架错误、引擎错误继续传播。不要用检测器异常表示停止意图。

## ReasoningDelta

| 字段 | 语义 |
| --- | --- |
| `token_ids` | 本次新增 reasoning ID tuple；不包含自然结束 token 或同 batch 的正文/工具 ID |
| `text` | 本次原生 detokenizer 片段；可能是空串或只有文本没有新 ID；不是 token→字符一一映射 |
| `start`, `end` | 思考 token 计数的 `[start,end)`；字符数不能用于这里 |
| `emitted_tokens` | 框架已接受的 parser-facing completion token 数，包括当前 batch；不是网络接收确认 |
| `boundary` | `None` / `think_end` / `tool_call`；混合 batch 文本无法安全分割时 text=None |
| `terminal` | 当前 batch 有原生 stop/length；该事件仅观察，返回的停止意图不会覆盖原生终态 |

自然边界/终态事件是最后一次观察；此后的正文/工具调用和 abort drain 不再触发算法。一次 delta 中边界之前的 reasoning ID 可以观察，但自然边界优先。空 delta 可合法出现，detector 不应据此增计 token。

## StopAndAnswer 与编辑

`reason` 非空、≤256 字符；建议稳定的原因代码，不放用户正文。
`detected_at` 为前缀长度，`0 ≤ detected_at ≤ delta.end`；`repeat_start` 可选且 `0 ≤ repeat_start ≤ detected_at`，只作诊断。

`answer_cue=None` 使用服务默认 cue；空串表示只闭合 reasoning。cue ≤4096 字符、编码后 ≤`max_cue_tokens`（默认 256），不得包含活动 tokenizer 的特殊 ID 或边界 ID。拒绝 cue 时原生继续；不会自动替用户改写 cue。

内部 final prompt 为 `original_prompt_ids + accepted_generated_ids + encode(cue) + [think_end_id]`。若 drain 已出现 `</think>` 或 `<tool_call>`，只用原始 prompt 和完整已接受前缀续写；忽略 cue，不重复闭合。首版没有额外 answer separator，避免在正文/tool 边界引入隐藏文本。cue 的优化效果未经服务器验证。

不可撤回已输出 token，不能承诺在 repeat_start 截断。若需要修改尚未输出的历史，必须另设计有界缓冲及 text/token 对齐；本接口不会悄悄修改已有前缀。

## 容量与终态

abort 前预留 closure + 至少一个 final token，以及 cue 的上下文容量。容量不足则原生继续。abort 后按实际 drain 前缀再次检查；不足则报错，不能以空答案冒充成功。

`answer_limit = min(max_model_len - final_prompt_length, completion_limit - emitted_tokens - pending_closure)`。
closure 占一个公开 completion token；cue 是隐藏 prompt token。响应 prompt/usage 元数据始终是原始请求，不把 final child 扩大的 prompt 当成用户 prompt。logprobs 不支持介入，因为不能给合成 closure 伪造概率。

自然 stop/length 在同 batch 或 drain 中胜出，保持原生终态。只有框架拥有的 abort 可被消去并接管。多个终态、终态后输出、无终态、非预期 abort、错误终态、completion 越界都使请求失败。

## Outcome / telemetry

`status`：`native_complete`、`native_won_race`、`handed_off`、`cancelled`、`error`。
`detected_at`、`repeat_start`、`emitted_at_detection` 是意图位置；`actual_handoff` 是实际 reasoning 前缀长度，`overshoot_tokens=actual_handoff-detected_at`。没有接管时后两者为空。

`callback_ms` 是 start/observe 墙钟累计；`abort_drain_ms` 从 abort 调用到 drain 完成；`handoff_ms` 包括接受尾部、前缀组装直到 final 提交，可受消费者背压影响。它们不是 GPU kernel 时间。

`children` 分列 observation 和 final 的 request ID、prompt/generated token、finish_reason，summary 模式另给 wall_ms。公开 emitted_tokens 与 child generated_tokens 不同：前者含合成 closure，后者不含；cue_tokens 另列。没有 hidden probe。

环境变量 `THINKING_OBSERVER_TELEMETRY=summary` 写 `THINKING_OBSERVER_SUMMARY` schema 1 JSON；默认 off 不写成功事件，不采每 child wall。无论 off/summary，detector.close 仍收到 Outcome，start/observe 都有软成本检查。不记录正文、原始 token、cue 或 tokenizer；只有 detector 自己给出的 reason 字符串。能力拒绝始终写带原因的 FALLBACK warning。

## 能力拒绝

仅文本 token prompt，单 choice，Qwen3 reasoning + 无 tool parser 或 Qwen3Coder tool parser。原生 reasoning 已关闭、其他 parser、beam、n>1、多模态/未知 prompt 字段、continue_final_message、prompt truncation、KV transfer/PD、未固定 rank 的多 DP、字符串 stop、min_tokens、非默认 presence/frequency/repetition penalty、structured output/自定义 logits/extra_args/allowed IDs/bad words/logit bias/thinking budget、logprobs/prompt_logprobs、禁用 detokenization：在 detector.start/engine 提交前显式原生回退。

原生 token stop IDs、temperature/top_p/top_k/seed 和采样默认值透传；接管重新启动采样不承诺与不中断生成相同的随机轨迹。priority、LoRA、trace headers、显式 DP rank、reasoning_parser_kwargs 逐 child 透传；final 仅把 reasoning_ended 改为 True。
