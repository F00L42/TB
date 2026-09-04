# run_server_04_token_stop：915bec9 测试结果审计

## 1. 结论

**token-stop 已在目标服务器生效；旧的字符串 stop 对齐异常本轮没有复现，但原子重构尚未完整通过。**

1. **实测**：120 次请求全部返回工具调用和唯一 `tool_calls` terminal，HTTP/SSE error、非法
   arguments JSON、工具 XML 泄漏、terminal 后尾流均为 0。原始脚本的 57 个 FAIL 是
   55 个 exact-tool-name 偏差和 2 个缺必填参数，不是 57 次 parser/服务器故障。
2. **实测**：60 次 DTB 的 initial-think 都以整数 `source_stop_reason=248069` 结束，边界 ID
   保留，边界后无多余 source token；普通 token 拼出的 `</think>` 在 2 个请求中继续作为正文
   生成，没有触发旧的 orchestration error。token-stop 的主要修复目标在本轮获得直接证据。
3. **新发现，实测 + 源码确定 + 离线复现**：47/60 次 DTB 在公共 `reasoning` 中丢失边界
   chunk 的正常文本，共 268 字符、117 个非边界 token，其中 17 次包含字母或汉字。
   controller 的 raw/forwarded 完全一致；丢失发生在原生 Qwen reasoning parser 的
   “有 end token ID、无 marker text → return None”分支。现有 audit 没有检查这一层，
   因而 `token_stop_contract_valid=60` 不等于原生 parser 等价已通过。
4. **实测**：DTB `l5-a30` 的模型 source XML 本来就只输出 `description`，不是 parser
   截掉 `prompt/subagent_type`。Vanilla `l10-a14` 缺 `write.content`；另外 Vanilla 两次
   调用了未声明的 `invalid` 工具。后者被现有工具误归为普通工具选择差异，不能当成合理替代动作。
5. **覆盖缺口**：60 次均先生成 `248069`，没有一次以 `248058` 直接结束 thinking。
   token-only tool-start 的跨 child 兼容路径没有被本轮线上结果覆盖；这会使 runbook 最后的
   `tool_call_boundary_not_covered` 断言失败，但不是已观测到该路径执行出错。

当前裁决：旧编排异常 **定向 smoke 通过**；现有工具 JSON/terminal 修复 **本轮通过**；
完整 reasoning/tool parser 等价 **未通过**；Vanilla no-harm 和 LightCode 9 场景质量/时延
**尚不能最终验收**。不要回退到字符串 stop，也不要继续仅调 DTB 预算来解决当前问题。

## 2. 实验身份、完整性与证据边界

| 项目 | 本轮证据 |
| --- | --- |
| 上传提交 / 对应实现 | `915bec9` / `cc9bfbe` 原子 token-stop 重构 |
| 输入 | `../input/cases.jsonl` 第 5、10 行；与 run_server_03 相同输入 SHA256 |
| 输入 SHA256 | `8667912b8a5b525c448cd8822281e7aa044df696cedbc2b16b42a46589e66e90` |
| 请求 | 每条上下文 Vanilla/DTB 各 30 次，共 120 请求、60 pair；单并发、alternating |
| 模型 | `qwen3.6-27b`，模型目录 `/opt/w00618058/Qwen3.6-27B`，max model len 262144 |
| 采样 | 保留源请求：max_tokens 32000、top_p 1、tool_choice auto；temperature/seed 未显式设置 |
| DTB 参数 | budget 20000、check 850、stride 500、num-thinking 5、min-think 1、prob 0.93 |
| 协议 | `qwen3`；think end 248069；tool call begin 248058 |
| 遥测 | `stream_audit`；60 个 DTB 精确白名单；stream_interval 默认 1，不引入其他值 |
| 运行时版本记录 | vLLM 0.23.0、vLLM-Ascend 0.23.0、torch 2.10.0+cpu、torch-npu 2.10.0.post4 |

独立复算结果见 [AUDIT.json](AUDIT.json)，脚本见 [audit_evidence.py](audit_evidence.py)：

- 从 `chunks.jsonl` 重建的 120 个 observation 与上传 `summary.jsonl` 完全一致，未见 SSE
  顶层 error 或 JSON parse error。
- 根据输入及 manifest 重建的全部 request body hash 与客户端 request 记录一致。第 5、10 行
  各自内部作 A/B；没有把 DTB 历史上下文和 Vanilla 历史上下文互相当作相同 prompt 配对。
