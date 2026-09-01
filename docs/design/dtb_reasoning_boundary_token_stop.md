# DTB reasoning boundary 的 token-stop 迁移设计

## 1. 设计目的

本文判断 DTB 是否应在 Qwen3.6 上用 `stop_token_ids` 取代控制器追加的字符串
`stop`，并定义可扩展到不同 reasoning 协议的边界发现、输出适配、配置校验和验证方案。

设计起点为仓库提交 `3caf51886bb2faccc2f318e5ef3db43e721e3edc` 加当前工作区中
已经删除 final-answer `</think>` stop 的修改，目标运行时为 vLLM `v0.23.0`、
vLLM-Ascend `v0.23.0rc1` 和 Qwen3.6-27B。已知目标 tokenizer 满足：

| marker | token IDs | 协议含义 |
| --- | --- | --- |
| `</think>` | `[248069]` | 显式结束 reasoning |
| `<tool_call>` | `[248058]` | 隐式结束 reasoning，同时开始工具 XML |

目标不是改变模型可生成的正文，而是让 DTB 的内部 thinking child 在结构边界精确结束，
并让后续 continuation 和原生 reasoning/tool parser 看到与一次原生生成等价的逻辑 token 流。
表中的 marker 只描述 Qwen3.6 协议，不能提升为 DTB 的跨模型常量；其他模型的 reasoning
结束 token、可见文本乃至“显式结束标记”是否存在都可能不同。

## 2. 结论

**不应整体替换。迁移仅属于 thinking child，并且按 parser 能力分阶段进行：Qwen3.6 的
explicit-end `248069` 可以先迁移；tool-start `248058` 必须在工具 parser 支持 token-only
起始边界后再迁移。**

具体决策如下：

1. `_ThinkingState` 中控制器追加的 explicit-end `</think>` 应从字符串 `stop` 改为
   `stop_token_ids=[248069]`。Scheduler 能在 speculative accepted batch 内逐 token 截断，
   直接消除字符串 stop 造成的 text 已截断、token IDs 仍带后缀问题。
2. `<tool_call>` 虽也是 singleton token `248058`，当前 patched Qwen 工具 parser 仍以
   `current_text.find("<tool_call>")` 建立工具区间。若 token stop 产生“空 text + 248058”，
   parser 会缺少工具起始位置。因此在 parser 完成 token-only tool-start 适配和真实验证前，
   暂时保留 `<tool_call>` 字符串 stop 与既有 suffix 对齐器。
3. `_FinalAnswerState` 不得添加这两个 token ID，也不得恢复字符串 `</think>` stop。
   字面量 `</think>` 在工具参数、源码和说明文字中仍编码为 token `248069`；token stop
   与 string stop 都会在这里截断合法答案。token stop 解决的是停止层次，不解决阶段误配。
4. `_ConfidenceProbeState` 是 reasoning 已结束后的隐藏试答，不应把 `</think>` 作为
   控制器 stop；当前 `params.stop = [THINK_END_TAG]` 不应机械替换成
   `stop_token_ids=[248069]`，而应删除该控制器 stop，并保留请求原生 stop/EOS/max-token
   语义。
5. 用户请求本身携带的字符串 `stop` 必须继续保留。vLLM 0.23 的
   `stop_token_ids` 只表达多个单 token ID 的 OR，不表达多-token 序列，因此不是
   OpenAI `stop` 的通用替代品。
6. 当前字符串-stop 对齐器应保留为兼容路径，但 token-stop 模式不应再依赖它裁掉
   speculative 后缀；token-stop 路径保持 source text/token IDs，不按 marker 文本做必要性判断。
7. DTB 不应内置“所有模型都用 `</think>`”这一前提。边界 ID、边界类型和控制器强制关闭
   reasoning 的 token 序列必须由当前活动 parser 的模型协议适配器提供。
8. continuation 主 prompt 应采用 **token-authoritative** 表示：`prompt_token_ids` 是唯一可变
   事实源，初始 `prompt_text` 只作为路由/诊断的不可变可选视图，不再随 thinking、cue 和
   boundary 反复拼接。该调整与 token-stop 兼容，但应作为独立优化阶段实施和计量。

