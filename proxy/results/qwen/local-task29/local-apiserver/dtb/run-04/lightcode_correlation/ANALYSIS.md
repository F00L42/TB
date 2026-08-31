# run-04 LightCode 关联分析

## 结论

本轮关联结果可用于逐任务排障：9/9 个 LightCode task、22/22 个已启动 agent、
25/26 个 proxy session 和 273/274 个请求已映射，唯一未映射请求是独立的会话标题生成，
没有任务歧义。完整关联账本见 [SUMMARY.md](SUMMARY.md)。

当前目标尚未达成：

1. **工具可靠性门未通过。** 372 个结构化工具调用中有 3 个 arguments JSON 截断，另有
   2 个 JSON 合法但不满足工具 schema 的调用；两个任务又在明确准备继续调用工具时以
   `stop` 提前结束，LightCode API 仍标记 `FINISHED`，但没有生成评测报告。
2. **有显著时延改善信号，但尚不能归因为 DTB。** 9 个同用例配对的 LightCode 活跃 E2E
   总和下降 73.19%；排除无报告任务后下降 71.06%，4 个两轮均得到优秀报告的任务下降
   66.72%。但是 proxy 可见 completion token 只下降 22.38%，LLM E2E 总和却下降 82.83%；
   run-03/run-04 又缺少统一实验 manifest 和服务器 phase 账本，不能区分 DTB 收益、服务状态
   差异、隐藏 probe 工作量和执行路径变化。
3. **报告层质量已退化。** Vanilla 为 7 个优秀、2 个不合格、0 个缺失；run-04 为
   4 个优秀、3 个不合格、2 个缺失。这里是评测报告结果，不等价于额外的自动 Judge 正确率。

因此，run-04 可以支持“DTB 值得继续做受控时延实验”，不能支持“质量不变且 DTB 已证明提速”。

## 输入、口径与证据边界

- DTB：`proxy/.../dtb/run-04` 与 `lightcode/session_logs_0829_dtb_v2`，仓库提交
  `99597cd6fbc31538dd34a231c316fe877c23a392`。
- Vanilla：`proxy/.../vanilla/run-03` 与 `lightcode/session_logs_0827_vanilla`。
- 两轮均为相同 9 个 case；LightCode 按用户确认采用单并发。
- 两轮 chunk 中的模型名均为 `qwen3.6-27b`，`system_fingerprint` 均为
  `vllm-0.23.0-tp8-f063d18e`。
- run-04 的 274/274 请求均注入固定配置：`budget=20000`、`check_length=850`、
  `think_stride=500`、`num_thinking=5`、`min_think_tokens=1`、`prob_th=0.93`。
- LightCode 活跃 E2E 定义为主 session 第一条消息到 `evaluation_results.json.end_time`；
  它包含 agent、工具、环境和报告交接时间。proxy LLM E2E 是请求等待时间，两者不能相加。
- run-04 没有服务器 phase/stream-audit 和完整实验 manifest。proxy completion usage 不能闭合
  hidden confidence probe；服务器 patch 路径、源码 hash、cache、seed、启动参数和运行时负载也
  无法从本目录审计。

## 工具调用可靠性

### 结构化输出审计

对 274 条完整 proxy 记录重建了 247 个 tool-call response、372 个 tool call：

| 类型 | 数量 | 影响 |
| --- | ---: | --- |
| arguments 不是合法 JSON | 3 | LightCode 将工具记为 `invalid`，连续重试 |
| arguments 缺少 required 字段 | 1 | `todowrite` 执行前 schema error |
| arguments 字段类型错误 | 1 | `todos` 应为 array，实际为 string |
| XML 泄漏到 content | 0 | 本轮客户端日志未观察到历史 XML 泄漏形态 |
| tool call 重建失败或空函数名 | 0 | proxy 分片与重建路径正常 |

3 个 JSON 截断来自同一任务 `task_f637076490574c2a_r1` 的三个独立 completion：

| Request | Proxy 证据 | LightCode 结果 |
| --- | --- | --- |
| `02e78969b36f45a2` | `full_log/proxy_session_05e43e4e79b9/part-00001.jsonl:3`，arguments 终止于 `{"description": "AI generate fixed code"` | `call_18233e4edbbf4c60a823e031` 被记为 Invalid Tool |
| `73dde28da2544e49` | 同 shard 第 4 行，arguments 终止于 description | `call_ff9dfe1bb6654613a3657cd9` 被记为 Invalid Tool |
| `d9389420a6c94b84` | 同 shard 第 5 行，arguments 终止于 description | `call_603a2ef65b0d429b962e7578` 被记为 Invalid Tool |

