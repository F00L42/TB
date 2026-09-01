# Tool-call run04 paired replay：部署修复后结果

## 1. 结论

本轮应判为：**DTB 主故障已修复；“不给 Vanilla 添乱”的门仍未关闭；暂不进入 LightCode
时延排名。**

- **实测**：DTB 的 `invalid_tool_arguments_json` 从 `run_server_01` 的 133/300 降为
  0/300。144 个 DTB final-answer 请求的 parser 前 raw 中共出现 442 次字面量
  `</think>`，但没有一个 final child 再以 `</think>` 作为 source stop，直接命中了本次
  `final_answer` stop 修复的正向 canary。
- **实测**：本轮仍有 3 个协议 hard failure：Vanilla 2 个、DTB 1 个。两次 Vanilla 失败均为
  header 已发出但 arguments 为空；现有 audit 只覆盖 DTB controller，没有保存 Vanilla 的
  parser 前 raw，因此不能区分“模型/XML 本来就未闭合”和“新 parser 造成回归”。
- **实测**：Vanilla hard failure 从上一轮 0/300 变为 2/300。单侧 Fisher exact
  `p=0.2496`，没有统计证据证明 patch 伤害 Vanilla；但 300 个样本也不足以证明非劣，且缺少
  相同 raw 的 parser A/B，不能把“未检出显著差异”写成“没有回归”。
- **实验声明 + 身份记录**：本轮部署目标是完整 burst-drain patch；但 79,912 个 DTB
  final-answer delta 中没有命中本 patch 新增的目标 burst 形状。因此本轮不是
  #39598/#43074/#45439/#45440 分支的服务器覆盖证据，且缺实际 import path/source SHA 使完整
  部署身份门仍未闭合。
- **实测但不用于排名**：255 个 fully-clean pair 中，DTB 相对 Vanilla 的配对中位 E2E
  **增加 552.505 ms**，中位 speedup 为 **-8.216%**，DTB 只在 99/255 个 pair 更快。
  本轮是 `stream_audit` 可靠性实验，且 45 个 pair 被协议或语义门排除，不能作为正式性能结论；
  它只说明当前固定参数没有显示出值得提前跳过可靠性门的时延信号。

下一轮的第一优先级不是扩大 LightCode 场景，而是保持生产默认 `stream_interval=1`，做一轮很小但有
因果裁决力的 **Vanilla parser A/B**，同时定向复现唯一 DTB completion 生命周期失败。

## 2. 输入、身份与证据边界

### 2.1 实验输入

| 项 | 值 |
| --- | --- |
| 输入 | `input/cases.jsonl`，line 1～10 |
| 输入 SHA256 | `8667912b8a5b525c448cd8822281e7aa044df696cedbc2b16b42a46589e66e90` |
| 重复 | 每个 case 30 次 |
| 变体 | Vanilla / DTB，alternating |
| 请求数 | 600（300 pair） |
| 并发 | replay 单请求顺序执行 |
| 接口 | streaming chat completions |
| DTB 固定参数 | budget 20000；check 850；stride 500；num-thinking 5；min-think 1；prob 0.93 |

manifest 没有覆盖 temperature、top-p、seed、MTP 配置、启动命令和 cache 状态。服务采用生产默认
`stream_interval=1`，后续不把它作为实验变量。本轮可做同一服务进程内的协议对照，但不能把跨 run 的
随机差异归因为 patch。

### 2.2 部署身份

| 组件 | 实测身份 |
| --- | --- |
| TB commit | `8f4722e273bbec7115b9d5d8c438d2937b6a6180` |
| vLLM commit | `0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665`，exact tag `v0.23.0` |
| vLLM-Ascend | package `0.23.0`；checkout commit 未记录 |
| torch / torch-npu | `2.10.0+cpu` / `2.10.0.post4`（按环境包元数据） |
| DTB serving patch SHA256 | `bfb4660b12ee4d5a2e6b3d5ee35973d217a3faa3a3978a429d62756a9b98dbfa` |
| Qwen parser patch SHA256 | `f8405cdfa04d69e942ac77fdb21f93a5d94a0c749d471ed03f9c921b2336dad2` |
| budget YAML SHA256 | `2b7299cef967065fc4b178f4c53e065172931f2e3ca276eba685e3a9d7bcd2eb` |

