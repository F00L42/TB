# Agent run-06：真实介入、任务质量与时延分析

分析日期：2026-09-10。目标：短请求原生旁路，长请求压缩，在不降低任务正确性的前提下改善 Agent 端到端时延。

## 1. 结论与本轮决策

1. **实测：13 次真实 abort→probe 均完成，未在已保存的对外输出中发现 G0 式 prompt/H 回显。** 343 个 DTB 请求均有正常、唯一的外部 terminal；工具参数 JSON/schema、结构标记泄漏等现有离线审计未报异常。330/343（96.21%）请求同一 native child 完成，没有 probe 或 final 子请求，短请求二次提交已被消除。这是实际链路的正向证据，不是任意切点 decoder 安全证明。
2. **实测：尚未证明“评测都正确时端到端加速”。** 严格按两侧编译与 DT 全通过筛选，仅 case **28、34** 同时满足；主 session 活动跨度合计 **893.356→942.881 秒，变慢 5.54%**；LLM 请求 E2E 合计 **569.335→600.921 秒，变慢 5.55%**。两项任务分别都变慢。
3. **质量未通过稳定性门。** DTB 9/9 产出报告，但 case 26、35、36 编译失败；Vanilla case 36 全通过而 DTB 失败。Vanilla case 35 原始测试记录为 36/36，但没有正式报告；DTB 该任务也编译失败。不能用 case 2、29 的改善抵消这些退化，也不能把“优秀（≥90%）”等同于全部测试正确。
4. **主要性能矛盾已经从短请求 handoff 转到长尾机会和介入有效性。** Vanilla 超过 850 token 的 11 次思考里，9 次是标题生成，只有 2 次来自主/子 Agent。DTB 13 次介入中 8 次是标题；24 次 probe 仅 2 次接受，隐藏试答 wall 达 **51.142 秒**，其中标题占 **42.632 秒**。标题思考减少 19.85%，但标题请求总时延反而增加 0.83%。

**下一步：保留当前实现作为诊断基线，暂不因本轮无回显而实施 H 文本重编码规避；先做分角色、同输入的有界配对回放，确认短旁路成本和长尾净收益，再以冻结评测流程、telemetry-off 的重复端到端实验验收。** 已知 G0 反例仍须保留为发布前的独立保真问题，不能写成已修复。

## 2. 输入、身份与证据边界

| 项目 | 本轮可确认的事实 |
| --- | --- |
| DTB 原始透传 | [dtb/run-06](../../../../proxy/results/qwen/local-task29/local-apiserver/dtb/run-06)：343 条 full-log；chunks；`server_filtered_part_01` |
| Vanilla 原始透传 | [vanilla/run-06](../../../../proxy/results/qwen/local-task29/local-apiserver/vanilla/run-06)：392 条 full-log；391 个请求有 chunks |
| 平台结果 | [DTB](../../../../lightcode/session_logs_dtb_06/evaluation_results.json)、[Vanilla](../../../../lightcode/session_logs_vanilla_06/evaluation_results.json)，各 9 个 task |
| 输入一致性 | case 2、3、26、28、29、34、35、36、37 的主 session 首条完整任务输入，逐 case 精确 SHA-256 相同 |
| 实际模型与请求 | 两侧 `model=qwen3.6-27b`、`stream=true`、`max_tokens=32000`、`top_p=1`、`include_usage=true`；未显式传 temperature、seed，不能反推服务默认值 |
| DTB 注入 | `fixed_budget_tokens=20000, fixed_check_length=850, fixed_think_stride=500, fixed_num_thinking=5, fixed_min_think_tokens=1, fixed_prob_th=0.93`，343 条一致 |
| 服务内身份 | phase schema 3、summary schema 1、`dtb-algorithm-v4`、`drain-prefix-v1`；DTB 明确开启 phase |
| 缺失身份 | 未提供部署 manifest，无法闭合 TB/vLLM/vLLM-Ascend commit、patch/YAML hash、模型权重、TP/DP、CANN/PyTorch/NPU、实际默认 sampling、cache 初始状态和 telemetry dropped 全局计数 |
| 并发 | 沿用用户明确的评测任务单并发；不将其误认为每次只有一个在途 HTTP 请求，标题等辅助请求可能与主流程重叠 |

这是 **phase-on 诊断性端到端观察**，不是正式 off 性能排名。虽然两侧 task 输入相同，多轮后续 prompt、工具动作、请求数量、编译/修复/测试轨迹并不相同；本轮也只有每 case 每 variant 一次，不能用均值差直接证明算法因果收益。

本地只读取既有日志、运行离线关联/协议审计与复算；没有执行模型、工具、编译或服务器实验。

### 2.1 时间口径

两份 `evaluation_results.json` 的 18 条任务均缺 `start_time`，只有 `end_time`。因此**无法复算用户定义的平台 `end_time-start_time` 总评测时延**，不以相邻 task 的 end、文件修改时间或下载时的 `updatedAt` 冒充 start。

本报告并列使用：