因此，这是一个**受 parser 能力约束的分阶段正确性加固**，不是修复 final-answer 截断的替代方案，
也不应被宣称为独立的显著时延优化。当前对齐器已经缓解字符串 stop 的已知后缀问题；迁移的
主要收益是从源头消除该问题、减少控制器补偿分支和少量无效 speculative token。

## 3. 源码确定的行为

### 3.1 字符串 `stop`

vLLM 在 `vllm/v1/engine/detokenizer.py` 中先把本轮全部 `new_token_ids` 解码并追加到
detokenizer，再执行 `check_stop_strings()`。命中后只裁剪 `output_text`。如果同一个
speculative batch 是：

```text
reasoning, 248069, suffix_1, suffix_2
```

字符串 stop 可以让 text 结束于 `</think>`，但 detokenizer 的 token IDs 已经包含后两个
token。当前 `align_stop_boundary_delta()` 正是为这种不一致补裁 token IDs。

### 3.2 `stop_token_ids`

vLLM Scheduler 在 `_update_request_with_output()` 中逐个 append accepted token，并在每个 token
后调用 `check_stop()`：

```text
append token -> last_token_id in stop_token_ids -> FINISHED_STOPPED
             -> 删除当前 accepted batch 中剩余 token
```

因此对 `[248069]` 和 `[248058]`，EngineCore 返回的 `new_token_ids` 最后一个 token 就是边界，
不会包含同批 speculative 后缀。`stop_reason` 是命中的整数 token ID，而不是 marker 字符串。

这也比文本匹配更符合 Qwen 原生 parser 的定义：原生 reasoning parser 以
`end_token_id`/`tool_call_token_id` 判定状态转换。即使某组普通 token 偶然解码拼成相同字符，
它也不应被 DTB 提升为结构边界；反过来，结构 token 即使被 `skip_special_tokens` 从 text 隐去，
仍必须触发边界。

### 3.3 stop token 的 text/token 输出

`include_stop_str_in_output=False` 时，V1 detokenizer 不让最后一个 stop token 参与 text 解码，
但仍把该 token 保存在 `CompletionOutput.token_ids`。设置为 `True` 时该 token 参与解码，
实际 text 是否出现 marker 还受 tokenizer 的 `skip_special_tokens` 行为影响。

**目标服务器实测（2026-09-01）：**流式请求配置
`stop_token_ids=[248069]` 后，terminal 的 `stop_reason=248069`、OpenAI `delta` 为空，
上一个可见 delta 只有 `"\n"`，整个 SSE 没有出现 `</think>`。这与上述源码路径一致：
结构 token 可以保留在 engine `CompletionOutput.token_ids` 中，同时不形成可见 text；原生
reasoning parser 消费 token ID 后也不会把结束标记输出到 reasoning/content。

因此不能假设以下任一关系恒成立：

```text
stop_reason == 248069  => output.text 一定以 </think> 结束
stop_reason == 248058  => output.text 一定以 <tool_call> 结束
```

因此，**不得把“text 中找到 marker”设为边界成立条件，也不得为了表面对齐而补回 marker
文本。** parser-facing 的事实源是活动 reasoning parser 的协议、`stop_reason` 和 token IDs。
Qwen 的统一 reasoning/tool parser 必须能消费“空 text + 结构 token ID”的边界 delta；
`<tool_call>` 路径是否满足该条件必须单独以真实 parser 验证，不能由 `</think>` 结果外推。
当前仓库的 `qwen3_coder_parser.patch` 在流式重算路径中通过 marker text 搜索
`tool_start_positions`，尚不满足这一门槛。

### 3.4 `min_tokens`

`stop_token_ids` 会进入 `all_stop_token_ids`。当 `min_tokens > 0` 时，vLLM 在采样前 mask
这些 token；字符串 stop 则只是暂不做文本匹配。这是行为变化，不可静默忽略。

