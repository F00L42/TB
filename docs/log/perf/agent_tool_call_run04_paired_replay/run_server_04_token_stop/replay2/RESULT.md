# replay2：reasoning parser 修复后服务器回放验收

分析日期：2026-09-04。输入为 `c3f7c0c` / `8f71b85` 上传的本目录结果；用户确认
这是应用 reasoning parser patch 后的重测。历史 `../replay` 保持原结论，不覆盖旧日志。

## 1. 结论

**实测：stripped terminal `</think>` 与普通正文同 chunk 时，Qwen reasoning parser
丢弃整个正文尾段的问题，在本次服务器回放中已解决，可以通过该缺陷的定向验收。**

- 60/60 条 DTB 的实际公共 reasoning，与 controller/pre-parser 的 initial thinking
  正文逐字符一致；总计 8,907 字符，无丢失、重复或额外 terminal marker。
- 本轮确实触发了 **48 次**“非空 text + 尾部 `248069`、text 中没有 stop marker”的
  旧故障条件。对应 **344 字符、124 个非边界 token ID** 的正文全部保留；不是仅测到
  纯 boundary chunk 后得出的假阳性。
- 未修复的原生 parser 重放本轮相同输入，恰好在这 48 条请求丢失这 344 字符。
  修复 parser 加实际公共输出核对后，84/84 个离线检查通过。
- Vanilla 和 DTB 各 60/60 条工具流协议检查通过。工具选择偏离、模型缺必填参数等
  **不计入本次推理框架修复失败**，不要求 parser 补造模型没有生成的参数。

这不是“所有模型语义或所有 parser 路径均已验证”的结论，也不是 DTB 时延收益结论。

## 2. 输入身份与完整性

依据 [manifest.json](manifest.json)、[AUDIT.json](AUDIT.json)：

- 2026-09-04 06:08:41 UTC 创建；原数据 line 5/10，各 Vanilla/DTB 30 次，单并发、
  alternating，合计 120 请求、60 对。固定 `check_length=850`、预算 20,000，其他
  请求覆盖参数见 manifest；未额外指定 temperature/seed。
- 输入 LF SHA256 为
  `8667912b8a5b525c448cd8822281e7aa044df696cedbc2b16b42a46589e66e90`。
  本地 Windows checkout 的 CRLF 规范化后匹配；120 个实际请求 body hash 也全部匹配。
- 从 `chunks.jsonl` 独立重组全部 120 条 SSE，逐字段匹配 `summary.jsonl`，不是只读取
  汇总中的 pass。两份 server 分片共 15,379 条有效 audit，精确覆盖 60 条 DTB，
  请求内 sequence 连续，无未知归属、重复 sequence 或无效记录。
- 原始 `raw_text/token_ids` 与 `forwarded_text/forwarded_token_ids` 全部相同；重新执行
  分类器与上传的 `analysis.json.requests` 完全一致。
- 本轮复用了旧 `request_id_run=run04-token-stop-audit-01`。本分析只读取 `replay2/`
  内的两个分片，未与 `../replay/` 合并；跨轮检索必须使用“目录 + request ID”定位。
- 按用户说明沿用原 vLLM / Ascend 0.23.0 和 Qwen3.6 环境、增量应用 reasoning patch。
  Audit 记录了实际 parser 类绑定，但本目录没有新的已部署 reasoning 源码 hash 或
  完整 tokenizer pytest 报告。下述 patch SHA 是**本地重放源码**的身份，不冒充
  服务器 import 文件的实测 hash；这不妨碍对已上传 HTTP 正文进行保真验收。

## 3. 丢字专项：旧 run 与本轮

| 检查 | 修复前 `../replay` | 修复后 `replay2` |
| --- | ---: | ---: |
| DTB 请求 | 60 | 60 |
| 非空正文 + terminal `248069` | 47 | 48 |
| 纯 `248069`、空文本 | 13 | 11 |
| 纯 `248058`、空文本 | 0 | 1 |
| 实际公共 reasoning 丢字请求 | 47 | **0** |
| 实际丢失普通字符 | 268 | **0** |
| 实际丢失正文对应的非边界 token ID | 117 | **0** |

旧数值来源于 [历史分析](../RESULT.md) 和 [历史审计](../AUDIT.json)。两次是重新采样，
不能把本轮的 344 字符写成“原 268 字符逐请求恢复”；本轮独立验证的是当前 48 个尾段。
其中 42 个尾段包含非空白字符，6 个仅为空白；两类均完整保留。

### 3.1 与最初缺陷完全相同的真实 chunk

本轮 `dtbtool-l5-a10-dtb-run04-token-stop-audit-01`：