- **主 session 活动跨度**：首条主 session 消息 timestamp，至最后一条消息 timestamp/已记录顶层 part 的 `time.end` 中较晚者。覆盖可见的主流程、等待子任务和工具时间，是本轮最接近完整执行时延的代理指标，但不包含首消息前的环境启动等平台成本，不等于官方 task wall。
- **LLM E2E 合计**：该 task 关联到的全部主/子/辅助请求 `metrics.e2e_ms` 之和。反映累计请求耗时，**重叠请求仍分别累加，不等于任务关键路径时长**。
- `chunks.jsonl.timestamp` 是请求开始时间，不是每个 chunk 的到达时间；不能据此恢复逐 delta TTFT 或重启后的首字间隙。

收益率统一为 `(Vanilla-DTB)/Vanilla`，正值为改善。

## 3. 关联、完整性与协议审计

### 3.1 关联闭合

先运行现有关联器，保留原始结果不改写：

- [DTB 关联概览](dtb_correlation/SUMMARY.md)：初始 342/343 请求归属明确。
- [Vanilla 关联概览](vanilla_correlation/SUMMARY.md)：初始 377/392 请求归属明确。

本轮审计增加两类**显式、可重算、无时间猜测**的补充连接：

1. Vanilla case 35 会话压缩后的 14 个请求：以返回 tool-call ID 与官方主 session 的 callID 精确对应，或完整 assistant content/reasoning 精确 SHA-256 对应，全部唯一归属该 task。该 segment 首 user 已变为 `What did we do so far?`，旧 root 指纹规则不能直接覆盖。
2. 两侧各 1 个独立标题请求：第一 user 为固定标题生成指令，后续 user 的**完整内容**与主 task 首条输入精确相等；分别关联 Vanilla case 2、DTB case 36，角色仍为 `auxiliary`，不冒充主 Agent。

补充证据见 [Vanilla links](vanilla_supplemental_links.json)、[DTB links](dtb_supplemental_links.json)。最终 **392/392、343/343** 请求均有唯一 task；正式带补充归属的账本为 [Vanilla request index](vanilla_request_index.jsonl)、[DTB request index](dtb_request_index.jsonl)。原关联器的 SUMMARY 数字未被静默替换。

### 3.2 phase 闭合与协议结果

| 检查 | 结果 |
| --- | --- |
| DTB parent | 客户端 343、summary 343、phase parent 343；无缺失、重复或其他 parent |
| phase | 415 条 = 343 native + 13 decision + 24 probe + 22 stride + 13 final |
| 模型子调用 | 402 = 330 个同 child 旁路 + 13 个介入请求共 72 个子调用；逐 parent 与 summary 对齐 |
| 预算解析 | 13 个 triggered 各 1 个有效 decision；330 个旁路无 decision，`not_required_native` |
| 完成 | DTB 343/343 `completion_status=success`、error=null、外部 terminal 恰好一次 |
| 流式重建 | 对两侧逐请求重建 SSE，content/reasoning/usage 与 full-log 全量精确对账 |
| 工具协议 | DTB 无现有审计命中的异常；Vanilla 只有下述无 terminal 空请求；两侧工具 arguments JSON/schema 错误均为 0 |

Vanilla `7774ec46a9f245c9`（case 35，17:13:42.876898，688.133 ms）无 chunks、无正文、无 usage，审计标记 `missing_terminal`。后续请求和测试继续完成，**不是 DTB 回显或 DTB 新增协议异常**。原始 full-log 没有足够 HTTP/服务错误信息，无法确定是连接取消、平台重试还是日志缺口；不得把它诊断成 parser bug。它仍计入累计请求耗时，不因空记录而丢弃。

审计复用了 `tools/dtb/replay_tool_calls.py::assemble_observation`。传入的 HTTP 200 只是该离线 API 的占位输入，**不构成服务器实测 HTTP 状态**；没有为真实 Agent 强行指定预期工具名，合法工具调用的业务语义仍由任务日志裁决。

## 4. abort→probe 是否出现异常回显

### 4.1 真实触发证据

13 条均为 `abort_count=1`、`abort_terminal_seen=true`、`post_drain_branch=confidence_probe`；介入/重启前缀为 850–854 token，threshold overshoot 为 0–4。全部 `accepted_drain_tokens=0`，没有出现排空追加 boundary 或自然 terminal 抢先的分支。

下表 thinking 为服务 summary 的可见思考账本；“接受”是本次请求内 probe 接受数/总数。服务行号来自 DTB `server_filtered_part_01` 的 summary，完整 phase/request/source shard 索引见 [analysis_evidence.json](analysis_evidence.json) 的 `triggered`。