`deployment_identity.txt` 证明了目标 patch 文件和目标 vLLM checkout 的身份，也列出了预期的三个
dirty 文件；但没有记录运行时实际 import path、应用后 parser 源文件 SHA 和加载类。所以下一轮仍需
补齐“patch 文件存在”到“服务实际加载该源码”的最后一段证据。

目录还缺少 metrics 前后快照和 phase 账本；结合 `stream_audit` 模式，本轮不能闭合服务负载、模型
总工作量或正式时延归因。

### 2.3 分片日志关联

分析器按顺序读取 `server_filtered_part_01`～`server_filtered_part_07`，再按每个 request 的
`sequence` 合并：

- client summary：600/600 请求；
- `DTB_STREAM_AUDIT`：86,458 条有效记录；
- 不可解析 audit 行：1 条；
- DTB server trace 覆盖：300/300；
- 每个 DTB request 的记录数：20～2,285；
- 缺失 DTB server trace：0。

分析器现支持一个或多个 server log：

```text
python tools/dtb/analyze_tool_call_run.py \
  docs/log/perf/agent_tool_call_run04_paired_replay/run_server_02 \
  docs/log/perf/agent_tool_call_run04_paired_replay/run_server_02/server_filtered_part_01 \
  ... \
  docs/log/perf/agent_tool_call_run04_paired_replay/run_server_02/server_filtered_part_07 \
  --output <temporary-analysis.json>
```

完整 analysis 会重复大量 raw delta，约 46 MB，因此没有再复制进 run 目录；原始分片、客户端
`summary.jsonl` / `chunks.jsonl` 和本报告保留可复核证据。

## 3. 客户端结果

### 3.1 协议、语义与时延

| Variant | Requests | Protocol pass | Protocol hard failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DTB | 300 | 299 | 1 | 26 | 26 | 10,627.631 | 28,358.822 | 663.716 |
| Vanilla | 300 | 298 | 2 | 32 | 34 | 9,869.376 | 28,343.337 | 578.016 |

`Overall failure` 是协议与语义异常的并集，不是两列相加。DTB 唯一协议失败也属于
`expected_tool_call_missing`，因此 DTB 并集仍为 26。

配对层：

- all pairs：300；
- protocol-clean pairs：297；
- fully-clean pairs：255；
- fully-clean pair 的 DTB - Vanilla 中位差：`+552.505 ms`；
- fully-clean pair 的 DTB 中位 speedup：`-8.216%`；
- DTB 更快：99/255；
- `latency_ranking_valid=false`。

### 3.2 三个协议 hard failure

| 请求 | 变体 | 客户端现象 | 服务器证据 | 当前裁决 |
| --- | --- | --- | --- | --- |
| `dtbtool-l5-a18-vanilla-run04-p0` | Vanilla | `task` header 已发出，arguments=`""`，随后 `finish_reason=tool_calls` | 没有 Vanilla parser 前 raw audit | 未定责：可能是模型/XML 未闭合，也可能是 parser 回归 |
| `dtbtool-l10-a12-vanilla-run04-p0` | Vanilla | `write` header 已发出，arguments=`""`，随后 `finish_reason=tool_calls` | 没有 Vanilla parser 前 raw audit | 未定责：同上 |
| `dtbtool-l10-a11-dtb-run04-p0` | DTB | HTTP 200，只有 initial-think delta；没有 usage、finish reason 或 terminal | 一个 completion epoch；initial-think 中途结束后直接 `controller_end`，没有 final state/source terminal | DTB orchestration/completion 生命周期失败；过滤日志缺 traceback，根因尚未闭合 |

两次 Vanilla 失败都不是“完整 arguments 少一个 `}`”这一已知形状，而是只出现工具名、完全没有
arguments delta。当前新 parser 只有在累计文本里找到完整 `</function>` 后才发送 canonical arguments；
这个客户端形状与 parser 前 XML 本来未闭合相容，但没有 raw 就不能据此免责。

DTB 失败请求的最后一条思考正文停在句中，随后出现 `controller_end`。源码会在状态机异常时记录
`orchestration_error` 并写 traceback，但 `server_filtered_part_*` 没保留该日志。因此本轮只能定位到
completion 生命周期层，不能继续猜测具体异常类型。更重要的外部契约问题是：内部失败被表现为
HTTP 200 的静默无 terminal 流；下一轮即使仍允许模型/执行报错，也必须要求这类失败显式结束或返回错误。

### 3.3 语义偏差不是 parser hard failure

分析器把 JSON schema failure 从 SSE/parser failure 中独立出来后，诊断如下：