三次失败直接浪费 18.187 秒 LLM E2E 和 1,483 个 response completion token；第四次重试才
生成合法 `task` 调用。LightCode 的 JSON parse error 见对应 session 第 967、1072、1177 行。

两个 schema error 位于 `task_64337995e17b4d94_r1`：

- `10a82e0ed5d242a4`：`todos` 为字符串而不是数组，proxy shard 第 18 行；
- `db7ba169a3db4760`：缺少 required `todos`，proxy shard 第 22 行。

LightCode 在 session 第 4807 和 5461 行明确记录了 `SchemaError`。该任务随后恢复并得到优秀报告，
所以这两次属于可恢复但增加重试和时延的工具语义故障。

### 两个无报告任务

`evaluation_results.json` 将全部 9 个任务标为 `FINISHED`，但以下两个任务没有任何报告制品：

1. `task_90079c04aee24ea6_r1` 在 clone 完成后明确推理“Now checkout to the base commit”，
   最后一条响应却以 38 completion token、`finish_reason=stop` 结束，content 截断在
   ``checkout to the base commit `0072``。证据为
   request `af590ee7efa14439`、`full_log/proxy_session_edd49453530f/part-00001.jsonl:6` 和
   LightCode session 第 471 行。
   它没有启动任何子 agent，也没有进入实际评测。
2. `task_f637076490574c2a_r1` 已完成生成和编译步骤，最后明确推理“generate the evaluation
   reports”，响应却仅输出 `##` 后以 18 completion token、`finish_reason=stop` 结束。证据为
   request `1e1d5c135dfb473f`、`full_log/proxy_session_ce2a47dd4bed/part-00001.jsonl:9` 和
   LightCode session 第 1973 行。

两条响应都没有 XML 泄漏，也没有结构化 tool call。proxy 只能证明“客户端最终收到 stop 且
没有工具调用”，不能判断 parser 前 raw 是否本来没有 `<tool_call>`。因此：

- **实测：** 它们是评测中断的直接原因，平台 `FINISHED` 不能作为成功信号；
- **假设：** `min_think_tokens=1` 使短请求也进入 DTB finalization/answer-cue 路径，可能改变
  原本应继续调用工具的行为；
- **尚不能定责：** 缺 stream-audit，不能在模型选择、DTB finalization 与 parser 丢失之间裁决。

## 报告结果转移

| Case | Vanilla | run-04 | 裁决 |
| --- | --- | --- | --- |
| 29 ExperienceAssurance | 优秀 | 优秀 | 保持 |
| 2 DataSmartCube | 优秀 | 优秀 | 保持；run-04 有 2 次可恢复 schema error |
| 34 DVAIAgent | 优秀 | 优秀 | 保持 |
| 37 DVAIAgent | 优秀 | 优秀 | 保持 |
| 26 ExperienceAssurance | 不合格 | 不合格 | 保持失败，具体生成错误不同 |
| 3 DataSmartCube | 不合格 | 不合格 | 保持失败 |
| 28 ExperienceAssurance | 优秀 | 不合格 | run-04 报告称 base commit 预存编译问题，不能直接归因 AI 代码 |
| 35 DVAIAgent | 优秀 | 无报告 | 提前 stop，hard failure |
| 36 DVAIAgent | 优秀 | 无报告 | 工具异常重试后在报告写入前 stop，hard failure |

即使把 case 28 按报告中的“预存问题”单独人工复核，两个 pass → no-report 转移仍足以判定当前
质量/完整性门未通过。

## 端到端时延

### 同 case 配对

| Case | Vanilla active E2E (s) | run-04 active E2E (s) | 变化 | run-04 报告 |
| --- | ---: | ---: | ---: | --- |
| 26 ExperienceAssurance | 1839.160 | 414.371 | -77.47% | 不合格 |
| 28 ExperienceAssurance | 1978.927 | 418.439 | -78.86% | 不合格/预存问题 |
| 29 ExperienceAssurance | 1688.163 | 540.363 | -67.99% | 优秀 |
| 2 DataSmartCube | 1978.373 | 557.411 | -71.82% | 优秀 |
| 34 DVAIAgent | 1290.742 | 514.753 | -60.12% | 优秀 |
| 35 DVAIAgent | 2176.007 | 99.155 | -95.44% | 无报告，不能计作有效提速 |
| 36 DVAIAgent | 961.306 | 473.234 | -50.77% | 无报告，不能计作有效提速 |
| 37 DVAIAgent | 2007.569 | 705.397 | -64.86% | 优秀 |
| 3 DataSmartCube | 1838.681 | 501.527 | -72.72% | 不合格 |