| case / 角色 | proxy request ID | thinking | hidden probe | 接受/次数 | 请求 E2E(s) | summary 行 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2 / 标题 | `738f6c58019a4ac5` | 2088 | 113 | 0/3 | 35.262 | 391 |
| 2 / 子 Agent | `b27de22508d844b8` | 1165 | 50 | 0/1 | 85.000 | 421 |
| 3 / 标题 | `4785d65c78974a9f` | 1662 | 100 | 0/2 | 28.472 | 461 |
| 3 / 主 Agent | `d8065be54645428e` | 1046 | 50 | 0/1 | 19.457 | 549 |
| 26 / 标题 | `e1060c99cb4a49ee` | 1355 | 100 | 0/2 | 23.955 | 75 |
| 26 / 子 Agent | `81765af65dfd402b` | 889 | 50 | 0/1 | 16.780 | 123 |
| 26 / 子 Agent | `3767e23cc5a84c95` | 860 | 50 | 0/1 | 18.723 | 129 |
| 28 / 标题 | `be84c56df2004586` | 1738 | 28 | 0/2 | 28.391 | 565 |
| 28 / 子 Agent | `0aa42c61e0a5411e` | 851 | 50 | 1/1 | 25.487 | 580 |
| 29 / 标题 | `62f66250101c4682` | 1350 | 68 | 1/2 | 24.259 | 9 |
| 34 / 标题 | `c383b943c8a74774` | 1908 | 27 | 0/3 | 31.308 | 321 |
| 35 / 标题 | `8c3e2ef9651b470e` | 1539 | 100 | 0/2 | 32.139 | 259 |
| 37 / 标题 | `03730f0a9b744377` | 1878 | 111 | 0/3 | 37.537 | 694 |

### 4.2 检查范围及实际文本

对全部请求的 reasoning、content、完整工具 arguments 检查本请求 system 文本前 256 字符的精确复现，以及 G0 标志 `system\n# Tools`、`<|im_start|>`、`assistant\n<think>`；两侧均无命中。同时核对 13 条介入输出的上下文和异常大 delta：

- case 2 子 Agent 在 `throwBadReqExp`、模板名称校验和国际化条目之间继续正常代码讨论，随后输出工具参数；没有重新输出系统工具说明。
- case 3 主 Agent 围绕 15 个测试、1 个失败分析，继而生成评测报告；没有返回历史完整 prompt。
- case 26 两条子 Agent 正在猜测 `RestCaller.Builder` API，输出中的 `.call()`/`.headers()` 是本次错误代码思路，**不是 prompt 回显**。
- case 28 接受 probe 的子 Agent 是 patch 信息提取，其后产出结构化信息；标题 case 29 在字符计数中被接受，然后正常产出标题。
- 标题中反复出现“已经选定标题、再次检查长度”的自然语言属于重复思考；不是 G0 中 decoder 重启释放整个 P/H 的证据。

最大的 DTB SSE 工具参数 delta 达 **98,571 字符**，来自 case 35 的 `b1a00908e22c4403`（`full_log/proxy_session_227b63ea0c82/part-00001.jsonl:3`）。解析后是 `write` 的完整 `IncidentManage.java` 内容（95,669 字符）；另一个 write 是 `DelayedIncidentScheduler.java`。本请求思考只有 138 token、同 child 旁路。**大 chunk 是完整文件写入，不是 abort 重启带出全部历史。**

证据限制：

- proxy chunks 是 **post-parser** 输出，没有保存重启处原始 pre-parser delta/token IDs，也没有隐藏 probe 全文。不能凭 phase 断言隐藏试答从未出现 decoder 文本异常。
- 上述检测对 G0 特征回显有针对性，不是任意局部重复的完备识别器，也没有重建每个 P/H/S 的固定流 oracle。
- 本轮未覆盖含排空尾、排空已有 boundary、自然完成抢先等路径。13 个零异常样本不代表异常概率为零。

因此结论是“**本轮真实介入未观察到影响外层 Agent 的回显**”，不是“G0 不真实/已修复”。按照[介入设计第 12 节](../../../design/dtb_minimum_intervention_threshold.md)保留候选 H 文本重编码方案，暂不新增短路径保存文本和重编码成本；若后续取得具体异常 parent，再按其实际分支选择修复。

## 5. 任务结果与失败根因

### 5.1 不使用报告评级代替全部测试通过

| case | Vanilla | DTB | 严格两侧正确？ |
| --- | --- | --- | --- |
| 2 | 编译失败 | 44/44 | 否，DTB 改善 |
| 3 | 14/15，评级优秀 | 14/15，评级优秀 | 否，两侧存在真实 SQL 语义缺陷 |
| 26 | 编译失败 | 编译失败 | 否 |
| 28 | 12/12 | 12/12 | **是**；DTB 额外处理预存编译问题及一次 task 调用错误 |
| 29 | 7/8 | 8/8 | 否，DTB 改善 |
| 34 | 5/5 | 5/5 | **是** |
| 35 | 无 JSON/MD 报告；原始 surefire 36/36 | 编译失败 | 否，质量与工作流分开计 |
| 36 | 4/4 | 编译失败 | 否，DTB 退化 |
| 37 | 1/3：1 failure、1 error | 2/3：1 error | 否；存在测试/基础设施裁决争议 |

