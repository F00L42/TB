# run_server_03：两条定向样例回放分析

## 1. 结论

本轮把两个不同层次的现象分开了：

1. **实测**：`cases.jsonl` 第 5 行原先关注的 tool arguments/JSON 故障没有复现。Vanilla 和
   DTB 各 30 次，共 60 次均有且只有一个合法 terminal；所有 tool arguments 都是合法 JSON，
   没有 schema error、XML 泄漏、tool index 断裂或 terminal 后尾流。
2. **实测**：第 10 行原先关注的 Vanilla empty arguments 也没有复现，Vanilla 30/30 协议通过。
   但 DTB 出现 2/30 次真实编排失败：`a9`、`a26` 都在 `initial_think` 中断，原始 SSE 返回
   `InternalServerError: DTB orchestration failed; see server logs`，没有 assistant terminal、usage
   或 tool call。
3. **源码 + 日志高度一致，仍缺原始 traceback 最后一锤**：最可能的直接根因是 DTB reasoning
   stop 的 token 对齐逻辑假设 `</think>` 在生成流里一定采用其独立编码 token `248069`。成功的
   58 次 initial-think terminal 全部包含该 token；失败 `a26` 却已将相同文本生成为普通 token
   序列 `</`=`510`、`think`=`26003`。当下一批次补出 `>` 并触发 vLLM 字符串 stop 时，当前实现
   会因找不到独立编码 token 子序列而抛出 `RuntimeError`。`a9` 也恰好停在即将引用同一字面量的
   位置，符合相同故障形状。
4. **实测**：原始 wire 不是“静默断流”。两次失败都有 SSE error，随后 `[DONE]`；HTTP status
   保持 200 是 streaming headers 已发送后的正常错误承载方式。之所以 `summary.jsonl` 只显示
   `missing_terminal` 且 `error=""`，是回放器没有识别合法 JSON SSE 中的顶层 `error` 字段。
5. **裁决**：DTB 的两次失败是需要修复的可用性问题，但不是 tool parser patch 造成的 tool-call
   JSON 故障；失败发生在 final-answer/tool parser 之前。其余所谓 semantic deviation 大多只是
   单一 `expected_tool_names` oracle 把合理的下一步 `bash`、`edit`、`write` 计为错误，不能作为
   parser 或系统 hard failure。

因此当前门状态是：

```text
line 5 tool-call protocol                 PASS on 60 responses
line 10 Vanilla empty-arguments replay    NOT REPRODUCED on 30 responses
DTB reasoning-stop token alignment        FAIL (2/30 on line 10)
wire-level explicit error                 PASS, but replay auditor misses it
Vanilla parser no-harm                     STILL INCONCLUSIVE
LightCode latency experiment admission    NOT YET
```

本轮是 `stream_audit` 定向可靠性 run，`latency_ranking_valid=false`，不能拿自动生成的 paired
latency 数字作为 DTB 时延结论。

## 2. 输入与证据边界

| 项 | 值 |
| --- | --- |
| 输入 | `../input/cases.jsonl` 第 5、10 行 |
| 输入 SHA256 | `8667912b8a5b525c448cd8822281e7aa044df696cedbc2b16b42a46589e66e90` |
| 重复 | 每行、每变体 30 次 |
| 变体 | Vanilla / DTB，alternating |
| 总请求 | 120（60 pair） |
| 接口 | streaming chat completions |
| DTB 固定参数 | budget 20000；check 850；stride 500；num-thinking 5；min-think 1；prob 0.93 |
| 客户端证据 | `summary.jsonl`、`chunks.jsonl`、`paired_summary.json` |
| 服务端证据 | `server_filtered_part_01`、`server_filtered_part_02`，15,155 条可解析 audit |

本目录缺少完整未过滤 `server.log`、deployment identity、metrics 前后快照和 phase 账本。因此：

- 可以裁决同一服务内的客户端协议现象、DTB controller pre-parser 流和配对结果；
- 不能从本目录直接读到状态机捕获的原始异常类型和 traceback；
- 不能严格证明它与 `run_server_02` 是完全相同的部署身份，也不能作正式时延排名或跨 run 归因。

## 3. 两条样例实际发生了什么

### 3.1 聚合结果