| 诊断 | DTB | Vanilla |
| --- | ---: | ---: |
| 协议与当前语义 oracle 均通过 | 274 | 266 |
| 期望工具名不匹配 | 24 | 31 |
| arguments JSON schema 不满足 | 1 | 1 |
| SSE/协议 hard failure | 0 | 2 |
| controller completion 生命周期失败 | 1 | 0 |

- DTB schema 样本 `l5-a9`：`task` 缺 required `subagent_type`；raw/JSON 完整，是模型语义输出问题。
- Vanilla schema 样本 `l9-a19`：第二个 `write` 缺 required `filePath`；同样不是 parser 截断。
- 15 个 pair 两侧都有语义偏差，15/15 的语义标签一致。
- 当前 `expected_tool_names` 是单一精确名称，例如要求 `write` 前不允许先调用 `bash`；这会把合理的
  agent 工具选择计为偏差。下一轮应改成 `allowed_next_tools` 或把工具名选择作为独立模型行为指标，
  不让它污染 parser hard-failure 门。

## 4. 与修复前 run_server_01 的对照

两轮输入、case 和每 case 重复数相同，但不是相同生成 raw，也没有固定 sampling/seed，因此下表只能
说明观察到的故障分布变化，不能单独提供 parser patch 的 Vanilla 因果效应。

| 指标 | run_server_01 | run_server_02 | 变化 |
| --- | ---: | ---: | ---: |
| DTB hard-failure response | 134 | 1 | -133 |
| Vanilla hard-failure response | 0 | 2 | +2 |
| DTB invalid arguments JSON | 133 | 0 | -133 |
| DTB missing terminal | 1 | 1 | 0 |
| DTB expected-name mismatch | 24 | 24 | 0 |
| Vanilla expected-name mismatch | 29 | 31 | +2 |
| Vanilla invalid arguments JSON | 0 | 2 | +2 |
| DTB semantic response | 33 | 26 | -7 |
| Vanilla semantic response | 30 | 32 | +2 |

### 4.1 DTB 主根因修复证据

`run_server_01` 的 133 个 DTB invalid JSON 中，132 个 raw XML 本来就不完整；其中 131 个 final child
错误地以 `</think>` stop 截断。run_server_02 中：

- DTB invalid JSON：0；
- final-answer `source_stop_reason=</think>`：0；
- 144 个 final-answer raw 含字面量 `</think>`，共 442 次，仍能继续完成；
- `completion_epoch_one`：300/300；
- `no_synthetic_terminal`、`no_terminal_passthrough_violation`、
  `no_fresh_final_after_tool_boundary`、`no_thinking_or_probe_after_tool_boundary`：均 300/300；
- `final_source_terminal_one`、`final_terminal_passthrough_one`：299/300，唯一缺口就是已列出的
  lifecycle failure。

这是“final-answer 不再继承控制器 `</think>` stop”在目标服务上的直接实测闭环。

### 4.2 “不给 Vanilla 添乱”尚未闭环

run_server_01 的 Vanilla 是 0/300，run_server_02 是 2/300。以两轮为独立二项样本作条件比较：

- 单侧 Fisher exact（新版本更差）：`p=0.2496`；
- 双侧 Fisher exact：约 `p=0.4992`；
- run_server_02 Vanilla hard-failure rate 95% Wilson 区间：约 `0.183%～2.398%`。

这些数值表示“现有样本没有检出显著伤害”，不表示“证明没有伤害”。尤其 parser patch 是 server-wide，
即使 DTB disabled 真正绕过 controller，Vanilla 仍必然经过新 parser。要完成因果裁决，必须让旧、新 parser
消费相同的 parser 前 raw 与 delta 分片，或至少进行预声明 margin 和样本量的服务级非劣实验。

## 5. burst-drain patch 的服务器覆盖缺口

对 DTB final-answer raw delta 做结构邻接扫描：

| 目标 burst 形状 | 本轮命中 |
| --- | ---: |
| 完整 `<tool_call>...</tool_call>` 在一个 delta | 0 |
| 最后 parameter 与 `</function>` 同 delta | 0 |
| 第一个 call close 与第二个完整 call 同 delta | 0 |
| 完整 function body/header/arguments/close 同 delta | 0 |