DTB 正式报告中严格全通过为 **4/9**（2、28、29、34）；Vanilla 正式报告严格全通过 **3/9**（28、34、36），另 case 35 有原始 36/36 通过证据、但缺正式报告。若只统计已观察的编译+测试通过而暂不要求报告交付，Vanilla 为 4/9，DTB 为 4/9；**总数相同也不是逐任务非劣**。本报告不对 case 37 的基础设施解释作自动正确覆盖。

### 5.2 源码内容、工具执行到报告的证据链

- **case 2 / Vanilla：语义性代码生成错误。** 报告定位到 `template.getBasicInfo().getStatus()`，实际 getter 位于 `ReportTemplate`。DTB 44/44。不是工具调用 arguments 缺失。
- **case 3 / 双侧：语义性测试失败。** `testUpdateByTemplateId` 的 creator 没有更新（expected `Updated Test Creator`、actual `Test Creator`）；Vanilla 报告进一步指出 mapper SET 缺 `CREATEDATE/CREATOR/STATUS`。DTB 的报告分析请求本身触发过 probe，但 SQL 缺陷已由测试产生在前，不能把后续报告阶段介入倒因为测试失败。
- **case 26 / 双侧：生成代码调用错误 API。** Vanilla 引用不存在的 `RespHeaderMap`；DTB 对 `RestCaller.Builder` 的构造参数、`build/call` 和响应 headers 访问方式判断错误。DTB `81765af65dfd402b`、`3767e23cc5a84c95` 的可见推理中明确继续假设 builder/response API，后续 compiler 报三个错误。两条均 probe 拒绝后继续，分别只再生成 36、9 个 token（含 think-end）；没有置信度接受导致强制提前回答的证据，但重启轨迹扰动不能凭此排除。
- **case 28 / DTB：语义性工具执行错误，已恢复。** `task` 参数的会话标识被填为 `code_gen_task`，工具返回 `Expected a string starting with "ses"`；之后重新启动子 Agent 成功。工具参数 JSON/schema 合法但值不满足运行时标识要求，不是流式 parser 把参数截断。报告还记录 `ThirdSceneSpaceAllocationServiceDelegateImpl` 的预存编译错误被修复，增加了主流程工作量。
- **case 29 / Vanilla：真实功能偏差。** `SceneTaskLockServiceImpl.runTask()` 的 try-catch 放在循环内部，首次异常后继续下一次调用，测试要求异常结束整个循环；`clearExpiredLocks` 期望 1 次、实际 2 次。DTB 8/8。
- **case 35 / DTB：checked exception 声明缺失。** 长文件 write 请求 `b1a00908e22c4403` 输出重构代码；主 session 的 `call_daf63a6c349f4dfab9da9bd0` 执行 `mvn clean compile`，在 `IncidentManage.java` 的 1109、1111、1117、1135、1139、1164 行报 `ServiceException ... must be caught or declared to be thrown`。涉及拆出的 `persistData/dispatchTasks/handleAlarmTask/submitDiagnosisTask/handleTaskSubmissionFailure`。这是源码语义错误，非正文/参数协议破坏；**本代码生成请求没有介入，case 35 只有标题触发介入**。
- **case 35 / Vanilla：代码通过但交付工作流缺项。** session `msg_0857532eb001sh9LdnPJKBGg2w` 记录 compaction；后续 14 条请求继续评测。`call_3d62ff13eabc4441ac84b77d` 读取 surefire 文件，实际返回 `Tests run: 36, Failures: 0, Errors: 0, Skipped: 0`，最终 assistant 也宣布通过，但没有生成 evaluation_report 的工具调用，归档没有报告。故不是“测试未执行”；“压缩后遗忘报告步骤”是符合时序的候选解释，不是已定位的平台故障。前述 0.688 秒空请求也不能单独解释报告缺失。
- **case 36 / DTB：checked exception 声明缺失。** `parseAndCleanContent` 抛出 `LLMFixException`，但方法签名未声明 throws。主 session `call_390c1bfcd04b475bb3d4fd7c` 编译返回 `LlmInvokeApi.java:[173,13] unreported exception ... LLMFixException`。本 task **51/51 请求全部同 child 旁路**，不存在任何 abort/probe；因此这条新增失败不可能是该请求的 abort 重启或 probe 压缩直接产生。尚不能仅凭两次随机轨迹判定是 sampling、环境或旁路实现差异；应以相同代码生成上下文配对验证。
- **case 37：评测一致性待冻结。** 原始 DT 一侧 1/3、一侧 2/3；两侧都有 `ConfigLoadedTest` 的 Spring runner 初始化 error。Vanilla 另有旧 ES index 断言不匹配；两次报告声明的修改文件数也不同（3 与 7）。报告中“基础设施原因、代码全部正确”的解释不能替代冻结后的测试补丁和原始断言复核。

所有 task 的官方 session/report 文件位置均记录在 `analysis_evidence.json → variants → tasks`，并可从关联概览进入逐 task 请求时间线。

## 6. 时延：整体更快不等于质量门后更快

### 6.1 逐任务（秒）