- 12,553 条 server audit JSON 全部可解析；60 个请求 ID 精确等于白名单，各请求 sequence 连续；
  所有 source/forwarded text 与 token IDs 一致。
- 重跑现有 analyzer 的逐请求结果与上传 `analysis.json` 完全一致。新发现不是上传 analysis
  过期，而是它的验收范围不包含 reasoning payload 丢失和未声明工具。
- 在内存中对固定 vLLM checkout 应用仓库 patch，得到的 parser/serving 文件 SHA256，与
  `runtime_identity.txt` 中的值完全匹配；controller 入口文件和 audit 配置也匹配，比较时统一 LF。

| 文件 | 匹配的 SHA256 |
| --- | --- |
| Qwen tool parser | `395bc73fd35155d8d85f19d6c101e5fc782d55abf33374cd0c642e3ac3d2e6c4` |
| serving | `393f79762e9b8c1424c0a9586bffe614e077372405620b430bb009be9bb0f945` |
| controller 入口 | `085d02b8676f3479f9a37c71b9801749d3818bd1daa2d3674645513319cf63eb` |
| audit 配置 | `dc35f129547a9fea8da9ed117ae4a0117a9ffc22e52caadf7c69af75ea3d19b3` |

因此本轮不应再用“可能没部署 token-stop/parser patch”解释已有现象。但上述三份源码 hash
不是整个运行环境的完整身份证明：未上传实际启动脚本/完整 commit 清单、全部 DTB 模块 hash、
CANN/模型 revision、reasoning parser 文件 hash 和未过滤 server.log。未声明变更的环境按既有
模板理解；不能把模板继承当作本轮逐项重新实测，也不能据此裁决跨 run 性能变化。

## 3. 为什么脚本仍有 FAIL：按真实现象重新分层

### 3.1 原脚本统计

| 上下文 | Variant | 请求 | 现有工具协议失败 | exact-name 偏差 | 缺必填参数 | overall FAIL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| line 5：task 重试 | Vanilla | 30 | 0 | 9 | 0 | 9 |
| line 5：task 重试 | DTB | 30 | 0 | 4 | 1 | 5 |
| line 10：生成报告 | Vanilla | 30 | 0 | 20 | 1 | 21 |
| line 10：生成报告 | DTB | 30 | 0 | 22 | 0 | 22 |
| 合计 | Vanilla | 60 | 0 | 29 | 1 | 30 |
| 合计 | DTB | 60 | 0 | 26 | 1 | 27 |

`replay_tool_calls.py:1168` 只要 `anomaly_reasons` 非空就打印 FAIL 并最终返回 1；
`analyze_tool_call_run.py` 也只有 `pass/vanilla_pass` 返回 0。因此 semantic deviation
足以导致非零退出，不能从退出码推断服务异常。未上传 `exit_status.txt`，这里说明的是源码规则，
不是声称读取了该文件。

60 pair 中：双方 exact oracle 均通过 19；仅 Vanilla 通过 11；仅 DTB 通过 14；双方均未通过
16。它们不是端到端任务质量转移表，因为没有执行后续工具/编译/评测。

### 3.2 有效工具但不是指定的下一步：53 个

55 个 exact-name 偏差中有 53 个调用了当前 tools 列表中的工具且 arguments 满足其 schema。
这只证明工具调用格式可执行，不证明任务策略正确。

- line 10 Vanilla `a1`：`bash {"command":"date +%Y%m%d%H%M%S", ...}`；
  DTB `a3`：`bash ls .../surefire-reports/`。写报告前取时间戳、查测试报告是合理多轮动作，
  不是 `write` parser 失效。
- line 10 DTB `a29` 通过 `bash` 中的 heredoc 写 JSON/Markdown 报告，虽然实际动作是写报告，
  仍被只接受 `write` 的 oracle 标为 FAIL。
- line 5 改选 `write/edit/bash` 可能是重试策略，也可能绕过应由子 agent 完成的代码生成流程；
  `echo ready` 等更不能直接判任务成功。需要继续执行任务才能裁决。

对应证据：`replay/summary.jsonl:61`、`:66`、`:118`；源 line 10 的最后一轮已创建报告目录，
而 todo 中报告步骤仍在进行，工具选择是 `auto`，并未强制下一步 `write`。

### 3.3 两次缺必填参数：是问题，但不是同一种截断

