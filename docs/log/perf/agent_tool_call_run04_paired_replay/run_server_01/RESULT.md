# run04 tool-call paired replay 裁决

## 结论

本轮 600 个响应（300 个 DTB、300 个 Vanilla）中有 196 个响应至少命中一个异常标签。它复现了
端到端 case36 的 DTB 工具参数截断，但主因不是 parser：133 个 DTB invalid-JSON 中，132 个在
parser 前已经没有完整 XML；其中 131 个 final-answer 子请求明确被字面 `</think>` stop 截断。
源码同时确认 final-answer 阶段错误地追加了该思考 stop。剩余 1 个样本的 parser 前文本包含完整
tool XML，而 SSE arguments 缺 `}`，是独立的 multi-call burst parser 缺陷。

196 不能直接解释为 196 个传输故障。53 个 `expected_tool_name_missing` 是固定单工具 oracle 与模型
选择不同，例如报告场景先调用 `bash` 而非 `write`；它们需要语义裁决，不能和 malformed JSON、
XML 泄漏或 terminal 缺失放在同一个协议失败分母中。可用性门未通过，因此本轮时延不参与排名。

## 输入与证据边界

- 实测输入：`summary.jsonl`、`chunks.jsonl`、`analysis.json`、Prometheus 前后快照和
  `paired_summary.json`；600/600 客户端结果、300/300 pair 均存在。
- 本目录没有原始 `server.log`，不能本地重新提取；根因复核使用已上传 `analysis.json` 中按请求保存的
  parser 前 raw、child phase、source finish/stop 和 terminal 契约。
- `manifest.json` 明确记录 10 个 case、每个 30 次、Vanilla/DTB 交替、DTB 固定参数；其余未声明项
  按实验说明继承模板。本目录未固化的源码/patch hash 不能从该声明反推出，下一轮必须实际记录。

## 异常统计

| 客户端异常标签 | DTB | Vanilla | 两边均出现该类型 | 性质 |
| --- | ---: | ---: | --- | --- |
| `invalid_tool_arguments_json` | 133 | 0 | 否 | 协议 hard failure |
| `expected_tool_name_missing` | 24 | 29 | 是 | 工具选择/oracle 偏差，不自动等于协议故障 |
| `expected_tool_call_missing` | 9 | 1 | 是 | 模型、控制器或终止行为；需 raw 定责 |
| `missing_terminal` | 1 | 0 | 否 | completion lifecycle hard failure |

共有 4 种标签，2 种在 Vanilla 和 DTB 中都出现。标签计数为 197，而异常响应为 196，是因为一个 DTB
响应同时命中 `expected_tool_call_missing` 与 `missing_terminal`。

按 pair 统计：128/300 两边都 clean；172/300 至少一边异常；24/300 两边都有异常；只有 12/300
两边异常标签完全相同，均为 `expected_tool_name_missing`。因此“共同错误”若按类型是 2 种，若按
paired attempt 是 24 对，若要求同一标签则是 12 对。

## 对端到端故障的复现

| 原故障族 | 回放观察 | 裁决 |
| --- | --- | --- |
| case36 `task` arguments 截断 | line 1～6 的 180 个 DTB 请求中 125 个 invalid JSON；180 个 Vanilla 为 0 | **已稳定复现**，且明显为 DTB 路径特异 |
| case35 checkout action 缺失 | line 7～8 没有 `expected_tool_call_missing`；只有 1 个 DTB、2 个 Vanilla 选择 `todowrite` 而非固定预期 `bash` | **未复现原缺调用**；这是 alternate action，不是 parser 证据 |
| case36 report write 缺失 | line 9：DTB/Vanilla 各 1 次缺调用；line 10：DTB 8 次缺调用，Vanilla 0 次，但 Vanilla 有 19 次先选 `bash` | **部分复现**；两边均可出现模型/流程选择偏差，DTB 更高，需修复后重跑 |

## 根因链

### 1. DTB final-answer stop 碰撞：主因

133 个 DTB invalid-JSON 响应中：

- 132 个 parser 前 raw 有 `<tool_call>`，但缺完整 `</function></tool_call>`；parser 不可能恢复模型未生成
  的 XML，也不应人工补齐。
- 这 132 个中，131 个 final-answer child 的 `source_finish_reason=stop` 且
  `source_stop_reason=</think>`；另 1 个 stop reason 为空。
- raw 尾部恰好停在工具参数正文中的字面 `</think>` 前：task 描述引用
  `THINK_BLOCK = "</think>"`，report/write 内容也会讨论该 marker。
- 源码确定：旧 `configure_final_answer_stop()` 对 final-answer sampling params 调用
  `_append_stops(params, "</think>")`。因此已关闭 reasoning 的最终答案仍把思考边界当 stop，导致 XML
  参数正文在合法字面 marker 处终止。

修复：final-answer 使用从原请求重新构造的 sampling params，不再追加任何 DTB 内部思考 stop；思考
阶段仍保留 `</think>` 与 `<tool_call>` boundary stop。新增状态机回归断言，确保 initial-think 含该
stop、final-answer 不含该 stop。