| case | V/D 请求数 | V 活动跨度 | D 活动跨度 | 跨度收益 | V LLM 合计 | D LLM 合计 | LLM 收益 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 38 / 30 | 326.517 | 444.547 | -36.15% | 251.225 | 372.686 | -48.35% |
| 3 | 35 / 48 | 355.009 | 380.827 | -7.27% | 270.973 | 290.598 | -7.24% |
| 26 | 29 / 34 | 234.586 | 275.655 | -17.51% | 180.138 | 250.365 | -38.98% |
| **28** | **51 / 58** | **545.232** | **584.918** | **-7.28%** | **387.230** | **409.099** | **-5.65%** |
| 29 | 57 / 30 | 533.077 | 388.092 | +27.20% | 455.544 | 299.654 | +34.22% |
| **34** | **29 / 30** | **348.124** | **357.963** | **-2.83%** | **182.105** | **191.822** | **-5.34%** |
| 35 | 64 / 27 | 645.425 | 497.148 | +22.97% | 505.801 | 406.362 | +19.66% |
| 36 | 42 / 51 | 524.139 | 561.980 | -7.22% | 350.402 | 314.115 | +10.36% |
| 37 | 47 / 35 | 509.314 | 468.670 | +7.98% | 319.037 | 270.431 | +15.24% |

### 6.2 汇总与解释

| 样本口径 | V 活动跨度合计 | D 活动跨度合计 | 跨度收益 | LLM 合计收益 | 能否支持目标 |
| --- | ---: | ---: | ---: | ---: | --- |
| 严格两侧全通过：28、34 | 893.356 | 942.881 | **-5.54%** | **-5.55%** | 否，0/2 更快 |
| 两侧报告评级不低于合格：3、28、29、34、37 | 2290.756 | 2180.470 | +4.81% | +9.49% | 否，混入 DT 失败，不能称“两侧均正确” |
| 全 9 项 | 4021.423 | 3959.800 | +1.53% | +3.35% | 否，混入质量变化/提前失败/流程差异 |

全任务活动跨度 P50 为 509.314→444.547 秒，P95 为 605.348→575.743 秒（线性插值）；只有 3/9 活动跨度更快。这些分位数与全量均值一样，**未经过质量门**。

全量 LLM E2E 合计 2902.455→2805.131 秒；响应 completion 263,947→256,639（少 2.77%），本地 reasoning 52,112→47,307（少 9.22%）。这些是不同生成轨迹的观察差异，非同一输出轨迹的因果压缩率。严格双正确子集 completion 少 1.96%，reasoning 反而多 9.23%，耗时增加；再次说明可见 token 减少不等于加速。

具体归因边界：

- case 29 请求数 57→30，是全量正收益的重要来源；不能把少走的工具轮次全部算为标题压缩或 DTB 算法收益。
- case 35 DTB 编译失败，没有完成 Vanilla 的测试阶段；其“快了 148.277 秒”不构成优化成果。
- case 36 LLM 合计下降但活动跨度增加，说明工具/环境处理时间足以逆转模型请求的收益。
- case 28 子 Agent LLM 累计 99.013→74.320 秒，但主 Agent 265.818→306.387 秒，标题 22.399→28.391 秒；主流程增加覆盖子任务节省。case 34 子 Agent 39.663→37.991 秒，标题 22.816→31.308 秒、主 Agent 119.627→122.522 秒。不能只取变快的子阶段。

### 6.3 同完整 HTTP body 的标题配对

去掉 `enable_think_budget` 和 `fixed_*` 后，完整请求体（包括 messages/tools/其他 sampling 字段）哈希相同的跨 variant pair 共 **12 对**、无重复歧义：9 个标题 + 3 个初始主请求。其余轮次不按 turn 编号强配对。

| 标题 case | V thinking | D thinking | V E2E(s) | D E2E(s) |
| --- | ---: | ---: | ---: | ---: |
| 2 | 1364 | 2088 | 15.357 | 35.262 |
| 3 | 1569 | 1662 | 27.053 | 28.472 |
| 26 | 1097 | 1355 | 16.574 | 23.955 |
| 28 | 1788 | 1738 | 22.399 | 28.391 |
| 29 | 3482 | 1350 | 53.459 | 24.259 |
| 34 | 1585 | 1908 | 22.816 | 31.308 |
| 35 | 3003 | 1539 | 39.010 | 32.139 |
| 36 | 1216 | 381 | 17.594 | 5.591 |
| 37 | 2237 | 1878 | 30.614 | 37.537 |
| 合计 | 17341 | 13899 | 244.876 | 246.914 |

标题思考减少 **19.85%**，累计耗时却**增加 0.83%**；其中 case 36 DTB 没有介入，1216→381 的下降本身就说明轨迹波动可以很大。case 29 是清晰的单对正向候选，但还需要重复回放。标题可能不在关键路径，即使单请求缩短也不能等额换算 Agent task wall。

## 7. 成本账本、旁路与可优化空间

### 7.1 DTB phase 账本