**DTB `l5-a30`，`replay/summary.jsonl:59`**：

```json
{"name":"task","arguments":{"description":"Generate fixed LlmInvokeApi code"}}
```

缺 `prompt` 和 `subagent_type`，不能正常启动目标子 agent。但是 parser 前完整生成的是：

```xml
<tool_call>
<function=task>
<parameter=description>
Generate fixed LlmInvokeApi code
</parameter>
</function>
</tool_call>
```

证据在 `replay/server_filtered_part_01:7490` 至 `:7496`：模型自己关闭了 parameter、function
和 tool call，最后正常 source terminal；并未生成另外两个 parameter。初始 thinking 只有
19 个 token（含边界），没有 probe、预算强制截断或 answer cue。

**实测结论**：这是 source 层的必填参数遗漏，parser 忠实返回了模型生成的内容。不是原先
`{"description": ...` JSON 缺右括号的流式截断。源码完整文本 tool parser oracle 和部署版
streaming tool parser 对这条 source 都得到相同结果。

**原因假设**：line 5 输入的最后两次 assistant 调用已经是 `invalid`，tool result 都在重复
“task JSON parsing failed / Expected '}'”。本次生成说明它试图用更简单的 description 重试。
这种错误历史可能诱导“只输出 description”或模仿 `invalid`，但不能仅凭自然语言自述证明
因果；应以保留历史/移除这两对失败消息的隔离诊断 A/B 验证，原始回归输入仍须保留。

**Vanilla `l10-a14`，`replay/summary.jsonl:88`**：

```json
{"name":"write","arguments":{"filePath":"/root/.local/share/opencode/log/evaluation_report_20260829171000.json"}}
```

缺 `content`，JSON 合法但工具无法写入报告。公共 SSE 有完整 JSON、唯一 `tool_calls` terminal，
不是空 arguments 或缺 terminal。**本轮没有 Vanilla 的 parser-before raw**，所以不能严谨地
把它直接定为模型遗漏，也不能宣称已排除 Vanilla parser 丢参。需抓取同样的 pre-parser delta
才能像 DTB `a30` 一样区分 source 未生成与 parser 未转出。

两次 schema 失败位于不同 pair；对应另一侧均为合法 `bash`，不是同一轮输入生成了同一错误。
“两侧各 1 次”不足以证明非劣，也不支持 DTB 独有的参数截断回归。

### 3.4 两次未声明工具：Vanilla 独有，现有审计漏分层

Vanilla line 5 `a8/a21`，分别在 `replay/summary.jsonl:16`、`:41`，返回完整的
`name="invalid"` 和合法 JSON。当前 request 的 tools 列表**没有** `invalid`。

输入历史中存在框架记录的 `invalid` 调用，但历史里的工具名不等于本次可调用工具。
这两次不能归入“合理换工具”。`replay_tool_calls.py:464` 只有找到同名 schema 才验证参数，
找不到时跳过；因此它们只被记为 `expected_tool_name_missing`。

**实测**：公共结果选择了未声明工具，正常工具分发不能把它当作已注册工具执行。
**假设**：模型模仿输入中的 `invalid` 错误历史；仍需 Vanilla raw 证明实际 source function 名称。
其 arguments 中的 `JSON Parse error` 是模型返回的字符串，不是本轮服务器抛出的 SSE error。

据此，本轮应分开报告：有效工具选择偏差 53（V 27 / D 26）、schema 不满足 2（各 1）、
未声明工具 2（V 2 / D 0）。后两类才是已观察到的 4 个工具调用不可按声明正常执行的结果，
不能把其余 53 个直接当作正确或错误的任务结果。

## 4. token-stop 生效的直接证据

### 4.1 实际 source boundary 不再是字符串 stop

所有 60 个 thinking child 的 audit 均记录：

```text
boundary_stop_mode = token
configured_stop_token_ids = [248069, 248058]
configured_stop_strings = []
include_stop_str_in_output = false
source_finish_reason = stop
source_stop_reason = 248069                 # JSON 整数
token_ids[-1] = forwarded_token_ids[-1] = 248069
alignment_dropped_token_count = 0
```

不是只看 YAML 判定开启。source terminal 的实际形状是：

- 13 次：`raw_text=""`、`token_ids=[248069]`；
- 47 次：`raw_text` 含边界前正常文字/标点/换行，IDs 长度 2–5，最后一项是 248069；
- 60 次的 raw/forwarded 中均没有该终止 marker 的可见 `</think>` 文本，且边界后 token 为 0。