DTB 的 reasoning boundary 必须能在任意 thinking child 的第一个自然边界结束，因此内部
thinking child 应使用 `min_tokens=0`。外部请求的 `min_tokens` 不应重复应用到每个内部 child；
final-answer 是否需要满足外部最小 completion 语义属于单独的 completion-budget 设计，不应由
boundary stop 隐式承担。

### 3.5 ID 范围风险

vLLM 0.23 只检查 `stop_token_ids` 的元素是整数，没有检查其落在当前 tokenizer/model vocab
范围内。错误硬编码 ID 还可能在 `min_tokens` logits mask 路径进入设备端索引。因此控制器必须
在生成请求进入 engine 前自行校验，不能把来自另一个 Qwen 版本的数字直接复用。

### 3.6 当前 prompt 已是双表示，额外成本来自文本镜像

当前 `DtbRequestContext.engine_prompt` 并非只保存字符。它直接引用 serving 产生的
`engine_inputs[0]`，其中已有 `prompt_token_ids`，并可能同时有 `prompt`；context 又单独保存
`prompt_text`。现有 continuation 路径实际上已经以 token IDs 驱动模型：

- `append_thinking()` 直接扩展 `engine_prompt["prompt_token_ids"]`；
- `append_prompt_suffix()` 先编码 suffix，再扩展 token IDs；
- `child_prompt()` 用主 prompt token IDs 加已缓存的 suffix token IDs 构造隐藏 child。

但三个方法还会同步拼接 `prompt_text` 并覆盖 `engine_prompt["prompt"]`。此外
`request_controller.py` 在做 scene/budget 分类前，无论 `conversation` 中是否已经存在最新用户
文本，都会对完整 `prompt_token_ids` 执行一次
`tokenizer.decode(..., skip_special_tokens=True)`。正常 chat 请求随后由
`visible_user_text()` 选择用户消息，因此这次
整 prompt decode 通常没有参与分类结果。

vLLM `v0.23.0` 的 `TokensPrompt` 明确只要求 `prompt_token_ids`，`prompt` 是可选字段；engine
请求、prefill、KV cache 和 token 预算使用 token IDs。可选文本会进入本地
`RequestOutput.prompt`，普通 chat 生成不依赖它，但 `return_prompt_text=true` 会把它返回 API。
由此可以移除 continuation 热路径的文本镜像，却必须在响应边界保留外部 prompt 文本契约；也
不能据此关闭生成结果的 detokenize，OpenAI 响应以及 reasoning/tool parser 仍需消费 child
生成的增量 text 与 token IDs。

## 4. 设计逻辑

### 4.1 按生成阶段定义 stop ownership

| phase | 控制器 marker stop | 原请求 stop | 原因 |
| --- | --- | --- | --- |
| `initial_think` / `iterative_think`（第一阶段） | Qwen explicit-end token ID `248069`；tool-start 暂留字符串 stop | 保留 | explicit-end 已验证；tool parser 尚依赖 marker text |
| `initial_think` / `iterative_think`（第二阶段） | 活动协议声明且 parser 已验证的 singleton boundary token IDs；Qwen3.6 为 `248069`、`248058` | 保留 | token-only tool-start 验收后收敛到全 token boundary |
| `confidence_probe` | 无 | 保留 | 已进入试答，marker 字面量不是合法停止协议 |
| `final_answer` | 无 | 保留 | 必须允许正文和工具参数包含 marker 字面量 |
| controller 合成 boundary | 不发 engine 请求 | 不适用 | 发出 parser-native 的边界 token delta；Qwen3.6 的 text 为空、token IDs 为 `[248069]` |

“marker 是否是 stop”由生成阶段决定，不由 marker 是否为 special token 决定。即使
`</think>` 永远编码为 `248069`，它在 final answer 的代码字符串中仍是 payload，不是状态转换。

### 4.2 ReasoningBoundaryProtocol

新增请求级、只读的 reasoning 协议能力对象。marker text 是可选诊断信息，不是跨模型事实源：