| phase | 次数 | prompt / cached token | 生成 token | wall(s) | queue(ms) | prefill(s) | decode(s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| native_passthrough | 343 | 27,859,506 / 26,366,208 | 238,735 | 2448.613 | 8.449 | 334.157 | 2107.889 |
| decision | 13 | 428,454 / — | 0 | 1.120 | — | — | — |
| confidence_probe | 24 | 542,974 / 504,576 | 897 | 51.142 | 0.275 | 31.472 | 18.681 |
| iterative_think | 22 | 478,729 / 451,584 | 7,271 | 108.376 | 0.255 | 12.748 | 94.381 |
| final_answer | 13 | 446,842 / 430,848 | 10,631 | 87.841 | 0.156 | 6.958 | 80.314 |

402 个生成 phase 的 queue/prefill/decode 均有值；decision 是 CPU phase。phase wall（含 decision）合计 **2697.093 秒**，对应客户端 **2805.131 秒**，差 **108.039 秒（3.85%）**。差额可能包含控制器 phase 外工作、parser、传输与代理等，不能无 profiler 就分摊；queue/prefill/decode 也不要求精确加总为 phase wall。

状态账本：可见 thinking **47,308**、hidden probe **897**、final answer **209,329**、unclassified **0**；合计 **257,534**，逐请求与 phase generated 一致。completion 为 **256,639**，等于 47,308+209,329 再加 **2 个置信度接受时合成的 boundary token**；正是标题 case 29、子 Agent case 28 各一个，不是重复计数错误。本地重新 tokenize reasoning 是 47,307，与源 ID 分类账本不能强制逐 token 等值。

**重要限制：13 个 abort 请求的 `model_work_complete=false`。** 当前 [phase_recorder.py](../../../../dtb/observability/phase_recorder.py) 在见到 abort terminal 时明确将该字段置 false；账本不能覆盖取消时设备已做但前端未接纳的全部工作。257,534 只能叫**已记账生成量/模型工作量下界**，不是硬件总工作量。不要因为 phase 与 summary 内部闭合就忽略该标志。

24 次 probe 全部采用 `average_fallback`，2 次接受、22 次 `below_threshold`，没有置信度不可用 fail-open。该方法名不自动表示 parser/tokenizer 故障：当前 [confidence_gates.py](../../../../dtb/reasoning/confidence_gates.py) 在 Value-Min 未启用或条件不足时就返回此方法名。标题的两个接受/拒绝现象只能证明当前 `.93` 策略接受率低，不能凭单轮直接指定更低阈值“必然安全”。

11/13 请求最终 `model_think_end`，只有 2/13 `confidence_accepted`；没有触及总思考 hard budget。`20000` 预算对本轮 1–2K 思考基本没有直接截短作用。

### 7.2 短旁路是否仍有成本

**实测：没有额外模型 handoff；不等于 CPU/调度开销严格为零。** 330 个旁路请求各一个 native child、零 decision、零 probe、无新 final child。全 343 请求记录的：

- entry-to-first-submit 累计 **485.390 ms**；
- observer forward 计时累计 **730.177 ms**；
- 13 次 handoff 累计 **5.764 ms**，显式 abort await **2.508 ms**，drain **0.220 ms**；
- scene router 累计 **1117.233 ms**，集中于真正介入后的预算决策。

这些是当前埋点范围的绝对耗时，部分可能嵌套，不全部相加；不包含所有 backpressure、队列/对象分配或系统争用，也不是相对 Vanilla 的增量基准。标题同输入配对和仅 3 个相同初始主请求不足以估计短旁路的稳定净增量，需 off 回放控制组。

本轮更大的可定位成本是隐藏 probe **51.142 秒**（平均每次介入 3.934 秒），外加 stride/final 重启的 prefill 合计 **19.706 秒**。这些是应重点优化的账本项，而非都已被严格反事实证明的新增耗时。不能把 final 的 87.841 秒全部当作控制器浪费：其中 80.314 秒为答案/工具参数 decode，原生通常也要生成。

### 7.3 当前测试集的“抓大放小”空间

Vanilla 的 392 条记录中，排除无 terminal、无法确认实际生成长度的空请求后，有效 391 条思考 P50=29、P95=578.5、max=3482；380/391（97.19%）≤850。再排除 9 条标题后，主/子 Agent 有效 382 条，380/382（99.48%）≤850，P95=398.1。空请求仍保留在请求数/耗时账本中，不伪装成一个正常零思考样本。

下表残余量为 `Σmax(0, L_vanilla-T)`：假设阈值后所有原生思考都能免费删除、答案和工具路径不变时的**乐观 token 空间**，不是实际可获得的收益。

| T | 全部超过 T 的请求 | 标题残余 token | 主/子 Agent 超过 T 的请求 | 主/子残余 token |
| --- | ---: | ---: | ---: | ---: |
| 500 | 23 | 12841 | 14 | 3617 |
| 850 | 11 | 9691 | 2 | 1222 |
| 1200 | 9 | 6644 | 1 | 685 |
| 1500 | 7 | 4664 | 1 | 385 |
| 2000 | 3 | 2722 | 0 | 0 |

**推论：T850 的可删原生思考尾部有 88.80% 来自标题。** 主/子任务本身只有 1222 token 的乐观残余；即使用示例 TPOT=15 ms/token，也只对应 18.33 秒（全任务活动跨度约 0.46%），还没扣 probe/prefill。15 ms 是用于展示量级的假定值，不是本轮 Vanilla phase 实测。应使用下轮同请求实测 TPOT 更新。

阈值提高可避开 860/889 这类“刚介入就自然结束”的亏损样本，却会进一步缩小业务收益空间；降低到 500 虽增加可介入业务样本，也增加 probe 和轨迹扰动。不存在仅凭本轮分布就能宣布最优的 T。

## 8. 下一轮：按收益机会和质量门分步推进

2026-09-10 补充：用户确认标题请求压缩也计入端到端收益。新增 [全部 probe 轨迹的语义审计与低成本停止候选](PROBE_ANALYSIS.md)，逐条覆盖 13 个请求/24 次 probe，量化 `.90/.80`、trial20/10 的机会，并给出 trial/final cue 对齐、接受后同 child 晋升及拒绝后观察旁路候选。标题独立 E2E 是有效优化指标；与完整任务 wall 分开记账仅为避免重叠重复计算。

随后用户选择先运行 [trial 追加 think-end 的单变量实验](trial_close_experiment/README.md)，保持原 trial50/.93 和其余参数不变；阈值/长度扫描暂后置。

### 8.1 先补可裁决性，不急于改重启实现

1. **冻结实验身份**：部署 manifest 保存实际包/两个上游 SHA、TB/patch/YAML hash、模型与并行设置、observability、代理日志开关、sampling defaults、seed 策略、cache 与单并发到达节奏。`stream_interval=1` 固定，不新增其他 interval 场景。
2. **补全 task start/end**：确认实际使用的 LightCode 客户端将平台原始 `create_time/start_time/end_time` 存入每个 task；字段缺失时报警并标记 task wall 不可用，不补造时间。
3. **固定质量 oracle**：同 base commit/测试补丁/依赖；预存编译问题的处理两侧一致；case 37 的测试更新/基础设施问题先定裁决规则；验证 case 35 compaction 后仍能生成标准报告。代码错误不是协议错误，但最终“质量不退化”不能跳过它。
4. **G0 状态不扩大解释**：本轮支持继续诊断性实验；不为尚未观测到的 E2E 回显抢先加文本重编码。正式发布前仍要完成目标 tokenizer 的可达切点固定流回归；若出现回显，保留异常 parent 的 P/H/S、原始 delta、切点和实际 child 路径，再决定第 12 节候选是否适用。

### 8.2 小规模、同上下文回放：先回答三个问题

复用 `tools/dtb/build_agent_paired_replay.py` 和 `tools/dtb/replay_tool_calls.py`。抽取**完整原始 request_body**，同一行分别 native/DTB；不把 Vanilla 第 N 轮与 DTB 第 N 轮的不同上下文直接算 pair。源位置可按本目录 request index 查到。

| 子集 | 本轮具体输入 | 要回答的问题 |
| --- | --- | --- |
| 真正业务长尾 | Vanilla `80ff52c11ff14d4d`（case 37 主 Agent，1885 token）、`a1470b7a4723479a`（case 29 子 Agent，1037） | 去掉标题后，T850 是否有业务净收益，长尾是否稳定复现 |
| 阈值附近负例 | Vanilla `cc554565d62b4ec4`（822）、`18236eb3c31e4791`（805）；DTB `81765af65dfd402b`、`3767e23cc5a84c95` 的原始上下文 | 是否只制造 probe 开销；同上下文自然剩余思考有多长 |
| 标题收益与波动 | 9 个标题全部纳入，详见 `comparison_summary.json → exact_body_pairs`，不能只选 case 29 | 是策略不匹配，还是单次轨迹/缓存波动；标题收益是否真的进入 task 关键路径 |
| 短旁路 | 从 Vanilla 主/子 Agent 取 ≤50、51–400、401–850 三档各 4 条，跨 prompt 长度且含工具参数生成；包含 case 36 代码生成上下文 | off 下是否保持单 child 与输出协议，短路径 E2E/TTFT 是否稳定非劣 |
| 质量退化上下文 | case 35 `b1a00908e22c4403`，case 36 子 Agent mutation（如 `4c65bf1c55d04943`，以原始调用内容确认具体改动）及 Vanilla 对应代码生成输入 | 在无 probe 条件下是否同样出现 throws 缺失，避免把旁路样本失败误判成压缩副作用 |

执行顺序建议：

1. **基准与成本分离**：先固定本轮参数 `T850 / stride500 / N5 / min1 / p=.93 / budget20000`，每个输入 6 次 V/D 配对、交替顺序、单请求并发；匹配 cache 预热规则。off 为性能组；另以同参数抽样 phase 记录真实触发率、接受率与完整账本。不混合两组 E2E。
2. **有界参数选点**：只有上一步证明长尾稳定，才对同输入比较 T850、T1200、T1500，其余参数先不动；以每次实际触发和净 E2E 判优，不能只按思考长度。短旁路子集不做降低 T 的扫描。
3. **按拒绝来源选第二轴**：若仍是多数 probe 拒绝且自然很快结束，先比较减少 probe 次数/增大 stride 的成本—质量曲线；若已得到正确答案但反复思考、且得分稳定低于 .93，才用当前 probe 得分分布提出至多两个低置信阈值候选。必须编译/测试验证；降低 `.93` 不是已获准的生产修改。不要一次同时改变 T、stride、N、prob 而失去归因。
4. **标题单独裁决**：标题走何种路由、是否原生旁路应由其 repeated paired 收益和关键路径占比决定。run-06 标题低接受率说明值得单独优化，但不能仅因“辅助”就默认全部禁用，也不能把标题节省当成同等 Agent task wall 收益。

每个 pair 保留 E2E、TTFT、reasoning、completion、隐藏 probe、child/prefill/cache/queue、协议异常及实际 `intervention_status`。源输入无工具的标题不设置“必须调用工具”。故障上下文回放只证明 HTTP 输出及采样表现，**不执行工具，不能代替编译/DT 的代码质量测试**。

这些是下一轮实验计划，本次未发送任何回放请求、未替换运行参数。

### 8.3 再做真实 Agent 端到端验收

- 先用 case 28、34 做全通过基础 smoke，再对 case 35、36 检验质量退化与工作流闭环，最终重跑全部 9 case；两侧同修订环境、同测试 oracle，保持单任务并发。
- 最终候选至少做 3 轮完整 V/D，轮间交替顺序，使用 **OBSERVABILITY.mode=off**；单独抽 phase 账本，不将其时间并入正式统计。每轮都要求 9/9 标准报告或明确标记工作流失败，不能删除失败任务来制造性能收益。
- 发布质量门：不新增回显/流式工具协议 hard failure；每 case 编译/DT 不低于冻结的 Vanilla（不借用产品 13 场景“一题容差”）；基础设施未裁决保持 pending。
- 时延门：同时报告全体 task wall 和严格双正确子集，不能只报后者隐藏质量损失；逐 case 差、配对均值、P50/P95、变快次数与重复波动均给出。采用相同样本和明确质量门后才判断最终目标。
- 若主/子 Agent 长尾的可删 token×TPOT 上界仍不足以覆盖控制成本，**停止在这 9 项上盲目扩大扫描**。保留其作为短请求回归集，补充具有稳定长思考、且位于关键路径的规划/复杂代码任务，冻结 Vanilla、分离 search/holdout。新长尾集的收益必须与旧 9 项旁路非劣同时报告，不能换集后宣布旧场景已经优化。

## 9. 离线复算与产物

在仓库根目录执行（仅使用本地 JSON/JSONL）：

```bash
python -X utf8 tools/dtb/correlate_lightcode_proxy.py proxy/results/qwen/local-task29/local-apiserver/dtb/run-06 lightcode/session_logs_dtb_06 docs/log/perf/agent_run06/dtb_correlation
python -X utf8 tools/dtb/correlate_lightcode_proxy.py proxy/results/qwen/local-task29/local-apiserver/vanilla/run-06 lightcode/session_logs_vanilla_06 docs/log/perf/agent_run06/vanilla_correlation
python -X utf8 docs/log/perf/agent_run06/analyze_results.py
```

- [analyze_results.py](analyze_results.py)：读取并验证精确关联、SSE/full-log、phase/summary、固定预算、terminal、task/root 输入与同完整 body 配对；失败 assert 直接退出，不继续发布汇总。
- [comparison_summary.json](comparison_summary.json)：逐 task 比较、12 个同 body pair、分角色长度分布与 phase 汇总。
- [analysis_evidence.json](analysis_evidence.json)：任务报告/错误索引、每请求指标、13 条触发证据及文本首尾预览。
- [phase_requests.json](phase_requests.json)：按客户端 parent 白名单隔离的原始 phase/summary，附服务日志行号。
- [extraction_manifest.json](extraction_manifest.json)：本次离线提取的输入路径/字节数/SHA-256 与覆盖审计，**不是补造的服务器部署 manifest**。Agent 本轮输入不符合 CSV `_SUMMARY` 提取器契约，因此使用此 run 专用 JSONL 审计，不冒称已运行通用 CSV 提取器。

原始大请求不复制进新目录；所有证据保留 shard 路径和行号。新增审计器不会运行日志中的 Agent 指令或工具。

## 10. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-10 | 完成 run-06 双侧关联、compaction/标题补充链接、13 次 abort→probe 回显审计、严格质量门后时延比较及 phase 工作量下界；形成先回放选点再 off 端到端验收计划。未修改 DTB/代理运行时或生产参数。 |
| 2026-09-10 | 增补 PROBE_ANALYSIS：标题收益明确计入；逐轨迹语义、阈值敏感性、trial10/20 成本及需开发的算法候选。 |