共扫描 79,912 个 final-answer delta，单 delta 最大 10 个 token ID；52,623 个 delta 恰为 5 个 token ID。
这说明默认 `stream_interval=1` 下目标运行时仍以很细的粒度向 parser 交付增量。本轮“没有再缺 `}`”
与新 patch 正确相容，但新增 drain 分支没有被执行，不能作为其服务器命中证据。后续不修改 interval
来人为制造分片：上游 burst 形状继续由确定性 parser fixture 覆盖，服务器实验只验证生产默认配置下
不新增 Vanilla 故障。

因此当前 parser 证据是：

1. **实验声明 + 身份记录**：目标 vLLM checkout 有预期修改和 patch SHA，但实际加载类/source SHA
   未记录，严格部署身份门未关闭；
2. **实测**：细粒度真实流没有出现 DTB JSON 截断；
3. **无模型实测**：离线 fixture 覆盖五个上游缺陷族并与非流式 oracle 等价；
4. **尚未实测**：Vanilla parser 前 raw 和目标服务非流式 serving；
5. **不能推出**：两次 Vanilla 空 arguments 与 patch 无关。

## 6. 下一轮实验方案

目标口径是“新 parser/DTB 不新增 Vanilla 故障”，不是要求模型永远生成有效工具调用。下一轮按以下
顺序执行；A、B 通过前不启动 9 场景 LightCode 性能轮。

### A. 相同 raw 的 Vanilla parser 因果 A/B（最高优先级）

先补 parser 层 allowlist audit，使 Vanilla 和 DTB 都能保存：累计 parser 输入 text/token IDs、每次
delta 分区、parser 输出的规范化 tool calls、finished/finish reason。只对白名单请求开启，避免全量正文日志。

使用两个固定服务身份：

1. vLLM v0.23.0 原生 parser 基线；若修复前生产基线还包含 parameter-close 最小 patch，则把它作为
   第三个明示 cell，不得用身份不明的 run_server_01 服务代替；
2. 完全相同的模型、serving 和 DTB 配置 + 当前 burst-drain patch。

DTB 在这一步关闭，只跑 Vanilla。优先把一次服务生成捕获的相同 logical raw 和 delta partition 离线送入
两个 parser；这样模型随机性不会污染 parser 因果比较。至少包含：

- 本轮两个 Vanilla 失败 prompt：`l5-a18`、`l10-a12`；
- #39598、#43074、#45256、#45439、#45440 的精确 complete-raw fixture；
- single-call、multi-call、partial delimiter、malformed/incomplete XML；
- 普通 content、reasoning + content、stream terminal 与 `length` terminal。

通过条件：

- 对每个**完整 raw**，新 parser 的 tool-call 数量/顺序、函数名、arguments 语义、content 和 finish
  必须等于非流式 oracle；`new_failure - baseline_failure = 0`；
- 新 parser 不得把基线可解析的相同 raw 变成 hard failure；
- 对**不完整 raw**不要求 parser 人工补齐，只记录两版行为并把故障归到模型/截断层；
- 两个 Vanilla 失败样本必须获得 parser 前 raw，不能再保留“header-only、无 raw”的未定责状态。

若还要用独立服务生成率做非劣判断，先预声明允许的 hard-failure rate margin（建议从 1 个百分点开始评审）
并按基线率和 power 计算样本量；不要把另一次 300 请求零显著性检验当成通过门。

#### 定向数据和调用参数

失败 attempt 不是独立输入。本轮三个异常只对应两个源请求体：

- `input/cases.jsonl` 第 5 行：`l5-a18-vanilla` empty arguments；
- `input/cases.jsonl` 第 10 行：`l10-a12-vanilla` empty arguments 和 `l10-a11-dtb` silent no-terminal。

回放器使用原始大 JSONL 加 `--lines 5,10` 直接形成定向数据集，不再复制两个超大请求体。先生成
Vanilla/DTB parser audit allowlist：

```bash
INPUT=docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl

python tools/dtb/replay_tool_calls.py \
  --file "$INPUT" \
  --lines 5,10 \
  --mode both \
  --repetitions 30 \
  --request-id-run run05-targeted \
  --list-request-ids
```

确定性 Vanilla parser A/B 在两个服务身份上分别运行同一命令，只改变 `--url` 和 `--output-dir`：

```bash
python tools/dtb/replay_tool_calls.py \
  --file "$INPUT" \
  --lines 5,10 \
  --mode vanilla \
  --repetitions 30 \
  --request-id-run run05-vanilla-parser-ab \
  --stream \
  --temperature 0 \
  --top-p 1 \
  --seed 42 \
  --url http://127.0.0.1:9000/v1/chat/completions \
  --output-dir <baseline-or-candidate-output>
```