这补齐了此前“公共 API 空 terminal 是否代表 parser-facing 也空”的证据：**本轮确实存在
parser-facing token-only boundary，但不代表整个 boundary chunk 一定没有其他正文。**

源码对应：`dtb/reasoning/parser_boundary.py:210` 只在 streaming thinking 添加两个 stop ID；
`:247` 以整数 stop reason/ID 检测边界；`:289` token 分支不再作字符串补齐或裁剪。
vLLM `v1/engine/detokenizer.py:107` 在 include=false 时仅跳过最后 stop token 的解码，同时
保留其 ID；scheduler `_update_request_with_output()` 按命中的 stop token 裁掉同批后缀。

### 4.2 普通引用文本不再误触发停止

DTB line 10 `a26`（注意不是 run_server_03 的同一条随机输出）在 thinking 内完整生成 4 次
普通文本 `</think>`；`a29` 生成 1 次。代表性证据：

```text
server_filtered_part_02:3110
raw_text = " `</think>`"
token_ids = [1510, 510, 26003, 42181]
source_stop_reason = null

server_filtered_part_02:3798  raw_text="</",     ids=[510]
server_filtered_part_02:3799  raw_text="think>` tag in", ids=[26003,42181,4619,303]
```

两条都继续 thinking，直到 `:3144` / `:3867` 的真正 `248069` 才切换 final；最终正常返回
`bash`，虽然不满足 exact `write` oracle，但没有 orchestration error。这直接验证了
run_server_03 中普通引用文本碰撞字符串 stop 的修复路径。

canonical `248069` 即使出现在语句/引用未完的位置，仍应按 Qwen 协议结束 reasoning；
不能因为公共 reasoning 看起来不完整就把它改判成普通文本。本轮另有真实丢字，需要按下一节
的 token/text 证据区分，不能混为模型语义判断。

### 4.3 final 阶段没有被 marker 再次截断

29 个 DTB 请求的 final source 中，137 个 delta 包含 `248069`，这些 delta 均未终止。
例如 `replay/server_filtered_part_01:98` 的 `</think>\") 索引` 继续进入工具参数。
全部 60 个 final child 最终是原生 `stop_reason=null` terminal，且原样向外透传一次。

final/probe 参数从原 request 重新构造（`generation_states.py:593`、`:744`），不调用
`configure_thinking_stop()`。audit 中 final 行仍显示的 `configured_stop_token_ids` 是
context 保存的 **thinking 配置**，不是 final child 的 SamplingParams；不能误读为 final 也
配置了这两个 ID。本轮无 probe，不能把 probe runtime 也算作已覆盖。

## 5. 新残留缺陷：reasoning boundary chunk 的正常正文被吞

### 5.1 最小真实样例

DTB line 5 `a1`，`replay/server_filtered_part_01:8`：

```text
已透传 reasoning: I need to properly escape the quotes in the prompt. Let me try a
raw_text = forwarded_text = " different approach.\n"
token_ids = forwarded_token_ids = [2086, 5313, 13, 198, 248069]
source_stop_reason = 248069
```

公共结果 `replay/summary.jsonl:2` 的 reasoning 恰好只到 `Let me try a`。
` different approach.\n` 没有出现在公共 reasoning 或 content 中。另一个中文例子是
line 10 `a5`，`server_filtered_part_02:239` 正常文字 `时间戳。\n` 被丢，公共结果只到
`好，让我生成报告文件。首先获取当前`（`summary.jsonl:70`）。

47 个非空边界 chunk 全部如此；13 个纯空边界 chunk 没有可丢的正文。总计 268 字符、
117 个非边界 token；41 次丢失非空白字符，17 次包含词语/汉字，并非全是不可见换行。

### 5.2 根因到源码

固定 vLLM `vllm/reasoning/qwen3_reasoning_parser.py:188–201`：

```python
if self.end_token_id in delta_token_ids:
    end_index = delta_text.find(self.end_token)
    if end_index >= 0:
        # 正常拆 reasoning/content
        ...
    # end_token_id in IDs but not in text (already stripped)
    return None
```

token-stop + include=false 使 special marker 不在 text 中，而同 chunk 的边界前正文仍在。
此分支把“marker 文本被剥离”误当作“整个 delta 都没有应输出正文”，直接丢弃完整 delta。
随后 unified `Parser.parse_delta()`（`abstract_parser.py:834–850`）把 reasoning 标记为结束，
不会在后续 chunk 补回这些文字。