| 源样例 | Variant | 请求 | 协议 hard failure | 当前 exact-name 语义偏差 | 实际首个工具分布 |
| --- | --- | ---: | ---: | ---: | --- |
| line 5：task JSON truncation | Vanilla | 30 | 0 | 5 | `task` 25；`bash` 2；`edit` 2；`write` 1 |
| line 5：task JSON truncation | DTB | 30 | 0 | 7 | `task` 23；`bash` 3；`write` 3；`edit` 1 |
| line 10：report write missing | Vanilla | 30 | 0 | 19 | `bash` 19；含 `write` 11 |
| line 10：report write missing | DTB | 30 | 2 | 20 | `bash` 17；含 `write` 10；无工具 3 |

120 个响应中的结构协议检查结果：

- invalid arguments JSON：0；
- arguments schema error：0；
- missing tool name：0；
- non-contiguous tool index：0；
- leaked tool XML：0；
- role tail、tool delta 后 content、terminal 后 chunk：均 0；
- 唯一 protocol hard failure：line 10 DTB 的两个 SSE orchestration error。

### 3.2 line 5：不是 parser 故障

line 5 的预期工具被写死为 `task`，所以合法选择其他工具也被记录为
`expected_tool_name_missing`。例如：

- `dtbtool-l5-a1-dtb-run04-targeted` 返回 `edit`，arguments 完整，修改目标 Java 文件的 import；
  `finish_reason=tool_calls`，严格一个 terminal。
- 对应 Vanilla `dtbtool-l5-a1-vanilla-run04-targeted` 返回 `task`，同样协议完整。

这是 agent 下一步动作选择差异，不是 parser 截断。30 个 pair 中，双方 exact-name 都命中 19 个，
只有 Vanilla 命中 6 个，只有 DTB 命中 4 个，双方均未命中 1 个；不存在 DTB 独有的结构错误。

### 3.3 line 10：`bash` 不等于写报告失败

line 10 的 oracle 只允许下一步为 `write`，但当前上下文中先执行 `bash` 获取时间戳、读取 surefire
报告是合理的多轮 agent 行为。例如：

- `dtbtool-l10-a1-dtb-run04-targeted` 调用 `bash` 读取 surefire reports；
- 对应 Vanilla 调用 `bash date +%Y%m%d%H%M%S` 获取报告文件名时间戳；
- 两者 arguments 均为合法 JSON，且都有 `finish_reason=tool_calls`。

所以 line 10 的 Vanilla 19/30、DTB 17/30 `expected_tool_name_missing` 不能解释成系统错误。
它们最多是模型/tool policy 指标，最终是否完成报告必须继续回放后续轮次或由 LightCode 任务级结果裁决。

## 4. 两个真实 hard failure

### 4.1 `dtbtool-l10-a9-dtb-run04-targeted`

客户端：

- HTTP 200，1,978.408 ms；
- 先收到 31 个 initial reasoning delta，共 121 个 token；
- reasoning 停在：`The testParseAndCleanContentWithThinkBlock failure is due to test data using \``；
- 随后收到：

```text
data: {"error":{"message":"DTB orchestration failed; see server logs",
       "type":"InternalServerError","param":null,"code":500}}
data: [DONE]
```

- 没有 assistant terminal、usage、content 或 tool call。

服务端 audit：一个 completion epoch；只进入 `initial_think_1`，所有已记录 source chunk 的
`finish_reason/stop_reason` 均为空、`raw_finished=false`；最后一个 delta 后直接 `controller_end`，
没有 confidence probe、final answer 或 source terminal。

对应 Vanilla `dtbtool-l10-a9-vanilla-run04-targeted` 正常以 `bash` tool call 结束。它没有命中 exact
`write` oracle，但协议正确。

### 4.2 `dtbtool-l10-a26-dtb-run04-targeted`

客户端：

- HTTP 200，1,941.551 ms；
- 先收到 30 个 initial reasoning delta，共 114 个 token；
- 最后正文依次为 `中使用了 \`\"`、`</`、`think`，停在尚未输出 `>` 的位置；
- 随后同样收到 `InternalServerError` SSE 和 `[DONE]`；
- 没有 assistant terminal、usage、content 或 tool call。