```python
@dataclass(frozen=True)
class BoundaryTokenSpec:
    boundary: ReasoningBoundary
    token_id: int
    marker: str | None = None

@dataclass(frozen=True)
class ReasoningBoundaryProtocol:
    natural_boundaries: tuple[BoundaryTokenSpec, ...]
    forced_close_token_ids: tuple[int, ...]
    forced_close_text: str

    def classify_stop(
        self,
        stop_reason: object,
        delta_token_ids: Sequence[int],
    ) -> ReasoningBoundary | None: ...
```

协议实例应由当前活动 reasoning/unified parser 的适配器创建，而不是先假设 `</think>` 再查询
tokenizer。Qwen3.6 适配器可以从 reasoning parser 的 `end_token_id` 和 tool parser 的
`tool_call_start_token_id` 取得两个自然边界；其他模型可以声明不同 ID，或明确声明不支持
token-stop DTB。当前 serving patch 已创建 `reasoning_parser` 和 `response_parser`，但
`DtbRequestContext` 只保存前者；实现时需要把 unified `response_parser` 也传入 context，避免
读取 Qwen reasoning parser 的私有 `_tool_call_token_id`。

构建时对每个 boundary 执行：

1. token ID 必须来自活动 parser 所绑定的当前 tokenizer，而不是 served-model alias；
2. `token_id >= 0`，且必须属于当前 tokenizer vocab；
3. marker 非空时，`tokenizer.encode(marker, add_special_tokens=False)` 必须严格等于
   `[token_id]`，但 marker 为空不影响 token 边界成立；
4. parser 的 `is_reasoning_end_streaming()` 必须对构造的 boundary delta 返回真；
5. tool-start 类型必须再经 unified parser 验证，确保空 text + token ID 后的 continuation
   能生成结构化 tool call。

任一校验失败时应 fail closed，不得静默使用硬编码 ID。若模型只有 multi-token reasoning
结束序列，vLLM 0.23 的 `stop_token_ids` 无法表达；应为该模型实现 parser-native sequence-stop
能力或禁用 DTB，不能把多个 ID 放入 `stop_token_ids`，也不能仅凭猜测的 marker 文本回退。

### 4.3 SamplingParams 修改

thinking child 的配置逻辑应：

```python
params.stop_token_ids = stable_union(
    params.stop_token_ids,
    protocol.token_stop_capable_boundary_ids,
)
params.stop = stable_union(
    params.stop,
    protocol.string_stop_fallback_markers,
)
params.min_tokens = 0
```

不得清空或重写用户原有的 `params.stop` 和 `params.stop_token_ids`。只有尚未通过 token-only parser
验收的 boundary 才允许进入显式 string fallback；Qwen 第一阶段仅保留 `<tool_call>`。
`stable_union` 保持原有顺序并去重，便于日志和测试稳定。

控制器不再为了 marker 改写 `include_stop_str_in_output`，而是保留原请求值；否则会同时改变用户
字符串 stop 的输出契约。目标 Qwen3.6 实验使用默认 `False` 时，实测 terminal text 为空、
boundary token 由内部 token IDs/stop reason 表达。无论该字段取值为何，正确性都不依赖 text
中出现 marker。

### 4.4 parser-facing token 边界透传

token-stop terminal 的处理输入是原始 `text`、`token_ids` 和整数 `stop_reason`：

```text
1. 用协议适配器根据 stop_reason/token IDs 映射 ReasoningBoundary；
2. 断言 terminal token_ids 最后一个正是 boundary.token_id；
3. 断言边界后没有 token；
4. 原样透传 source text 和 token IDs；Qwen3.6 的 `</think>` terminal 允许 text 为空；
5. 把 boundary token 原样追加到 continuation prompt；
6. 对外清除 thinking child 的 terminal，最终只透传 final child terminal。
```

不得把 marker text 合成到 source delta。对 Qwen3.6，`text=""`、
`token_ids=[248069]` 才是已实测的 parser-facing 原生形状；补出 `</think>` 虽可能被 parser
再次剥离，但已经破坏严格的原生等价性。stream audit 应保存 source stop ID、token IDs、空 text
和协议分类结果。