```text
delta_text      = " different approach.\n"
delta_token_ids = [2086, 5313, 13, 198, 248069]
stop_reason     = 248069
```

证据定位：`server_filtered_part_01:2048`。对应 `chunks.jsonl:373` 的实际公共 SSE
包含 `delta.reasoning = " different approach.\n"`；`summary.jsonl:19` 拼接结果为：

```text
The task call format is problematic. Let me try a different approach.
```

未修改 parser 重放此请求，结果只到 `Let me try a`，少 21 字符；修复后的服务器已经
发出了该尾段。另一处数字尾段 `l10-a4` 的 `"900\n"` 也完整保留，最终时间戳为
`20260829_170900`，见 `server_filtered_part_01:8089`、`chunks.jsonl:1507`、
`summary.jsonl:67`。

完整 60 请求的来源行、SSE 行、边界 text/IDs 和公共尾段保存在
[AUDIT.json](AUDIT.json) 的 `boundaries`；48 个非空尾段均可在公共 SSE 中直接找到
对应的独立 reasoning delta。

### 3.2 正负对照，避免只证明“离线能修复”

| 对照 | 结果 | 能证明什么 |
| --- | --- | --- |
| 当前 SSE 拼接 vs pre-parser thinking 正文 | 60/60 精确一致 | 本次服务器实际公共输出已保留正文 |
| 未修复 v0.23.0 parser重放本轮相同 text/IDs | 48/60 请求丢字，合计 344 字符 | 本轮覆盖旧缺陷，不是没有触发条件 |
| 修复 parser，含 `--require-public-fidelity` | 84/84，0 失败 | 原生源码检查 + 当前公共输出 + content/tool 结果对账均通过 |

84 个检查由 10 个新增 regression、10 个原路径对照、4 个 detokenizer/unified
检查、60 个完整 parser 链回放组成。负对照输出的 **53 failures = 48 个归档输入
失败 + 5 个合成回归失败**，不是本轮服务器报了 53 个错误。

门禁产物：[reasoning_fidelity.json](reasoning_fidelity.json)、
[reasoning_fidelity_unpatched.json](reasoning_fidelity_unpatched.json)。
修复源码 LF SHA256：
`cdb64a46e7077ff6f2600ce02916cfbd4cdb5bb6f62937673013a68e6addea88`。
重放使用固定原生源码与依赖 stub；实际 token IDs 来自日志，不用字符 tokenizer
重新构造真实 chunk。这不是另一次模型生成或本机 NPU 测试。

## 4. token-stop 与工具调用边界

**实测：60 次 thinking 均由整数 token ID 停止，59 次为 `248069`，1 次为 `248058`。**
边界 ID 均保留为最后一个 token，正文中已剔除 terminal marker，未越过停止点。
thinking 配置仍为 `stop_token_ids=[248069,248058]`、`stop=[]`、
`include_stop_str_in_output=False`，不是打开 include-stop 的 workaround。

本轮补到了过去缺失的一次真实 token-only tool boundary：

```text
request: l10-a11-dtb
initial_think: "好\n\n" -> text="", ids=[248058], stop_reason=248058
final_answer: "\n" -> "<function=write>" -> 参数 -> </tool_call>
public: reasoning="好\n\n", content="", write(content=..., filePath=...)
finish_reason: tool_calls
```

`server_filtered_part_02:1958` 是纯 `[248058]`；`:1960` 起 final child 承接已经打开的
工具调用，`:2186` 原样透传最终结束事件；`summary.jsonl:82` 是完整合法的 `write`。
没有插入第二次思考/probe，没有重发 `<tool_call>`，未把工具 XML 泄漏到公共 content。
这是既有 token-stop/tool parser 链的一次线上覆盖，不是新增 reasoning patch 修了
implicit-tool 行为；也不能用这一例宣称所有 tool boundary 分片都覆盖了。

120 条公共响应均正常结束为 `tool_calls`，各有一个 terminal；HTTP/流解析/参数 JSON
语法/工具 XML 泄漏等协议异常为 0。60 条 DTB 的 source terminal 原样透传、completion
epoch 和 token-stop contract 均通过。新 parser 完整链重放得到的 content、工具名及
arguments 字符串与本轮公共输出逐请求一致。

注意 audit 的 `configured_stop_*` 字段记录的是 context 中的 **thinking 配置**，即使
事件 phase 为 final_answer 也如此；不能据此误称 final child 同样配置了结构 stop。

## 5. 剩余语义偏离：单列，不阻塞框架修复验收