token 证据尤其关键：

```text
raw_text="</"     token_ids=[510]
raw_text="think"  token_ids=[26003]
```

对应 Vanilla `dtbtool-l10-a26-vanilla-run04-targeted` 正常调用 `write`，arguments 完整，
`finish_reason=tool_calls`。

### 4.3 `a13` 是对照样例，不是 protocol hard failure

`dtbtool-l10-a13-dtb-run04-targeted` 没有调用工具，但它与前两例不同：

- initial think 正常产生 `source_finish_reason=stop`、`source_stop_reason=</think>`；
- 随后进入 final answer；
- final child 正常透传唯一 source terminal，`finish_reason=stop`；
- 客户端有 usage 和 `[DONE]`。

它只是模型继续输出分析正文并以 EOS/stop 结束，因而被记为 `expected_tool_call_missing`。从
agent 任务质量看它可能导致当前轮没有推进，应单列观察；从 stream/parser 契约看它是合法完成，
不能与 `a9/a26` 混为 controller failure。

## 5. 根因证据链

### 5.1 vLLM 字符串 stop 允许同一文本采用不同 token 化

`configure_thinking_stop()` 给 initial-think 添加 `</think>` 和 `<tool_call>` 字符串 stop，并设置
`include_stop_str_in_output=True`。vLLM v0.23 的 detokenizer 按**解码文本**查找 stop string；命中后
可以把 `output_text` 截到 stop 末尾，但 `RequestOutput.token_ids` 仍可能带有同一次 speculative batch
中 stop 后的 token。因此 DTB 需要在 terminal delta 上裁掉这些 token，保持 text/token_ids 连续。

这次回放中：

- 成功的 58 个 initial-think `</think>` terminal 全部包含规范 special token `248069`；
- 其中 46 个 terminal 还成功裁掉了 speculative suffix；
- `a26` 的字面量却由普通 token `510,26003,...` 组成，不包含 `248069`；
- 已有成功流也证明普通 `think` 后的 `>` 可对应 token `29`，所以完整文本完全可能是
  `[510,26003,29]`，而不是 `[248069]`。

从失败位置的自然语言连续性和 token 形状看，`a9/a26` 的拆分 marker 都是**普通引用文本**，不是
Qwen reasoning 协议的结束边界：

- `a9` 停在 ``failure is due to test data using ` ``，下一项显然是在反引号内引用测试数据；
- `a26` 已明确生成 ``测试数据中使用了 `\"</think``，marker 位于代码/字符串引号内；
- `a13` 以及其他 canonical `248069` 靠近字符串、反引号或错误消息引用的样例不是反例：在 thinking
  阶段，canonical token 本身就是模型作出的 reasoning→content 协议转换。DTB 随后启动 final child，
  允许它从同一 token prompt 继续未完的自然语言，因此“final-answer 继续同一句”不能证明该 token
  只是普通引用文本。

整个 run 的 final-answer 阶段也有请求把 `248069` 作为正文 token；这同样不推翻假设，因为 final-answer
已经不处于 reasoning 状态，边界 token 在该 phase 是合法 payload，控制器不得配置为 stop。

因此当前样例一致支持更精确的假设：**在 thinking 阶段，canonical `248069` 是 Qwen reasoning
协议的真实结束决定；普通引用文本采用 `[510,26003,29]` 等拆分 token。**原生 Qwen streaming parser
也只在 delta IDs 含 `end_token_id` 时结束 reasoning，并用 `<tool_call>` 的 vocab token ID 作为隐式
reasoning end；普通 token 即使解码为同样的 `</think>`，仍属于 reasoning payload。

### 5.2 DTB 对齐实现错误地要求独立编码 token 精确命中

`dtb/reasoning/parser_boundary.py` 当前流程是：

1. 独立编码 `</think>` 得到 marker token IDs；
2. 在累计 source token IDs 中寻找完全相同的连续子序列；
3. 文本已以 stop marker 结束但 token 子序列不存在时，抛出
   `reasoning stop boundary is visible in text but absent from token ids`。

tokenizer 的 BPE/special-token 编码不是“任意上下文中同一字符串必有唯一 token 序列”。因此第 2 步
不是合法不变量。`a26` 正好提供了真实反例：文本正在形成同一个 marker，token IDs 却是拆分序列。