旧 `align_stop_boundary_delta()` 仍处理用户/兼容路径触发的字符串 stop。token stop 路径若发现
boundary 后还有 token，必须报错而不是再次静默裁剪，因为这违反 vLLM Scheduler 的已知保证，
可能意味着版本或 patch 不一致。

### 4.5 控制器强制关闭 reasoning

模型自然生成 boundary 时可以原样透传；预算耗尽或置信度接受时，控制器必须主动构造一个
parser-native boundary。当前 `GUIDED_FINAL_PROMPT_V1`、`close_reasoning()` 和
`emit_reasoning_boundary_once()` 都硬编码 `</think>`，因此它们同样需要协议化：

1. continuation prompt 追加 `protocol.forced_close_token_ids`；
2. parser-facing synthetic delta 使用相同 token IDs；
3. synthetic delta 的 text 使用 `protocol.forced_close_text`，Qwen3.6 应为空字符串；
4. 引导语与 boundary token 分开建模，避免用一个含 `</think>` 的字符串同时承担自然语言 cue
   和协议关闭；
5. 没有安全 forced-close 能力的 parser/model 不启用强制 DTB 截断。

这解决了跨模型的根本问题：控制器关闭的是 parser 定义的 reasoning 状态，而不是搜索或拼接
某个固定文本。

### 4.6 配置策略

不建议把以下内容作为普通 YAML 必填项：

```yaml
model: qwen3.6-27b
think_end_token_id: 248069
tool_call_start_token_id: 248058
```

原因是 served-model alias、模型目录、tokenizer revision 和 LoRA 不能由这个字符串可靠唯一标识；
错误 ID 在 vLLM 0.23 上也不会被前端完整拦截。

推荐配置分层：

- **运行时事实源**：当前活动 parser + 实际 tokenizer 提供的协议能力；
- **代码协议**：boundary 的语义角色、singleton token 约束和 forced-close 能力，不固定 marker 文本；
- **可选部署断言**：manifest 或启动配置可保存期望的 tokenizer revision 与两个 expected ID，
  只用于 fail-closed 比对和审计，不作为实际 ID 来源；
- **telemetry**：记录模型名、tokenizer/parser 标识、解析出的 boundary role→ID、可选 marker
  和 stop mode。

如果确实需要支持多个协议不同的模型，应新增 `MODEL_PROTOCOLS`，键使用可验证的模型/tokenizer
revision，而不是仅使用 API 的 served-model alias；实际 token ID仍从 tokenizer 解析并与配置
断言比较。

### 4.7 continuation prompt 的 token-authoritative 设计

#### 4.7.1 状态结构

`DtbRequestContext` 应把“模型输入账本”和“可选文本视图”分开。可以新增一个轻量
`ContinuationPrompt`，也可以先在现有字段上保持同一不变量：

```python
@dataclass
class ContinuationPrompt:
    engine_fields: dict[str, Any]       # multimodal/cache 等非文本字段
    token_ids: list[int]                # 唯一可变事实源
    initial_text: str | None = None     # 不可变；仅供路由、诊断或兼容输出

    def append_tokens(self, token_ids: Sequence[int]) -> None: ...
    def fork(self, suffix_ids: Sequence[int]) -> dict[str, Any]: ...
    def engine_input(self) -> dict[str, Any]: ...
```

必须保持以下不变量：

1. thinking child 返回的 token IDs、自然 boundary token、forced-close token 和 cue 编码结果只
   追加到 `token_ids`；不得再通过生成文本重编码来恢复主 prompt；
2. cue 等静态字符串仍可在请求级缓存中编码一次；改为 token prompt 不等于模型不再接收 cue；
3. 每个 child 传入 vLLM 的字典保留原 `engine_inputs` 中仍适用的 multimodal、cache 等字段，
   但删除已经随 continuation 失效的 `prompt` 字段，避免 text 与 token IDs 不一致；
4. hidden probe 使用 fork 后的新 token list，不得与主账本共享可变 list；
5. `initial_text` 不随 thinking 增长，不作为模型 prompt 的权威表示；需要完整 continuation 文本
   的隔离审计才允许惰性 decode，并必须单独计入 controller CPU；