**离线实测**：从未修改的固定 Qwen 源码中提取并执行该 streaming 方法，用 60 个真实 initial
delta 重放，全部得到与上传公共 reasoning 完全相同的结果，包括全部 47 次丢字。该验证不加载
模型/设备；它证明源码机制与日志逐字符一致，不冒充目标服务器的新一轮测试。reasoning parser
文件本身未上传 hash，严格的运行时身份补证仍应补该文件 hash。

这解释了“stop 实现为什么会影响 parser”：采样边界仍是同一个 token，但 detokenizer 交给
parser 的 text 形状变了；tool parser 已补 token-only start，reasoning parser 的混合
`普通正文 + end ID、无 end 文本` 分支尚未适配。

这是输出保真缺陷，**不是本轮 task 参数遗漏的直接原因证据**：source prompt/token 链没有丢，
tool parser 前后的调用内容也能对上；本轮没有证据表明丢失几个 reasoning 末尾词直接导致
任务失败。但按原生 parser-facing 等价契约，不能因此宣布原子修复完成。

### 5.3 为什么既有离线门禁与 stream_audit 没发现

- audit 的 raw/forwarded 对比位于 controller→parser **之前**，此次这一段确实无损。
- JSON、schema、terminal 检查均不核对公共 reasoning 是否完整。
- `parser_partition_equivalence.py` 的 token-only gate 主要验证 tool parser；
  `_run_delegating()` 没绑定真实 Qwen reasoning parser，不能替代完整 reasoning→tool 链路测试。

因此不能只把原有测试再跑一次；必须增加边界前普通正文的守恒检查和真实 reasoner 组合测试。

## 6. tool parser 修复情况与尚未覆盖的边界

本轮 60 条 DTB 的 final source 已用 SHA 匹配的部署版 tool parser 离线重放，生成的 tool name、
arguments 字符串和 content 与公共结果逐项一致；同一完整 source 的非流式 tool parser oracle
也得到相同工具名与解析后 arguments。这里 full parser 仅用作离线 oracle，**没有执行或扩展
生产非流式 DTB 测试范围**。

因此这 60 条 raw 中没有发现分片诱发的 tool JSON 截断；`l5-a30` 的缺参在完整文本 oracle
中同样存在。旧工具 JSON、XML 泄漏、terminal 故障本轮未复现。Vanilla 也没有现有审计可见的
JSON/terminal 故障，但缺少 Vanilla raw，不能据此断言它的缺参一定不是 parser 问题。

**线上未覆盖**：全部 thinking boundary 都是 `think_end`。final 中虽然有 65 个 `<tool_call>`
token，但这些 delta 都携带 marker 文本，它们不等于 thinking 在 token-only `[248058]`
处停止、下一 child 从 `<function=...>` 继续的路径。runbook 最后一个覆盖断言会失败正是此原因。

本轮 line 10 从旧 run03 的 DTB 2/30 orchestration failure 到当前 0/30，并且包含成功通过的
split-marker 请求，支持机制修复。但 0/30 的单侧 95% 失败概率上界仍约 9.5%；两个 prompt
合计 0/60 约 4.9%。这只是独立重复假设下的近似范围，不能外推成所有任务零失败或非劣证明。

## 7. 时延只作观察，不作优化裁决

| 指标 | Vanilla | DTB |
| --- | ---: | ---: |
| 请求 E2E 均值 | 8986.507 ms | 8760.484 ms |
| E2E 中位数 | 8304.028 ms | 8339.024 ms |
| E2E P95 | 23154.114 ms | 21998.389 ms |
| completion tokens | 50,932 | 46,782 |

60 pair 的 DTB−Vanilla 均值为 −226.023 ms，但配对差中位数是 **+544.830 ms**，
DTB 只在 22/60 pair 更快。19 个双方 exact oracle 通过的 pair 中，也仅 7 个更快，差值中位数
+724.042 ms；筛选后的数字不能代表全体任务。

关键限制：

- 60 个 initial-think 只有 10–451 token（含边界），中位数 19，总计 2982，全部在 850 之前
  自然结束；无 iterative think、hidden probe、controller forced close 或 answer cue。
  本轮没有实际触发“专门压缩长思考”的策略，不能拿 token 差解释为该策略收益。