| 分类 | Vanilla | DTB | 本次处理 |
| --- | ---: | ---: | --- |
| 工具流协议异常 | 0 | 0 | 框架验收项 |
| 不符合回放脚本预期工具名 | 29 | 20 | 语义/动作选择，非 parser 修复目标 |
| 已输出 JSON 缺必填参数 | 1 | 2 | 单列语义项；不能由 parser 猜测补参 |
| 上述语义偏离请求合计 | 30 | 22 | 不计为框架失败 |

两侧各有 1 次调用未声明的 `invalid` 工具，已包含在工具名不符计数中，不能重复相加。
其余预期工具名不符也不等价于任务失败，例如生成报告前先用 `bash` 取得时间戳。

DTB 的两条缺参（`l5-a1`、`l5-a22`）有直接源码前输入证据：模型生成了完整闭合 XML，
但本来就只有 `description`；没有生成 `prompt` 或 `subagent_type`。例如：

```xml
<tool_call>
<function=task>
<parameter=description>
AI generate fixed code
</parameter>
</function>
</tool_call>
```

公共 arguments 为 `{"description": "AI generate fixed code"}`，忠实反映了模型输出，
不是 reasoning 尾段丢字，也不是参数被 parser 截断。原始文本和公共 JSON 见
`AUDIT.json.semantic_evidence`，公共行号分别为 `summary.jsonl:2`、`:43`。
Vanilla 缺参样本在 `summary.jsonl:57`；没有 Vanilla pre-parser audit，不把其生成层
根因写成已证实。当前没有证据要求为这些语义项增加推理框架补参逻辑。

因此生成器报告里的 `overall_failures=22/30`、`dtb_acceptance.pass=38/60` 是混入语义
oracle 的汇总，不代表 reasoning 修复或工具传输验收失败。本分析保留原 JSON 分类，
只明确其适用范围，不修改评测 oracle 或掩盖模型质量问题。

## 6. 结论边界与下一步

1. 可以关闭本次 **terminal mixed-chunk reasoning tail discard** 缺陷，保持
   include-stop 默认 False 和三个现有 patch；没有证据支持继续为上述缺参修改 parser。
2. 保真只保证“实际生成并进入 parser 的正文没有被额外吞掉”，不保证模型在语义上总
   选对 thinking 结束位置。例如 `l10-a14` 的公开思考停在未闭合的 `indexOf("` 引用后：
   `server_filtered_part_02:2383` 的该段正文和换行已经完整发出，`:2384` 是纯 `[248069]`；
   final child 又从 `")` 接着生成。它没有被本 patch 吞字，但说明不能把本轮结果扩大为
   “结构 end ID 在模型语义上绝不会用于引用”的证明。较早的普通 `</think>` 引用则由
   非结构 IDs 分片生成并正常保留（`:2380–2381`）。不按字面 `</think>` 出现次数判泄漏，
   也不把模型续写/边界语义纳入本次 tail-discard 修复。
3. 本轮都是短思考：含边界 token 的范围 3–154，中位数 22，均未到 850 检查点；无
   probe、iterative thinking 或 forced-close。不能用本次时延/semantic-clean 子集
   均值裁决长思维链优化效果，亦不能据此推断隐藏 probe 成本。原始时延数据仍在
   [paired_summary.json](paired_summary.json)，本次不作性能排名。
4. 下一阶段可进入冻结版本下的 9 场景端到端回归：保持单并发、默认 stream_interval=1，
   工具传输保真与任务语义/评测质量分开验收，再用同身份 Vanilla/DTB 的 telemetry-off
   配对数据判断 E2E。不必等待模型语义偏离清零再结束本轮框架修复；仍不能把这些
   偏离当成任务质量通过。新 run 使用独立 ID，并记录部署 reasoning parser hash。

## 7. 复算命令

在 TB 根目录执行（读取本次归档，不访问推理服务）：

```bash
REPLAY=docs/log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/replay2

# SSE/summary、输入、归属、正文与语义分层复算，预期退出 0。
python "$REPLAY/audit_evidence.py" --output "$REPLAY/AUDIT.json"

# 修复源码 + 已录制的真实公共 reasoning，预期 84/84、退出 0。
python tools/dtb/reasoning_tail_fidelity.py --apply-repo-patch \
  --replay-dir "$REPLAY" --require-public-fidelity \
  --output "$REPLAY/reasoning_fidelity.json"

# 未修复源码负对照，预期 53 failures（其中 48 个真实输入）、退出 1。
python tools/dtb/reasoning_tail_fidelity.py --replay-dir "$REPLAY" \
  --output "$REPLAY/reasoning_fidelity_unpatched.json"
```