6. prompt token 数、外部剩余额度和 phase telemetry 均直接读取 token ledger 长度。

`prompt_text=None` 不会阻止 vLLM 处理 `TokensPrompt`。在当前 `AsyncLLM.add_request()` 路径中，
vLLM 会从实际 prompt 字典重新提取可选文本；纯 token prompt 的 `RequestOutput.prompt` 将为
`None`。普通 DTB chat 输出不依赖这个字段，但 `return_prompt_text=true` 依赖它。响应适配层应在
该选项启用时按原生外部请求语义提供 `initial_text`，而不是返回含隐藏 probe、thinking、cue 或
synthetic boundary 的内部 child prompt；还应检查服务日志或自定义插件是否读取
`RequestOutput.prompt`。不能只保留一个与 token IDs 已不一致的初始字符串并继续随 child
传给 engine。

#### 4.7.2 分类文本与 fallback

scene/budget 分类的优先级应改为：

```text
最新可见 user message
> serving 已有的 prompt_components.text
> 仅在两者都缺失时惰性 decode prompt_token_ids
```

这能直接删除正常 chat 请求当前必做的一次完整 prompt decode。需要保留 fallback 是因为未来
可能存在 conversation 缺失、非标准输入或兼容入口；fallback 触发次数和 decode wall 应进入
phase telemetry，不能静默退回每请求 decode。

#### 4.7.3 能省与不能省的开销

该设计能够减少：

- scene/budget 决策前一次通常无用的完整 prompt decode；
- 每次 thinking/cue/boundary 追加时 Python 长字符串的复制和双份 prompt 常驻内存；
- text/token 双写不一致导致的兼容分支和检查成本。

该设计不能减少：

- serving 首次把 chat template 转成 token IDs 的必要 tokenization；进入 DTB 时这一步已经完成；
- 模型的 prefill/decode 计算量，prompt token 序列没有因此变短；
- child 输出为了 OpenAI 响应及原生 reasoning/tool parser 所做的增量 detokenize；
- token-stop 本身减少的 speculative boundary overshoot；那是另一个收益来源。

因此预期收益首先体现在 `decision.decode_prompt`、`controller_cpu` 和内存分配，端到端收益大概率
小于模型 phase wall。必须实测，不应把“prompt 改存 token IDs”描述为减少模型 decode token。

#### 4.7.4 分阶段实施

为保证归因，建议顺序为：

1. 先把分类 decode 改为上述惰性 fallback，不改变 context 的双表示；
2. 再将 continuation 账本改成 token-authoritative，停止维护可变 prompt text；
3. 最后与 4.1—4.5 的 boundary token-stop 合并验证；性能报告仍分别列出
   `lazy_prompt_text` 与 `boundary_token_stop` 的增量收益。

## 5. 对现有实现的影响

| 位置 | 修改 | 影响 |
| --- | --- | --- |
| `dtb/reasoning/parser_boundary.py` | 增加 parser-backed protocol；thinking stop 改为追加 IDs；token boundary 原样透传 | 核心修改 |
| `dtb/reasoning/request_context.py` | 缓存 protocol；提供 ID→boundary 查询；关闭 reasoning 时追加协议 token；prompt token ledger 成为唯一可变事实源，文本只保留不可变/惰性视图 | 去除固定 marker 假设；减少字符串复制并消除双写漂移 |
| `dtb/request_controller.py` | 把 serving 已构造的 unified `response_parser` 保存在 context，并据此建立 protocol；分类优先使用 conversation/已有文本，只在缺失时 decode | 不依赖 parser 私有字段；去除正常 chat 的整 prompt decode |
| `dtb/integration/vllm/response_adapter.py` | `return_prompt_text=true` 时按外部请求语义恢复初始 prompt text | 避免泄漏内部 continuation 或返回空 prompt |
| `dtb/reasoning/generation_states.py` | thinking 使用 token stop；probe 删除 controller marker stop | final answer 保持无 controller stop |
| `GUIDED_FINAL_PROMPT_V1` / `emit_reasoning_boundary_once()` | cue 与 protocol close 分离；Qwen synthetic boundary 为 empty text + ID | 支持非 `</think>` 模型 |
| `detect_reasoning_boundary()` | 委托 protocol 识别 marker token ID、整数 stop reason | text marker 不再是必要证据 |
| `align_stop_boundary_delta()` | 保留 string-stop legacy；token-stop 只做 invariant check | token 模式不再 drop suffix/补 text |
| `qwen3_coder_parser.patch` | tool parser 从 token IDs 识别 248058，并维护 parser 内部虚拟 structural prefix；不得改写外部 delta text | 第二阶段迁移前置条件 |
| telemetry/analyzer | 同时保存 raw stop ID、逻辑 boundary 和 stop mode | 旧脚本不能再只比较 `"</think>"` |
| YAML/manifest | 可选 expected-ID 断言；不把硬编码 ID作为运行时事实源 | 防止跨 tokenizer 误配 |
| 单元/回放测试 | 增加 token stop、special-token text 缺失、literal marker payload | 必须覆盖流式和非流式 |