异常发生在 `consume_output()` 调用 `align_stop_boundary_delta()`、而 terminal source result 尚未写入
audit 之前，所以日志形状正是“最后一个非 terminal delta -> controller_end”。状态机捕获异常后设置
`orchestration_error`；response adapter 再向 vLLM streaming generator 抛出统一 RuntimeError，后者
将其转换为已经看到的 SSE `InternalServerError`。

由于本目录没有未过滤 traceback，不能把上述具体 RuntimeError 写成“日志直接实测”；但成功/失败 token
分布、异常发生位置和源码控制流完全一致，已经比泛称 engine 中断更有解释力。修复后仍应回收一次完整
traceback 来做最终确认。

### 5.3 这不是 Qwen tool parser patch 回归

两个失败都没有进入 final-answer child，也没有向 native tool parser 交付 `<tool_call>`。本轮其余 118
个响应也没有 invalid arguments JSON、XML 泄漏或 tool terminal 异常。因此：

- 能说明当前 parser patch 在这两条定向 prompt 上没有复现先前 JSON 截断；
- 不能用 parser patch 修复本次 reasoning-boundary token alignment；
- 也不能因 Vanilla 30/30 就宣告 server-wide parser no-harm，仍缺相同 parser-before raw 的旧/新
  parser A/B 和 deployment identity。

## 6. 是否属于问题

| 现象 | 裁决 | 理由 |
| --- | --- | --- |
| DTB `a9/a26` SSE error | **是，P0 可用性问题** | 相同 prompt 的 Vanilla 正常；DTB 在工具调用前中断，LightCode 会失去整轮结果 |
| HTTP status 200 | **本身不是问题** | streaming headers 已发送后只能通过 SSE error 表达失败；wire 上已有明确 error |
| `summary.error=""` + `missing_terminal` | **是，审计工具问题** | 回放器把顶层 SSE `error` 当普通无 choices chunk，没有设置 request error |
| `a13` no-tool + stop terminal | **不是 parser/stream hard failure** | 原生 source terminal 完整；属于模型/agent policy 或任务质量问题 |
| `bash/edit/write` 不等于唯一 expected tool | **通常不是问题** | 多轮 agent 可先做准备动作；应与结构协议、最终任务完成率分开 |

## 7. 修复建议

### 7.1 P0：Qwen streaming 改用结构 token stop，但不能只改一行参数

对当前 Qwen3.6 streaming 路径，优先方案应从“字符串 stop 后修补 token 对齐”改为与原生 parser
一致的结构 token stop：

1. 从 tokenizer vocab/native reasoning parser 取得 `</think>` token ID `248069` 和 `<tool_call>`
   token ID `248058`，追加到现有 `params.stop_token_ids`，不覆盖调用者原有 stop token；
2. 不再由 DTB 主动追加这两个字符串到 `params.stop`；调用者原本显式传入的 string stop 保留；
3. 保持 stop token 可见，使 parser-facing token stream 包含真实 source marker；
4. reasoning boundary 检测以 canonical token ID 为准，split plain-text marker
   `[510,26003,29]` 继续作为 reasoning；
5. `<tool_call>` 同样只在 canonical `248058` 出现时触发 implicit reasoning end；
6. 两个 token stop 与 streaming Qwen tool parser patch 原子交付：parser 必须记录空文本
   `[248058]` boundary、跨 final child 保持状态，并用 parser-local structural prefix 解析随后
   `<function=...>`，不得向外部 delta 补 marker。

这条路径还减少了 speculative suffix 风险：vLLM scheduler 对每个 accepted token 逐个执行 stop-token
检查，命中后会在 EngineCoreOutput 前裁掉同批次后续 token；无需 DTB 再从含 suffix 的字符串-stop
terminal 中反推 cutoff。

但**只修改 `configure_thinking_stop()` 还不够**。当前 `detect_reasoning_boundary()` 同时按 text、token
和 stop reason 搜索 marker。即使 string stop 被删掉：