`--temperature 0 --top-p 1 --seed 42` 必须在两个 cell 完全一致。它减少生成差异，但 HTTP 生成仍不保证
parser 前 raw 逐字相同；严格因果裁决仍以 allowlist audit 捕获的相同 raw/delta 离线送入两版 parser
为准。若目标是复现原来的生产随机故障而不是 A/B，省略这三个 override，回放器会保留归档请求参数。

### B. 生产默认配置服务器集成

服务器保持默认 `stream_interval=1`，不增加 interval 参数、不为此重启多个服务。主回放使用
`stream=true`；另用同一服务身份执行一次 `--no-stream` 协议 smoke，验证完整响应 parser。Vanilla 和
DTB 的 parser-before/parser-after audit 仅对白名单 request ID 开启。

通过条件：相同完整 raw 上的新 parser 不新增 missing call、invalid JSON、XML 泄漏、index 不连续、
错误 finish reason 或 terminal 后 chunk；非流式结果与流式重组语义一致。上游 burst 形状由已经存在的
#39598/#43074/#45256/#45439/#45440 精确 fixture 和随机分区属性测试裁决，不属于服务器 interval 实验。

### C. DTB lifecycle 定向回放

使用同一源数据第 10 行，以 `--lines 10 --mode both --repetitions 30` 回放，同时保留：

- 未过滤的异常 traceback；
- `orchestration_error` 的 error type、stage 和 trace/request ID；
- source child terminal、controller state transition 和客户端最后事件；
- 同 prompt 的 Vanilla 对照。

通过条件不是“内部永不报错”，而是：不得再出现 HTTP 200 后静默无 terminal；成功请求严格一个 source
terminal，失败请求返回明确 SSE/API error，且 controller 不在 tool boundary 后启动新的 thinking/probe。

### D. 扩大 paired reliability

A～C 通过后，再跑当前 10 case 的 paired reliability：

- 单并发、相同 sampling/seed、相同 cache/到达顺序；
- 记录实际 server flags、parser import path/source SHA、MTP，并确认使用默认 `stream_interval=1`；
- `expected_tool_names` 改为 `allowed_next_tools`，工具选择和协议正确性分别统计；
- 主门为 parser/controller **新增的差分 hard failure 为 0**；模型 no-tool、schema 错误、无效 raw 单列，
  不伪装成 parser failure，也不要求绝对错误数为 0。

### E. LightCode 正确率与时延

只有 A～D 关闭可靠性门后，才回到单并发 9 场景：

1. 先以相同样本验证 Vanilla/DTB 完整任务率和 LightCode Judge；
2. 正式性能 run 使用 `OBSERVABILITY.mode=off`，另开 `phase` 账本 run；
3. 请求级配对报告 E2E、P50/P95、质量转移、visible/hidden/final token 和 phase wall；
4. 若当前固定参数仍像本轮一样整体变慢，再调整“短程自由思考、只压长尾”的路由/参数，而不是通过
   接受工具协议失败换取表面时延。

## 7. 下一轮最小交付物

- 两套（若保留 minimal-patch 则三套）服务的完整 deployment identity：vLLM/vLLM-Ascend commit、
  启动参数、实际 parser import path、应用后 source SHA、patch SHA、MTP，并确认默认 interval；
- Vanilla/DTB parser-before 与 parser-after allowlist audit；
- 相同 raw 的旧/新 parser A/B 表，以及 complete/incomplete raw 分层；
- 五个上游 burst fixture 的离线 A/B 结果和 source hash；
- `l5-a18`、`l10-a12`、`l10-a11` 三个 request family 的定责记录；
- 客户端 summary/chunks、分片 audit、不可解析行计数、聚合 `RESULT.md`；
- 明确门结论：`Vanilla no-harm PASS/FAIL/INCONCLUSIVE`，不得用总成功率平均值替代。

本轮的最终门状态为：

```text
DTB erroneous </think> final stop       PASS
DTB JSON truncation regression          PASS on this replay
DTB completion lifecycle                NOT PASS (1 silent no-terminal response)
Vanilla parser no-harm                  INCONCLUSIVE (2 failures, no pre-parser raw)
v0.23 burst-drain offline fixtures      PASS
default-config server integration       NOT YET
LightCode latency experiment admission  NOT YET
```