`CompletionOutput.token_ids` 会保留命中的 stop token；这正是 continuation prompt 和原生 reasoning
parser 所需的逻辑序列。completion token 账本继续把该 token 计入外部 completion budget。迁移后
`raw_generated_tokens` 不应再包含 stop 后 speculative token，历史指标中的
`alignment_dropped_token_count` 应在 token 模式恒为 0。

## 6. 一致性校验

### 6.1 必须保持的外部契约

1. 协议的 explicit-end token 只负责 reasoning→content 转换，不出现在最终 `message.content`；
2. tool-start boundary 必须恰好一次进入统一 parser 的逻辑 token 输入；不要求 SSE 中出现 marker text；
3. continuation prompt 与 parser-facing 流拥有相同的边界 token 序列；
4. final answer 的合法正文或工具参数可以包含任意多个 `</think>`/`<tool_call>` 字面量；
5. final child 的 terminal、finish reason 和 stop reason仍只透传一次；
6. 用户原生 stop/EOS/max-token 行为不因 controller marker 迁移被删除。

### 6.2 不能接受的实现

- 把 `[248069, 248058]` 同时添加到所有 child；
- 用 token stop 修复 final-answer literal collision；
- 仅修改 SamplingParams，仍要求从 text 搜索 `</think>`；
- 因 terminal text 为空而向 source delta 补写 `</think>`/`<tool_call>`；
- 在当前 text-dependent Qwen tool parser 上直接启用 `stop_token_ids=[248058]`；
- 设置 `skip_special_tokens=False` 后把 EOS、chat-role 等其他 special token 一并泄漏给 parser；
- 用 served-model 名称直接查硬编码 ID，缺少 tokenizer 一致性校验；
- encode 得到多个 ID 时把它们全部放进 `stop_token_ids`；
- token-stop 路径继续静默裁剪 Scheduler 返回的边界后 token。

## 7. 验证方案

### 7.1 无模型测试

至少新增以下测试：

1. Qwen protocol 从活动 parser 解析得到 explicit-end `248069`、tool-start `248058`；ID 不匹配、
   parser 不承认边界、multi-token stop 均 fail closed；
2. 第一阶段 thinking params 保留用户 stop，追加 `248069` token stop、保留 `<tool_call>` string
   fallback，`min_tokens=0`；第二阶段在 parser 验收后追加两个边界 ID；
3. probe/final params 不含控制器边界 ID，并允许 payload 中多个 marker；
4. `stop_reason=248069`、text 为空、token IDs 含 boundary 时保持 source text/token 不变，
   原生 parser 正确切换到 content；
5. 第二阶段门禁：`stop_reason=248058`、text 为空的直接 tool-call boundary 能让 patched Qwen parser 解析随后续写的
   `<function=...>`；
6. token stop terminal 后若仍有 token，状态机 fail closed；
7. string-stop legacy fixture 继续通过现有 speculative suffix 对齐测试；
8. token-authoritative prompt 与旧实现对所有 final-answer reason 产生完全相同的 child
   `prompt_token_ids`，hidden fork 不修改主账本；