| 集合 | N | Vanilla 总时长 (s) | run-04 总时长 (s) | 加权变化 | 更快任务 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 全部任务 | 9 | 15758.928 | 4224.650 | -73.19% | 9/9 |
| run-04 有报告 | 7 | 12621.615 | 3652.261 | -71.06% | 7/7 |
| 两轮均为优秀 | 4 | 6964.847 | 2317.924 | -66.72% | 4/4 |

“两轮均为优秀”是目前最可信的方向性信号；“全部任务”包含提前终止，不能作为主结论。

### proxy 全局工作量

run-03 缺少可做精确 task 关联的完整 request body，因此这里只比较整个 9-task run，不把
Vanilla proxy session 启发式归到单个 task。

| 指标 | Vanilla run-03 | DTB run-04 | 变化 |
| --- | ---: | ---: | ---: |
| 已启动 LightCode agent | 27 | 22 | -18.52% |
| proxy 请求 | 369 | 274 | -25.75% |
| prompt token 总量 | 29,174,343 | 20,512,519 | -29.69% |
| response completion token | 239,629 | 186,001 | -22.38% |
| proxy LLM E2E 总和 (s) | 12,712.288 | 2,183.014 | -82.83% |
| 请求 E2E P50 (s) | 13.557 | 4.070 | -69.98% |
| 请求 E2E P95 (s) | 123.155 | 28.407 | -76.93% |

请求数和可见 token 减少支持 DTB/轨迹缩短确实贡献了收益；但 LLM E2E 的降幅远大于工作量
降幅，说明当前数据还混有无法解释的服务速率或隐藏工作量差异。没有 phase 账本时，不能用
response completion token 代替总生成量，也不能从这两个跨日 run 得到 DTB 的净时延因果量。

## 下一步实验

### P0：先关闭工具可靠性门

1. 从上述 7 个 request ID 提取 wire-exact 请求：3 个 malformed JSON、2 个 schema error、
   2 个应继续工具调用却 stop 的任务末请求。
2. Vanilla/DTB 交替顺序 paired replay，每类至少 30 次；保持原 `max_tokens=32000`、工具 schema、
   sampling 和完整多轮 messages。不要只统计“是否出现任意 tool call”。
3. 对 DTB request ID 开启精确 allowlist `stream_audit`，记录 parser 前 raw、forwarded token/text、
   source/forwarded finish reason；manifest 固定 vLLM/vLLM-Ascend、模型、DTB/parser patch hash 和
   实际 import path。
4. analyzer 增加两项门禁：按 request `tools` 校验 arguments required/type；对“推理声明下一步
   调工具但 stop 且输出不完整”单列 `expected_action_missing`，不能把它当普通 no-tool。
5. 准入标准：tool-call response 的 JSON、schema、XML、index、finish reason hard failure 为 0；
   9-task smoke 必须 9/9 产出报告。未通过前不做参数排名。

### P1：再裁决 DTB 净时延

1. 用同一服务器进程、同一模型和 patch，按 case 交替或 ABBA 运行 native Vanilla 与 DTB；
   每个 task 仍保持单并发，记录统一 manifest、seed/sampling、cache 状态和到达节奏。
2. 正式性能排名使用 `OBSERVABILITY.mode=off`；另做一轮 `phase` 账本实验闭合 visible think、
   hidden probe、final answer、queue/prefill/decode/controller CPU。两类 run 不混用基线。
3. 主指标为“成功产出且质量不退化任务”的 paired active E2E；同时报告全部任务 hard failure、
   请求数、agent 数、P50/P95 和更快任务数。提前结束不得计为提速。
4. 先复测当前 `moderate` 配置；通过后再加入“long-only DTB”候选：短思维链走原生路径，只有
   超过阈值的长思考进入 DTB/answer cue。这样可直接验证短链 answer cue 是否带来不必要开销或
   工具行为偏移，而不是立即扩大固定参数网格。

当前推荐决策是：**保留 DTB 的时延优化方向，暂停参数搜索；先完成 7 个故障请求的带
stream-audit paired replay，并把 9/9 报告完整性恢复为硬门。**