- split marker 若完整落在一个 delta，仍会被 `text.find()` 误判为 reasoning end；
- marker 跨 delta 时，child 最终结束后的 full-text scan 仍会命中最早的普通文本 marker；
- `append_thinking()` 也仍可能按独立编码或文本位置计算错误的边界 token index。

因此必须同步把 Qwen special-token 路径改为 token-ID authoritative，并把边界的实际 token index 一并
传给 `append_thinking()`。目标 Qwen streaming 路径不再保留 controller marker string fallback；
没有可验证 singleton boundary token 的模型应禁用该 token-mode DTB，不能伪造 canonical token。

此外，上游 Qwen parser 当前 streaming 路径按 token ID、non-streaming 完整响应路径按文本
`partition("</think>")`，对 split marker 本身存在语义差异。本轮修复范围只包含当前 LightCode
streaming 场景；non-streaming 保持原有 string-stop/full-parser 路径，不进入本轮验收。

确定性回归测试至少覆盖：

- canonical `248069`：即使位于引号内也按 native streaming parser 结束 reasoning；
- alternate plain marker `[510,26003,29]`：同 delta 和跨 2/3 个 delta 都不得结束 reasoning；
- canonical `<tool_call>` token `248058`：以空 text delta 触发 implicit reasoning end；下一 child
  从 `<function=...>` 继续时 parser 不重复 reset、不泄漏 XML，并正确解析单工具和多工具；
- speculative batch 在 stop token 后的 token 必须由 vLLM 裁掉，DTB audit 不得见到 suffix；
- 调用者原有 string stop / stop token IDs 不得被覆盖；
- streaming DELTA/CUMULATIVE output kind；non-streaming 参数保持迁移前不变；
- 对齐失败必须返回 error，不能泄漏 suffix 或伪造 terminal。

### 7.2 P1：补齐错误观测

- 状态机在 `controller_end` 前记录 sanitized `error_type`、state/phase、child request ID 和错误摘要；
- 服务器归档保留白名单请求对应的未过滤 traceback；
- `tools/dtb/replay_tool_calls.py` 解析 SSE JSON 后若存在顶层 `error`，设置 `Observation.error`，并把它
  分类为 `request_error`；已有 `assemble_observation()` 会因此避免再误记为 `missing_terminal`；
- 报告同时保留 HTTP status、SSE error code 和是否收到 `[DONE]`，不要把 streaming 200 当成功。

### 7.3 不建议的“修复”

- 不要在 controller 异常后伪造 `finish_reason=stop`；
- 不要补写猜测的 tool arguments 或 `</think>` canonical token；
- 不要通过放宽“必须有 terminal”掩盖错误；
- 不要把 exact expected tool name 调到全通过来冒充 parser 修复。

## 8. 修复后的验证顺序

1. **无模型确定性测试**：先用上述 alternate-token fixture 复现当前 RuntimeError，再验证修复后原始 token
   顺序、marker、suffix 丢弃和唯一 terminal 契约。
2. **line 10 生产随机回放**：保持原归档 sampling（不要用 temperature 0 消除会触发 split-token 的路径），
   同一服务上交替 Vanilla/DTB：

   ```bash
   python tools/dtb/replay_tool_calls.py \
     --file docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl \
     --lines 10 \
     --mode both \
     --repetitions 100 \
     --request-id-run run04-boundary-fix \
     --stream \
     --url http://127.0.0.1:9001/v1/chat/completions \
     --output-dir docs/log/perf/agent_tool_call_run04_paired_replay/run_server_04
   ```

3. **必须回传**：完整 deployment identity、两侧 client chunks/summary、DTB stream audit、顶层 SSE error
   统计，以及所有 error request 的未过滤 traceback。服务器继续使用默认 `stream_interval=1`。
4. **通过门**：alternate marker fixture 全部通过；DTB 不再因 marker tokenization 报 orchestration error；
   Vanilla 不新增 invalid JSON/empty arguments/tool terminal 故障；任何内部失败仍必须显式 SSE error，不能
   伪装成成功 terminal。
5. 100 次 DTB 零失败的 95% 上界仍约为 3%，只适合作为修复 smoke。若要把新增 hard-failure rate 的
   上界压到约 1%，再扩到至少 300 次/变体并预声明非劣 margin；之后才恢复 9 场景 LightCode 可用性与
   时延实验。