9. conversation 有用户文本或 serving 已提供文本时 mock tokenizer 的 `decode()` 调用数为 0；
   两者缺失时只 fallback 一次；
10. 纯 token prompt 的流式 DELTA、非流式 FINAL_ONLY、原生 parser、multimodal 字段保留和一次
    speculative batch 跨边界都覆盖；
11. `return_prompt_text=false` 不产生完整 continuation 文本，`true` 时返回值与同一外部 Vanilla
    请求的 prompt text 语义一致，且不包含隐藏 probe、内部 cue 或 synthetic boundary。

### 7.2 目标服务器定向实验

在 MTP 保持生产默认的服务器上，对同一请求体执行 `string_boundary_stop` 与
`token_boundary_stop` 各至少 30 次，顺序交替、单并发。四组输入分别强制覆盖：

- 模型自然输出 `</think>`；
- 模型从 reasoning 直接输出 `<tool_call>`；
- speculative batch 在边界后还接受候选 token；
- final tool 参数和普通 content 中包含 1 次及多次字面量 `</think>`。

stream audit 至少记录：parser/protocol identity、configured stop strings/IDs、raw stop reason、
boundary、source text 和 token IDs、边界后 token 数、parser 输出、final terminal。Qwen explicit-end
样本必须包含实测形状 `stop_reason=248069`、terminal text 为空、前一 text delta 为 `"\n"`。
prompt 表示实验还应记录 `prompt_text_source`、`prompt_decode_fallback_count`、
`decision.decode_prompt` wall、controller CPU 和各 child prompt token 数；不得用 E2E 噪声替代
对被删除 decode 的直接验证。

通过条件：

```text
token mode 的 boundary 后 source token 数 = 0
token mode 的 alignment_dropped_token_count = 0
continuation prompt 中 boundary 数 = 1
parser-facing boundary token 数 = 1；marker text 数允许为 0
raw tool XML 完整且 structured arguments JSON 合法
literal-marker final payload 不提前终止
stream/non-stream parser 结果一致
hard failure = 0
```

### 7.3 paired replay 与 LightCode 门禁

1. 复用 run04 的真实 Vanilla 上下文和现有异常 paired replay case；
2. 增加 `dtbtool-l4-a17-vanilla-run04-p0` 一类工具参数含多个 `</think>` 的定向 case；
3. 对相同 request body 运行 Vanilla、当前 string-boundary DTB、token-boundary DTB；
4. 协议错误为 0 后再比较 E2E、generated token、边界后无效 token 和 phase wall；
5. 最终执行同配置 LightCode 9 场景，要求报告完整率和质量不低于 Vanilla。

性能报告必须把收益限定为实测结果。理论上 token stop 会减少边界同批的 speculative overshoot
和前端 abort 往返，但每请求通常只发生一次或少数几次，不能在服务器测量前推断其能显著降低
LightCode 端到端时延。

## 8. 变更历史

| 日期 | 变更内容 | 原因 |
| --- | --- | --- |
| 2026-09-01 | 首版：确定仅迁移 thinking boundary，定义 token-stop 输出规范化、配置校验和 paired replay 门禁 | Qwen3.6 两个 reasoning marker 均为单 special token，且 string stop 已出现 speculative text/token 不一致 |
| 2026-09-01 | 修订为 parser-backed 模型协议；禁止用 text marker 证明或补写边界；协议化 forced close | 服务器实测 `stop_reason=248069` 时 terminal delta 为空且 SSE 不含 `</think>`；不同模型结束协议不固定 |
| 2026-09-01 | 改为两阶段迁移：先 explicit-end 248069，tool-start 248058 等待 token-only parser 适配 | 当前 Qwen parser patch 通过 marker text 建立 tool span，不能消费空 text 的 tool-start boundary |
| 2026-09-01 | 增加 continuation prompt 的 token-authoritative 设计、惰性文本 fallback、成本边界和独立验证 | 当前实现已经使用 prompt token IDs，但仍无条件 decode 初始 prompt 并持续维护可变文本镜像 |