- metrics 从 0 增至 180 个 stop 完成、0 length/abort/error，与 60 Vanilla 单请求 +
  60 DTB 双 child 一致。总 generation 为 97,714，恰好等于两侧 completion 之和；
  本轮 hidden probe 为 0。DTB 2982 initial + 43,800 final = 46,782。
- metrics 的总 prompt 17,032,512，与 Vanilla 5,676,510 + DTB initial 5,676,510 +
  DTB final 5,679,492 对账；final 比原 prompt 多的 2982 正好是 thinking token，没有额外
  answer cue。重复 prompt token 数不代表同量 uncached prefill；不能据此断言 KV 没有复用。
- 这是 stream_audit 排障 run，输出工具/参数长度不同，且无请求级 phase queue/prefill/decode
  账本。全局 metrics 不能完成 controller 成本分解。
- `latency_ranking_valid=false` 的代码条件实际要求所有 pair 同时语义干净，不只是 protocol
  干净（`replay_tool_calls.py:885`）。自动 RESULT 的末句不够完整；本报告不改变历史产物。

结论是：**当前没有可信的长思考时延优化证据，也不能把随机生成差异归因于 token-stop。**

## 8. 下一步：最小闭环，不扩大参数搜索

1. **优先修 reasoning parser 混合 boundary delta。** 保持 stop 以 ID 为准。对已验证为
   末尾 end token、marker text 缺失的 chunk，应保留边界前的真实 `delta_text`，而不是
   `return None`；纯空 boundary 仍无正文。不要无条件把 ID 位于中间的全部 text 归到
   reasoning，也不要重新用字符串猜测/补造 source token。同步覆盖 implicit tool boundary
   的边界前正文。这是待实现方向，本次只分析，未修改生产 parser/DTB。
2. **先离线重放这 60 条 raw。** 组合真实 Qwen reasoning parser + unified parser +
   tool parser；检查 47 条丢字样例正文恢复、13 条空边界不多输出、tool arguments 和唯一
   terminal 保持不变。增加 marker-present/token-only、带普通前缀、跨 child 单/多工具、
   split literal marker 的确定性 fixture。工具 checker 增加未声明工具分类，保留 semantic
   与 protocol/保真缺陷的分层，不为“全绿”取消覆盖断言。
3. **补 `[248058]` 的真实线上覆盖。** 使用经筛选能直接从 thinking 进入 tool 的相同请求格式
   输入，要求 audit 实际 `source_stop_reason=248058`；不能把 final 中出现工具 token 算作覆盖。
   若真实模型始终先输出 248069，明确该分支仍只有确定性 fixture 证据，不伪造自然覆盖结果。
4. **补 Vanilla pre-parser 排障观测。** 当前 DTB whitelist 不会捕获 Vanilla；仅把 Vanilla ID
   加进配置不够。需要隔离、默认关闭的 native parser 输入观测或录制 raw 的测试入口，同时
   抓取现有 streaming SSE，定位 `write.content` 缺失与 `invalid` 工具；优先记录 reasoner/
   unified/tool 三个源码 hash。不得用另一轮非流式重新生成充当同一 source 的证据。
5. **再作 no-harm 与任务闭环。** 原始 line 5/10 保留生产采样、单并发、默认 stream_interval=1
   扩大重复；对 source 完整却 parser 丢失的错误零容忍，对模型/schema 失败报告配对分布与
   不确定性，不要求 Vanilla 本身永不失败。必要时以 ≥300 次/变体作为约 1% 量级的零故障
   smoke 上界，而不是只看平均成功率。随后恢复 LightCode 同配置 9 场景，核对报告完整性、
   主/子 agent 流程及编译/DT 质量；通过后以 observability=off 做正式时延配对。

本地复算命令（仅读取上传输入与固定源码，生成分析 JSON，不访问服务器）：

```bash
python docs/log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/audit_evidence.py \
  --output docs/log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/AUDIT.json
```

退出 0 表示**证据复算一致，包括成功复现残留缺陷**，不是产品验收通过。

## 9. 变更记录

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-09-04 | 完成 915bec9 上传结果审计；核实 token-stop 部署与 split-marker 修复；新增 reasoning 末尾丢字及未声明工具分层，保存可复算脚本和证据 | 现有 120/120 工具协议通过未覆盖完整 reasoning parser 等价，不能将总体 FAIL 或总体 PASS 直接当作可用性裁决 |