### 2. Qwen3-Coder streaming parser 分片敏感：独立次因

`dtbtool-l10-a5-dtb-run04-p0` 的 parser 前 raw 至少包含完整 tool XML，但客户端首个 `write`
arguments 缺最终 `}`；同一 final child 后续还有相邻调用/正文 burst。这命中 legacy parser 在
tool-start、header、JSON-open、parameter-close 与 call-advance 各处分支提前 return 的缺陷族。

新的 v0.23.0 patch 不再逐 parameter 猜测可增长 JSON 前缀，而是在累计文本中：header 完整即发工具
名称，`</function>` 完整后用同一非流式 XML parser 生成 canonical JSON arguments 并一次发送；单个
callback 可聚合多个连续 index 的 `DeltaToolCall`。它覆盖社区 #39598/#43074/#45256/#45439/#45440
的 parser burst 形状，且不会为 incomplete XML 合成 close。patch SHA256：
`f8405cdfa04d69e942ac77fdb21f93a5d94a0c749d471ed03f9c921b2336dad2`。

### 3. 其他可用性项

- `dtbtool-l10-a28-dtb-run04-p0` 是唯一 completion lifecycle failure，同时命中缺调用与缺 terminal；
  它不由 parser patch 解释，下一轮必须继续保持 terminal/passthrough 审计。
- 53 个工具名偏差应从协议门移到语义门。尤其 line 10 的 `bash` 可能是生成报告前的合法检查步骤；
  下一轮使用允许工具集合/状态转换 oracle，并另报具体选择分布。
- `analysis.json` 的历史 diagnosis 把 raw-incomplete、parser failure 和工具选择混为
  `downstream_parser_or_finish_failure`。分析器已增加 stop-collision、complete-XML parser failure、
  truncated XML 与 semantic tool choice 的独立分类；因本归档没有原始 server.log，未覆盖写回旧
  `analysis.json`，上述复核来自其已保存的请求级 raw 证据。

## 时延

原始 300 对中 DTB 较快 177 对、配对中位数表面 speedup 15.541%；但只剩 128 对双方协议 clean。
clean 子集上 DTB 仅快 43/128，中位 `DTB - Vanilla = +546.507 ms`，即 DTB 中位慢 15.773%。这两个
数字都不能用于 DTB 时延结论：失败请求提前被 `</think>` 截断会制造虚假加速，clean-only 又是由故障
机制选择出的偏差子集。本轮 `latency_ranking_valid=false` 的裁决保持不变。

## 下一轮完整 paired replay

### A. 离线与部署身份门

1. 对固定 `third_party/vllm` 执行两个 patch 的 `git apply --check`；保存 vLLM/vLLM-Ascend commit、
   import path、实际 parser 源码 SHA、DTB commit、两个 patch SHA、配置 hash 和启动加载类。
2. 对应用新 parser patch 的源码运行 `parser_partition_equivalence.py --require-equivalence`。必须同时
   通过历史 parameter-close、whole body、whole call、last-param+close 和 first-close+second-call，
   且 streaming 重组结果与非流式 parser oracle 一致。
3. 状态机测试必须证明 final-answer stop 不含 DTB 注入的 `</think>`；用户原生 sampling stop 不得被
   清空或改写。

### B. 定向服务器矩阵

使用本轮同一 10 个真实多轮输入，单并发、每 case 每 variant 30 次、Vanilla/DTB 交替；补一个明确在
tool argument 中包含字面 `</think>` 的最小 canary，以及完整 single-call/two-call burst canary。
分别执行：

- `stream=true`，`stream_interval=1/3/8/20`；
- `stream=false`，验证原生完整响应 parser；
- DTB 固定参数保持本轮值，Vanilla 保持原生路径；请求采样、seed、到达节奏和 cache 策略相同。

仅对这些 request ID 开 `stream_audit`，每个失败样本保存 parser 前 raw、forwarded text/token IDs、
source/forwarded finish/stop、completion epoch 和 terminal origin。不要把 telemetry run 用作性能排名。

### C. 双层裁决与准入

协议层对两个 variant 分别要求：request error、invalid JSON、schema failure、XML leak、index 不连续、
tool 后 content、terminal 缺失/重复、terminal 后 chunk、raw-complete 但 parser 丢调用全部为 0；DTB
还要求 final-answer `source_stop_reason=</think>` 为 0，单 completion、单 source terminal、无 synthetic
terminal 和 passthrough violation 为 100%。

语义层不再要求每个上下文只能调用一个固定工具。每个 case 配置 `allowed_next_tools` 或可审计状态转换；
`bash→write`、`todowrite→bash` 等合法前置步骤单列，不计协议失败。报告每对的 tool-call presence、首个
工具、完整名称序列和 schema-valid arguments；确定性配置要求 paired outcome 完全一致，生产采样配置
则报告每类转移和 McNemar/置信区间，不以一次随机不同判 parser 回归。

只有上述可用性门通过，才关闭 `stream_audit`，在同服务身份下另跑 `observability=off` 的 9 场景
Vanilla/DTB 单并发性能实验。该性能 run 再做 E2E、completion token、质量与 DTB 隐藏工作量配对比较。
