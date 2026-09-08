# 最低介入值 G0 / G1：run_server_01 结果与下一轮计划

分析日期：2026-09-08。目标仍是：短思考原生旁路、长思考获得净收益，最终降低 Agent task wall，而不是只降低响应 token。

## 1. 结论

1. **G0 未通过，属于重启文本保真问题，不是可忽略的答案语义差异。** 768 个固定 token 切点对照有 60 个失败；拟支持的无 string-stop 子集也有 30/384 失败。Unicode/emoji 前缀重启后出现 prompt 回显和文本重复；完整排空、token ID 不丢失，仍不足以保证 detokenizer 可安全重启。
2. **G1 证明了短思考不会因为正文很长而重新提交，但尚未证明所有切换分支可用。** phase 组 32 次 DTB 中，31 次同一个原生 child 完成，1 次介入后执行 probe。已观测旁路思考长度为 8–264 token；正文最长的旁路请求总计 4,507 token，也没有触发 abort。
3. **回放中仅一种协议错误：工具参数为空字符串、无法解析为 JSON，共 3 次。** 主回放 Vanilla 2/480、DTB 0/480；phase 回放 Vanilla 0/32、DTB 1/32。后者明确是原生旁路、没有 abort，不能归因于介入切换。语义告警单列、按本次要求不作协议门禁失败。
4. **尚不能宣称 DTB 带来稳定压缩或 E2E 优化。** 主回放预先声明的时延样本、双侧协议合格配对：可见 completion 减少 2.06%，合计 E2E 降低 3.78%；去掉有明显首轮顺序偏差的样例首轮后分别只有 0.45%、0.80%。全 16 输入协议合格配对去首轮后，可见 completion 增加 1.80%，E2E 增加 0.98%。这些都是请求级实测差异，**不是 Agent 端到端收益，也不是思考压缩率**。
5. **下一轮先关闭 G0 与工具协议风险，再扩大长尾样本和切换分支覆盖。** 暂不做大范围参数搜索，不进入 Agent 性能发布。短路径结构成立与长期净收益成立是两道不同的门。

## 2. 实验身份、完整性与证据边界

### 2.1 输入与实际参数

| 项目 | replay | replay_phase |
| --- | --- | --- |
| manifest 创建时间 UTC | 2026-09-07 09:07:47 | 2026-09-08 01:16:47 |
| 输入 / 重复 | 16 个历史工具调用输入 × 30 次 × 两种 variant | 同一输入 × 2 次 × 两种 variant |
| 请求数 / 配对数 | 960 / 480 | 64 / 32 |
| 服务地址 | localhost:9001 | localhost:9001 |
| 请求模型 | qwen3.6-27b | qwen3.6-27b |
| streaming / 顺序 | streaming；每样例 attempt 奇数先 Vanilla、偶数先 DTB | 同左 |
| 请求参数 | max_tokens=32000、top_p=1；temperature/seed 未指定 | 同左 |
| DTB fixed 参数 | budget=20000、check_length=850、stride=500、num_thinking=5、min_think=1、prob_th=0.93 | 同左 |
| 可用服务器证据 | 未上传对应服务器账本 | server_filtered_part_01：37 条 DTB_PHASE schema 3 |

输入是 `docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl`，不是 9 个 Agent 的完整执行。
其中行 1–10 的 `latency_eligible=true`，行 11–16 是 `quality_probe`、`latency_eligible=false`。
`category=protocol_failure` 是历史输入标签，不能当成本轮发生协议错误。
每行的 `dtb-context` / `vanilla-context` 是历史上下文来源；**本轮同一行输入内部的 Vanilla/DTB 才是一对**，不能跨上下文行配对。

**已验证：**

- 输入 LF 规范化 SHA256 与两份 manifest 一致：`8667912b8a5b525c448cd8822281e7aa044df696cedbc2b16b42a46589e66e90`。Windows checkout 原始字节为 CRLF，不能直接与 Linux LF 文件 hash 比较。
- 以现有 replay 的 `build_body` 重建了全部 1,024 个实际请求，逐条与 chunks 的 `request_body_sha256` 相等；不是仅凭相同 case_id 假定输入一致。
- 所有预期 `(line_no, attempt, variant)` 均存在且唯一，HTTP 都是 200；summary、request 记录、有效 SSE chunk 数闭合，无缺失配对。
- phase 中 parent ID 恰好覆盖 32 个 DTB 请求，无额外请求、重复 phase；Vanilla 没有 DTB phase 合法。
- 两个子 run 复用了 `request_id_run=intervention-g1`，因此跨 run 的 ID 会重复。此次以 **子 run + request_id** 隔离，未混合归属。下轮必须使用不同 ID 后缀。
- 工具源码逐请求 `await run_one`，是单并发；这一点不构成服务器无其他流量的证据。

**尚缺：**服务部署 commit/dirty diff、vLLM-Ascend/PyTorch/torch-npu/CANN、模型修订与 tokenizer/patch/YAML hash、cache 策略、其他服务流量、metrics 快照以及完整 `DTB_REQUEST_SUMMARY`。G0 只记录 `vllm=0.23.0+empty`、`tokenizers=0.22.2`、`CachedTokenizersBackend`，不能代替部署身份。`replay` 的 observability mode 未写入 manifest；不能因为没有 log 就认定它是 `off`。phase 文件未包含 request summary，不推断生产端没有生成，只能说上传证据缺失。

本次源码定位依据当前本地 TB `ddde51c46b5b16f5472ec4f1cc4c4ce69ab9bcaf` 及固定 vLLM checkout；**不是服务器 commit 的证明**。正式性能排名门禁仍未通过。

## 3. 正确性

### 3.1 G0：固定 token 重启对照

| fixture | 对照数 | 失败数 |
| --- | ---: | ---: |
| ASCII | 192 | 0 |
| 中文、emoji、组合字符 | 300 | 60 |
| 引用 `</think>` 文本 | 84 | 0 |
| reasoning boundary + tool JSON | 192 | 0 |
| 总计 | 768 | 60 |

证据：根目录 `detokenizer.jsonl` 的 `event=summary` 和逐条 `event=cut`。失败只涉及 sample=1，在排空后的 restart=5、7、8、10、12；无 string-stop 也失败 30 次，pending=0 也失败 20 次。该 7.81% 是**人工枚举 fixture 的失败比例，不是线上故障概率**。`forced_close=false/true` 两种续写均有失败。

一个无 string-stop、保留原始固定后缀的实际样例：

```text
sample=1, trigger=1, pending=4, restart=5, forced_close=false
prefix_ids = [96847, 99986, 95999, 40958, 59720]

expected:
分析中文与 emoji 👩🏽‍💻🙂；组合字符 é 与换行\n结束。

actual:
分析中文与 emoji<|im_start|>assistant\n<think>\n分析中文与 emoji 👩🏽‍💻🙂；组合字符 é 与换行\n结束。
```

这里没有重新采样，没有模型“选择不同答案”；相同 token 后缀在新 decoder 中多输出了 prompt。结构标记也进入了错误文本，属于 parser-facing 文本连续性缺陷。若相同行为扩展到真实长 prompt，存在上下文回显风险，但本轮 Agent replay **没有证据证明已经发生该风险**。

**源码确定的定位范围：**

- `tools/validate_dtb_intervention_detokenizer.py` 使用真正的 OutputProcessor/collector 执行显式 abort，断言接纳的 IDs 等于 `generated[:end]`、请求表清空，然后比较“连续 decoder”与“旧输出 + 新 decoder(P+H)”的同一固定后缀。最终 summary 存在说明这些 token 保全/回收断言没有中途失败。
- 因此 G0 排除了“仅因 R 被丢弃或 RNG 变化导致这 60 个差异”；问题至少位于**重启后的增量文本解码契约**。
- `third_party/vllm/vllm/v1/engine/detokenizer.py:61` 决定 fast/slow 路径；`:183` 的 fast decoder 以全部 prompt IDs 初始化 DecodeStream；`:263` 的 slow decoder 建立 prompt 的 prefix/read offsets。重启时两者都必须正确区分已提交前缀与尚未形成完整字符的尾部。
- **待验证假设：**Unicode token 切点存在字节/增量解码未完成状态，而重新构造 decoder 的 prompt 初始化/消费偏移没有恢复同等状态。现有记录没有实际 detokenizer 类、DecodeStream 所属模块、逐 token 新输出，不能直接宣布是 fast 路径的某个异常恢复分支，也不能宣布改 slow 必然修复。

这不等同于重新引入整段 prompt decode：优先在 decoder/adapter 责任层解决状态恢复或确定安全切点；不能靠删字符串前缀、吞掉空 delta 或过滤 `�` 来修补，因为合法引用和 token/text 对应关系也会被破坏。

### 3.2 G1：语义与协议分层

| run / variant | 请求数 | 协议合格 | 协议失败 | 语义告警请求 |
| --- | ---: | ---: | ---: | ---: |
| replay / Vanilla | 480 | 478（99.58%） | 2 | 46 |
| replay / DTB | 480 | 480（100%） | 0 | 39 |
| replay_phase / Vanilla | 32 | 32（100%） | 0 | 3 |
| replay_phase / DTB | 32 | 31（96.88%） | 1 | 3 |

语义告警的 3 类：

- `expected_tool_name_missing`：主回放 V=44、D=33；phase V=3、D=3。模型选择的工具不符合历史期望，不是 SSE/XML 结构破坏。
- `invalid_tool_arguments_schema`：主回放 V=1、D=3。JSON 能解析，但缺少工具必填字段。例如 DTB 行1/attempt13 的 task 缺 `subagent_type`；行5/attempt11 缺 `prompt` 和 `subagent_type`；行9/attempt2 的第二个 write 缺 `filePath`。这是工具契约/任务质量问题，不是 JSON 语法或流式封装错误。
- `expected_tool_call_missing`：主回放 V=1、D=3。历史期望下一步调用工具，但实际没有调用。单凭这一告警无法证明流式 parser 丢失调用，按本次要求归语义/行为层保留，不作为协议门禁失败。

上述是**回放审计分类**，不是工具执行后人工证明答案正确。此次语义告警不剔除时延配对、不触发协议门禁；后续完整 Agent 仍须独立验证任务完成率。

所有协议失败均为 `invalid_tool_arguments_json`，具体不是“JSON 内容不理想”，而是工具名已发出、参数一直为 `""`，随后结束：

| run | line / attempt / variant | 实际工具 | SSE 证据（chunks.jsonl 文件行） |
| --- | --- | --- | --- |
| replay | 4 / 19 / Vanilla | task，arguments=`""` | 5977：唯一 tool delta（event31）；5978：tool_calls terminal（event32） |
| replay | 10 / 2 / Vanilla | write，arguments=`""` | 14850：唯一 tool delta（event10）；14851：tool_calls terminal（event11） |
| replay_phase | 2 / 1 / DTB | task，arguments=`""` | 250：唯一 tool delta（event57）；251：tool_calls terminal（event58） |

第一条前文说准备启动代码生成子 Agent，正文为“现在启动代码生成subagent，要求在base commit版本下生成修复代码。”第二条前文说先创建 JSON 再创建 Markdown，正文为“好，让我创建评测报告。”第三条思考规划了用 Optional 修复 `invokeLlm` 空指针及 `parseAndCleanContent`，但最终仍只有 task 名、没有参数。

对每个失败请求检查了全部 SSE，而非只检查末条摘要：没有被客户端漏拼的后续非空 arguments。工具名 + 空参数 + `finish_reason=tool_calls` 无法执行，属于实际协议失败。

phase 的失败请求 `chatcmpl-dtbtool-l2-a1-dtb-intervention-g1` 在 `server_filtered_part_01:3`：

```text
intervention_status=disarmed_boundary
observed_reasoning_tokens=156, observed_final_tokens=3605
abort_count=0, native_same_child_completed=true
source_terminal_passthrough_count=1
```

**实测裁决：它不是 abort/drain/restart 产生的错误。** Vanilla 存在同一表面错误族，但目前没有 parser 输入前的原始 text/token IDs，不能区分模型本身未生成有效参数、detokenizer 文本异常、parser 未发出参数。也不能仅凭 Vanilla 有同类错误就证明两侧完全同根或“DTB 严格不增加故障率”。

本轮已上传证据没有出现外泄 abort、重复 terminal、role 尾流、工具 XML 泄漏或切换后 JSON 截断；这不弥补 G0 的失败及未覆盖的切换分支。

## 4. 可见压缩与请求级 E2E

定义正数为改善：

```text
E2E 优化率 = 1 - Σ DTB elapsed_ms / Σ Vanilla elapsed_ms
可见 completion 减少率 = 1 - Σ DTB usage.completion_tokens / Σ Vanilla usage.completion_tokens
逐对优化率 = 1 - DTB_i / Vanilla_i；另报其中位数，不与合计比值混用。
```

### 4.1 主回放：保留语义告警，协议坏配对透明单列

| 统计集合 | 配对数 | V / D 合计 E2E（秒） | E2E 优化率 | 可见 completion 减少率 |
| --- | ---: | ---: | ---: | ---: |
| 预声明时延样本，全量（含协议失败） | 300 | 3139.050 / 3019.737 | +3.80% | +2.22% |
| **预声明时延样本，双侧协议合格** | **298** | **3116.830 / 2999.005** | **+3.78%** | **+2.06%** |
| 上项去每个输入 attempt1（敏感性分析） | 288 | 2939.001 / 2915.478 | +0.80% | +0.45% |
| 全 16 输入，全量（含协议失败；描述性） | 480 | 6380.828 / 6343.498 | +0.59% | −1.34% |
| 全 16 输入，双侧协议合格（描述性） | 478 | 6358.608 / 6322.766 | +0.56% | −1.42% |
| 上项去 attempt1（描述性敏感性分析） | 462 | 6062.934 / 6122.486 | −0.98% | −1.80% |

298 对主口径下：V/D completion=314,315/307,839；DTB 更快 143/298（47.99%）；逐对优化率中位数 **−1.72%**，DTB−V 中位数 **+60.889 ms**。V/D E2E P50=9.474/9.517 秒，P95=26.491/26.266 秒。不能用合计正收益暗示多数请求变快。

原工具的 fully-clean 把语义告警也排除，主回放只剩 242 对。本报告按用户要求保留语义告警，因此使用 298 对；没有改写子目录自动 RESULT，也没有把两份口径混用。

### 4.2 phase 回放：独立账本，不与主回放合并排名

| 统计集合 | 配对数 | V / D 合计 E2E（秒） | E2E 优化率 | 可见 completion 减少率 |
| --- | ---: | ---: | ---: | ---: |
| 预声明时延样本，全量 | 20 | 199.465 / 211.696 | −6.13% | −27.34% |
| 预声明时延样本，双侧协议合格 | 19 | 185.134 / 180.117 | +2.71% | −12.39% |
| 全 16 输入，全量（描述性） | 32 | 424.984 / 437.658 | −2.98% | −9.21% |
| 全 16 输入，双侧协议合格（描述性） | 31 | 410.653 / 406.079 | +1.11% | −3.29% |

19 对中 DTB 更快 9 次，逐对优化率中位数 −6.43%；删掉一个协议坏配对就使合计收益变号，说明小样本/失败筛选极敏感。phase 的 2 次重复不用于收益裁决。唯一介入请求来自行11，是预声明 `latency_eligible=false` 的质量探针，所以它没有被塞入主时延样本排名。

### 4.3 首轮偏差与压缩证据限制

**实测：**主回放有 14 次 Vanilla 首输出超过 1 秒（1.32–16.07 秒），phase 有 12 次（1.31–10.30 秒），全部是各自输入的 attempt1；DTB 没有超过 1 秒的首输出。工具在 attempt1 固定先运行 Vanilla。

**假设：**样例首次 prefill/cache 预热等首次请求成本偏向 Vanilla。phase 的 DTB observation 多数有大量 cached prompt，方向与假设相符，但没有对应 Vanilla phase/cache 指标，不能定量认定全部差异来自缓存。

去首轮后的主回放：预声明样本 first_output 平均 V/D=590.089/593.543 ms；全输入为 564.755/566.449 ms。说明原始平均 TTFO 的大幅优势高度依赖首轮。**去首轮是事后敏感性分析，不是已完成严格热缓存实验**；缓存驻留、采样差异仍未控制。

客户端 usage 没有独立 reasoning token 数，也没有主回放 DTB phase，故不能计算这批请求的真实思考压缩率/总模型工作量压缩率。可见 completion 包含正文和工具参数；两侧没有固定 seed，原生生成轨迹不同也会改变其长度。不能把 +2.06% 全部命名为 DTB 压缩。

逐输入双侧协议合格结果供定位，不作独立场景泛化：

| 输入行（上下文来源） | 主回放 N | 主回放 E2E 改善 | 主回放 completion 减少 | phase E2E 改善 |
| --- | ---: | ---: | ---: | ---: |
| 1 task-truncation-1 / DTB context | 30 | +17.13% | +17.76% | +15.78% |
| 2 task-truncation-1 / Vanilla context | 30 | −17.50% | −24.42% | −23.32% |
| 3 task-truncation-2 / DTB context | 30 | +8.86% | +5.73% | −9.72% |
| 4 task-truncation-2 / Vanilla context | 29 | −1.72% | +0.45% | +12.64% |
| 5 task-truncation-3 / DTB context | 30 | −25.86% | −34.48% | −29.61% |
| 6 task-truncation-3 / Vanilla context | 30 | +6.55% | +4.75% | −7.32% |
| 7 checkout / DTB context | 30 | +1.83% | +1.73% | +46.55% |
| 8 checkout / Vanilla context | 30 | +26.71% | +30.32% | +13.44% |
| 9 report-write / DTB context | 30 | +6.59% | +6.06% | +4.30% |
| 10 report-write / Vanilla context | 29 | +13.66% | +2.69% | +52.65% |
| 11 case26-code / DTB context（质量探针） | 30 | +8.94% | +4.46% | −118.52% |
| 12 case26-code / Vanilla context（质量探针） | 30 | −0.13% | −0.25% | +2.70% |
| 13 case28-code / DTB context（质量探针） | 30 | +1.08% | −0.47% | −0.18% |
| 14 case28-code / Vanilla context（质量探针） | 30 | −3.62% | −4.35% | +3.39% |
| 15 case3-XML / DTB context（质量探针） | 30 | −21.10% | −23.62% | +48.80% |
| 16 case3-XML / Vanilla context（质量探针） | 30 | +0.79% | +0.12% | +3.80% |

phase 每行通常 N=2，行2 为 N=1。输入类别代表历史故障样本，不是 16 个独立生产场景。

## 5. phase：短路径与唯一长路径

### 5.1 短路径

31 个旁路请求全部 `disarmed_boundary`、`abort_count=0`、`native_same_child_completed=true`，没有 decision/probe/final child，原生 terminal 透传一次。其思考长度合计 1,838，P50=26，最大264；长正文不会误触发 850 思考阈值。这是本轮最明确的机制正向证据。

`observer_forward_wall_ms`：均值3.805 ms、P50=2.956 ms、P95=9.850 ms，31 请求合计117.942 ms，约为这些请求 observation wall 的0.029%。**不是“零成本”或纯额外 CPU 的测量**：源码 `deferred_observation.py:154–190` 在 accept 内计时，包含计数、转发及 await；不覆盖所有外层初始化，且运行了 phase telemetry，也没有等价 Vanilla 计时项可扣除。可以说所测区段很小，不能据此宣称生产 off 模式旁路全部损耗恰好3.805 ms。

### 5.2 唯一实际介入：行11 / attempt2

parent=`chatcmpl-dtbtool-l11-a2-dtb-intervention-g1`，证据 `server_filtered_part_01:22–27`。

| phase | 生成 token | 可见性 | wall ms | prefill ms | decode ms |
| --- | ---: | --- | ---: | ---: | ---: |
| native_passthrough → abort | 853 | 可见 | 12997.077 | 374.033 | 12612.303 |
| decision | 0 | CPU | 24.904 | — | — |
| confidence_probe round1 | 50 | 隐藏 | 1821.516 | 885.704 | 880.640 |
| iterative_think | 500 | 可见 | 8326.240 | 375.668 | 7885.794 |
| confidence_probe round2 | 50 | 隐藏 | 929.544 | 357.710 | 530.609 |
| final_answer | 917 | 可见 | 7631.425 | 351.821 | 7232.719 |

阈值850，实际 G=853、overshoot=3；abort 一次且收到 terminal；R=0，H=853，无排空 boundary。abort=0.154 ms、drain=0.019 ms、handoff=0.412 ms。第一轮置信度0.679534拒绝，第二轮0.982268接受。探针使用 `average_fallback`；是否适合 Agent 工具动作的质量决策还需要独立评测，不能从本条“无协议错”证明其质量有效。

此处两个 probe 合计 **100 隐藏 token、2751.060 ms**；初始请求之后四个模型 child 的 prefill 合计 **1970.903 ms**（已包含在各 phase wall 内，不要再次相加）。controller decision 24.904 ms。源码确定的重复提交成本和实测账本均表明：**abort 操作自身不是本例主要开销，probe 与后续生成/重复 prefill 才值得优化**。

配对 Vanilla：8.415 秒、1,014 completion；DTB：31.847 秒、2,271 completion。
实测 E2E 优化率 **−278.45%**，可见 completion 减少率 **−123.96%**。模型实际生成为853+500+917+100=**2,370**，比 Vanilla 1,014 增加133.73%；响应2,271包含1个合成 boundary，但不包含100个隐藏 probe，因此不能直接以 usage 比较模型工作量。两侧协议和语义审计均无告警。

不能据此归因“DTB 使原本8秒的同一条确定轨迹增加23秒”：Vanilla 思考只有一句“Now let me create the InstanceUtils.java file based on the detailed requirements.”；DTB 在介入前就已经生成853思考 token、耗时约13秒，长于 Vanilla 整个请求。未固定 seed，也没有固定共同前缀的反事实 continuation。**该长轨迹经 DTB 后究竟比不介入省了多少，当前不可知；只知道这次配对没有兑现目标。**

### 5.3 账本闭合与 usage 元数据

- 全32个 DTB 客户端 E2E=437658.191 ms；37 phase 的 wall 总和432363.930 ms，缺口5294.261 ms（1.21%）。这个缺口包含未被 phase 计入的 serving/tokenization/parser/SSE/客户端等范围，不能全部贴上“DTB CPU”标签。
- 唯一介入请求 phase wall=31730.706 ms，客户端31846.849 ms，差116.143 ms。phase 生成可见 token=2270、合成boundary=1、隐藏probe=100。
- 全组 phase 可见生成49307，响应completion49308，相差1个合成boundary；隐藏生成100，总模型生成49407。相对 Vanilla 45150 的总生成量增加9.43%，不是压缩收益。
- phase 的 DTB 行11/attempt2 响应 `prompt_tokens=41438`，而对应 observation 与 Vanilla 输入均为40061；41438恰好等于 final child 的 prompt 长度。主回放行11/attempt17 也出现40061→40981的响应 prompt usage 变化，但没有服务器账本，不能凭此裁决其所有内部 phase。
- 全部 request_body hash 已匹配，故上述不是输入配对错误。当前 vLLM serving 在每个 RequestOutput 重读 `len(res.prompt_token_ids)`（`chat_completion/serving.py:499–502`），会使最后的内部前缀影响对外 usage。**这是待修正/验证的计量元数据问题**，不是本次空 tool arguments 的已证根因；账本应使用 observation 的原始 prompt 与各 child 独立成本，不能把对外 prompt usage 当原始输入长度。

## 6. 下一轮实验：先可用性，再净收益

计划以当前已实现的 observation→abort/drain→DTB 为基线，保持 `stream_interval=1`，不增加其他 stream_interval 场景。阈值与预算规则仍是 `fixed_check_length <= fixed_budget_tokens`。

### P0 / G0-R：优先定位并关闭 Unicode 重启风险

目的不是让报表全绿，而是证明拟支持切换路径的 parser-facing text/IDs 连续。G0-R 未通过前不增加生产长请求介入范围。

1. 在同一服务 Python/tokenizer 环境重跑现有脚本，完整保存 stdout/stderr、退出码和部署身份。补录 transformers/tokenizers/fastokens 版本、实际 `type(detokenizer)`、`DecodeStream.__module__`、实际 skip_special_tokens 与相关 fast-detokenizer 开关；不能仅由 tokenizer_class 推测实际路径。
2. 对 sample1 的 restart=5/7/8/10/12，逐步记录：旧 decoder 每步新增 text、abort terminal text/IDs、新 decoder 首步输出及 prefix/read offsets（实际实现有该字段时）。**同一组 P/H/S IDs** 分别走连续、abort-drain 重启、直接以 P+H 创建 decoder 三条路径，精确区分 collector 与 prefix 初始化责任。
3. 保留 pending=0/1/4、forced_close 两种情况；补入真实 Agent 长 prompt 和中文工具参数 fixture、与部署一致的 special-token 配置。不把空文本 delta 视作无 token，也不改变或重新 encode 已接纳 H。
4. fast/slow 或相邻依赖版本仅作为隔离诊断，禁止未经验证直接升级部署。若必须增加安全切点检测，证明检测开销只落在触发边界且不要求短请求 full prompt decode；若只能缩小支持范围，应在**abort 前**可靠 disarm，不能取消后才发现不可恢复。
5. 验收：支持的无 string-stop 路径全部同 token 后缀文本相等、H 保全、无 prompt 回显/结构污染、请求表回收；通过后重新执行 parser 回归。仅 string-stop 对照通过不能解除其能力门禁。

### P1 / G1-R：定向原始流取证与 parser 固定流回归

#### P1.1 目的、范围与实施前置

本实验只回答：**空 arguments 是原始输出不完整，还是 reasoning/tool parser、DTB 转发或 serving 丢失了本来存在的参数；当前 parser 补丁是否引入回归？** 不用于测量压缩率或正式 E2E，也不以“Vanilla 同样失败”作为排除 parser 的依据——两侧共用同一补丁。

源码候选路径已经明确：`qwen3_coder_parser.patch` 先发工具名及空 arguments，等 `</function>` 后才发完整参数；缺少结束标记、解析异常或返回空值，都可能保留“有工具名、无参数”的表面结果。必须取得该次失败的实际输入才能裁决，不能直接认定是哪一条分支。

范围先限定输入 `cases.jsonl` 的行2、4、10，每行 Vanilla/DTB 各30次，单并发、交替顺序、`stream_interval=1`。保持原采样和850阈值，不主动制造新切换。**这三条历史短输入重新采样仍可能变长**；G0-R 尚未通过时，先做 Vanilla 取证，DTB 部分须待支持路径验证通过后再执行，或另开明确标记的仅旁路诊断组，不能把后者冒充850策略实验。

**本节是待实施计划，不是现有工具已具备的能力。** 当前 `DTB_STREAM_AUDIT` 只在控制器路径记录 source/forwarded，`enable_think_budget=false` 绕过该路径；仅把 Vanilla ID 加入 `OBSERVABILITY.stream_request_ids` 不会采到其原始流。应先实现下面的公共取证点及固定流输入适配；不再把开启 `OBSERVABILITY.mode=stream_audit` 作为本实验的必要步骤。取证组可以保持 `OBSERVABILITY.mode=off`，但额外启用了取证设施，**仍然不是正式 off 性能基线**。

#### P1.2 最小采集链与文件契约

为限定 parent ID 安装只读采集点，不改变采样、parser 输入、delta 顺序或结束处理：

| 采集点 | 位置与覆盖 | 必须保存的内容 |
| --- | --- | --- |
| A：engine_output | engine generate 返回的 RequestOutput，DTB 转发前；Vanilla 也在原生生成路径采集 | child ID、逐 delta text/token_ids、finished、finish_reason、stop_reason；这是 **detokenizer 后** 的输出，不宣称是 scheduler 内部原始状态 |
| B：parser_entry | 实际送入原生 reasoning/组合 parser 的输入，两种 variant 共用 | 逐 delta text/token_ids、终态与来源 child；DTB 的合成/隐藏终态及边界来源；与 A 对齐的序号 |
| C：tool_parser_call | reasoning 分流后的实际 tool parser 调用边界 | previous/current/delta text 与 token IDs、调用顺序、工具 schema、解析返回值/异常；尤其保留标记只在 token_ids 中出现的情况 |
| D：SSE | 原生 serving 序列化后的响应 | 复用 replay 的 chunks.jsonl：工具名/arguments delta、finish_reason、usage、DONE/错误 |

只修改临时取证实现，不直接修改只读 `third_party` checkout；在 manifest 记录部署源码、取证补丁及 parser hash。A/B 不一致仅对声明的压缩、合成 boundary、内部 abort 隐藏等合法变换豁免，**不能机械要求所有 DTB phase 的 A/B 都相等**；原生旁路则要求有效载荷及终态相等。

建议归档到新目录 `docs/log/perf/agent_minimum_intervention/run_server_02/`，不覆盖本轮证据：

- `capture_manifest.json`：模型/服务/依赖版本与 commit、三份部署 patch/YAML hash、取证开关与白名单、有效 sampling 参数、原始输入 SHA256、实验阶段及采集点版本。
- `replay_capture/`：现有 replay 的 manifest、summary、chunks、自动 RESULT。
- `raw_capture/<parent_request_id>/request.json`：输入行/attempt/variant、原始 request_body 引用及 hash、初始 prompt IDs；每个 child 的实际 prompt IDs和有效参数各保存一次，避免每 delta 重复整个 prompt。
- `raw_capture/<parent_request_id>/part-*.jsonl`：A/B/C 事件，含 schema、parent/child ID、site、单调 sequence、关联序号、有效载荷及终态。C 的实际调用参数须完整可还原；不能用 SSE 反推或用重新 tokenize 的文本冒充原始 token IDs。
- `capture_index.jsonl`：每个请求的文件/hash、各采集点事件数、采集完整性、协议结果、是否进入 DTB。每请求一个目录，按体积分片，但不截断单个事件；溢出、写失败、缺序号都标为 `capture_incomplete`，不得用于归因。
- `parser_replay/`：固定 fixture、各 parser 版本的逐调用返回/状态、最终重建结果、判别结论及最小复现样例。

取证从请求开始保存，不能等客户端发现空参数才临时开启；否则前面的生成证据已经丢失。首轮保存所选请求的完整流及成功对照，落盘分片，不使用会覆盖请求前半段的尾部 ring buffer。prompt/工具参数可能含内部数据，只保存显式白名单，不记录认证头；对外分享的脱敏副本与原始证据分开，不能将脱敏导致的 parser 变化当原故障。

#### P1.3 定向在线复现

先用一个 Vanilla 与一个 DTB 请求做采集 smoke，核对两侧 A/B/C/D 均覆盖、请求 ID 能关联、终态完整、旁路数据未改变。**仅 smoke 通过后**执行以下命令；它只负责发送请求，不能代替 P1.2 的采集实现。当前均未在本机/服务器执行：

```bash
python tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl \
  --lines 2,4,10 --mode both --order alternating --repetitions 30 --stream \
  --url http://127.0.0.1:9001/v1/chat/completions \
  --fixed-budget-tokens 20000 --fixed-check-length 850 \
  --fixed-think-stride 500 --fixed-num-thinking 5 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.93 \
  --request-id-run intervention-g1r-capture \
  --output-dir docs/log/perf/agent_minimum_intervention/run_server_02/replay_capture
```

公共取证点的 parent 白名单为 `chatcmpl-dtbtool-l{line}-a{attempt}-{variant}-intervention-g1r-capture`，line∈{2,4,10}、attempt∈1..30、variant∈{vanilla,dtb}；内部 child 通过 parent 归属继承，不用宽泛前缀采集所有服务请求。现有 `--list-dtb-request-ids` 只能辅助生成 DTB 部分，公共白名单必须显式补全 Vanilla。先做 Vanilla 时用 `--mode vanilla`，并使用独立的 run ID/输出目录及相应白名单。

30次未复现，只记录“本次未复现”，不认定问题消失；可按预先冻结的输入与参数追加到每行每variant累计100次，追加批次使用新run ID/目录、保留全部失败分母。累计仍未复现则停止盲目扩样，报告证据不足；合成 fixture 的通过不能替代缺失的真实失败证据。捕获到完整失败流后即可开始离线归因，无须等待随机实验全部结束。

#### P1.4 固定流回归：不重新调用模型

1. **先验证可重放性。** 保留原始 delta 分片、空文本非空 IDs、边界与 terminal；以新建的 parser 实例重放失败 fixture，核对当前部署版本重建出同样的工具名、空 arguments 和结束结果。若不能复现，先补齐 request/parser 初始状态、实际调用参数或 serving flush，不能据此排除 parser。
2. **两级回放定位。** 直接将 C 的实际调用参数送入 tool parser，测试工具解析本身；再从 B 经实际 reasoning/组合 parser、tool parser及 serving结束处理重放，定位分流与终态转换。C正确返回arguments但D丢失时继续查serving，不能统称tool parser问题。A的text/IDs若疑似不一致，再以实际tokenizer固定序列验证detokenizer，与G0关联。
3. **隔离补丁变量。** 对同一fixture比较当前部署tool parser与固定vLLM v0.23.0未打该tool补丁版本，其他reasoning/serving/schema条件保持不变；候选修复作为第三组。使用独立源文件/隔离测试环境，不能在正在测量的服务上来回卸载patch。原版不是绝对正确的oracle：对完整XML结构与期望arguments再做独立语法/schema检查，并人工确认最小样例；非流式parser结果只作交叉证据。
4. **验证分片不敏感。** 首先回放原始分片，再合并相邻、同一流且无终态隔断的完整delta；覆盖function/parameter结束标记、空文本结构token、多个tool相邻输出。不得把Unicode文本任意切开后猜token IDs，也不得把不同child或内部abort跨界合并。需要更细切片时用真实tokenizer/增量decoder生成可验证的text/IDs。服务器 `stream_interval` 始终为1，这不是新增stream_interval实验。
5. **成功与非法输入都要保留。** 同输入的成功流、防回归历史fixture、合法空参数对象 `{}` 与非法空字符串 `""` 分别测试。对本来不完整的XML，不通过伪造 `{}` 或猜测参数让测试变绿；检查其错误/终止行为，避免静默宣布可执行成功。

可复用 `tools/dtb/parser_partition_equivalence.py` 的direct/delegating两层测试思路和 `--qwen-parser-source` 版本隔离方式。**它目前使用内置fixture及部分stub依赖，并没有读取本次raw_capture的CLI**；本节要求新增固定流输入适配和真实部署环境的pipeline回归，不能声称现有脚本直接覆盖本次失败，也不编造未实现的命令参数。

#### P1.5 归因与验收

| 固定流证据 | 本样例裁决 / 下一步 |
| --- | --- |
| A已有完整合法参数，B在旁路中丢失或发生未声明变换 | DTB转发/集成问题；先修复A→B |
| B完整，C缺失必要结构或参数 | reasoning/组合parser分流问题 |
| C完整合法，当前tool parser丢参数；原版能正确处理 | tool parser补丁回归的直接证据 |
| C完整合法，原版和补丁都失败 | 共同parser缺陷或调用状态问题，不能排除parser |
| C正确返回arguments，D未输出 | serving序列化、flush或终态处理问题 |
| A已缺少结束标记/有效参数，且token解码对照一致；B/C未丢数据 | 该样例不是parser丢失已有完整参数；继续定位模型输出及停止原因，并单独评价parser对非法输入的终态策略 |
| A的text与相同token序列的连续解码不一致 | detokenizer/重启问题，转G0-R；不能直接判模型生成错误 |
| 任一采集点缺失或当前版本无法重放线上结果 | 证据不足，禁止宣布“与parser无关” |

通过条件不是“必须证明parser无关”，而是得到可审计的归因：捕获故障的四层证据闭合；当前行为可以固定流重现；若是补丁问题，候选修复在故障流、成功对照及合法分片下保持工具名/参数/顺序与结束语义正确，并通过原有回归；若原始输出已经非法，结论严格限定到该样例，不豁免仍存在的协议失败。

固定流通过后，在真实服务以原输入和相同采样配置做独立小批在线回归，检查无新增协议故障。离线协议验证不执行工具、不证明代码质量，也不能证明完整Agent正确率非劣。输出 `parser_replay/RESULT.md`，逐故障引用parent ID、采集文件/事件序号、首个差异层、版本hash、归因强度及尚未解决项。

#### P1.6 独立 phase 组：真实切换覆盖

取证结束后关闭公共采集设施，另开不同ID的 `OBSERVABILITY.mode=phase` 组，添加固定seed的paired诊断与run-05 Vanilla短/阈值附近/历史长思考输入。固定seed降低方差，但不保证abort后RNG或采样轨迹完全相同；历史长输入本次仍可能变短。取证组、phase账本与P2正式off性能组不得混合排名。下表保留原有切换门禁：

| 覆盖项 | 当前实测 | 下轮门禁证据 |
| --- | --- | --- |
| boundary 前自然旁路、长正文 | 31次 | source/forwarded相等，child1、abort0、probe0、decision0、terminal一次 |
| T 后 abort、无 boundary → probe | 1次，且R=0 | 扩展多个真实长输入，含probe接受/拒绝；审计H不重计、completion额度 |
| R>0，排空含 think-end/正文 | 未覆盖 | H=G+R，直接final，probe0，无额外cue/close |
| 排空含 tool前缀/参数 | 未覆盖 | token/text连续，arguments完整、单terminal |
| 自然 stop/length 抢先、取消/error/超时、额度耗尽 | 未覆盖 | 确定性状态测试＋可控服务器验证；不能把本地测试说成服务器实测 |

为制造分支，可对指定输入把 T 放在其 boundary 附近；**只用于诊断，不参与850阈值收益排名**。保留既有非流式回归，但本轮不扩展其介入功能。需要保留完整 `DTB_REQUEST_SUMMARY`、phase与metrics，修正日志过滤漏项；同时核对外部原始 prompt usage 的稳定性。

### P2 / G2–G3：有控制的长尾收益与完整 Agent

1. 不再用16条历史故障输入估计生产长尾分布。按 run-05 Vanilla 真实轮次提取短、中、T附近、显著超过T的分层集合，保留任务/session/轮次及原请求体映射。延用850作已测锚点；候选T由真实 Vanilla 分布与成本账本决定，不因单次负收益就降低T或遍历大量参数。
2. 为判断长轨迹压缩，可预先固定长输入集合、多seed配对，并对触发前缀做隔离的共同前缀 continuation 对照，区分“本来会自然结束”与“DTB真正减少长思考”。后者是算法诊断，不能冒充完整请求E2E。
3. 正式请求级性能用 `OBSERVABILITY.mode=off`；独立 `phase` 组闭合工作量。每个输入正式计时前给两侧相同预热条件、预先规定不计入正式统计的 warmup，并记录缓存命中/驻留策略。不能只跑一遍全数据预热就假定所有长 prompt 都留在cache。保留冷热不同目标时各自单独成组。
4. 单并发、同模型/采样/seed/到达节奏、偶数重复且顺序均衡，补齐部署manifest。逐对报告E2E、P50/P95、胜率、可见reasoning/final与隐藏probe、child queue/prefill/decode。不要用响应completion代替总工作量。
5. 净收益分解为“长请求节省 − 长请求probe/续写成本 − 所有短请求观察额外成本”。本例abort不足0.2ms，probe约2.75s；需要重点量测/筛选值得介入的尾部，不能仅优化abort函数来期待秒级收益。上述单例成本不能未经验证外推到所有prompt长度。
6. G0/G1通过后做9场景完整 LightCode单并发：先确认主Agent环境构建、子Agent执行、主Agent评测报告均完成，9/9报告与质量不退化、无新增协议hard failure；随后以至少两轮反向顺序的off实验裁决task start/end wall。proxy按session日志与LightCode归属闭合，报告LLM E2E、工具/环境耗时与总轮次。

**发布决策：**当前只能认定短旁路结构获得正向验证；G0不通过、G1切换覆盖不足、收益无稳定证据。下一轮先做G0-R＋G1-R，未达到门禁时继续原生开关回退，不把协议失败从正式可用性统计中删除。语义告警在本轮协议分析可忽略，但最终Agent质量目标不因此被豁免。

## 7. 复算、交付与变更记录

本报告保留两个子目录自动 RESULT 原文；新增 `analyze_run.py` 与 `analysis.json`，包含逐输入/各统计集合、全部协议失败SSE证据和phase归属。离线复算：

```bash
python -X utf8 docs/log/perf/agent_minimum_intervention/run_server_01/analyze_run.py
```

该脚本只读归档、写同目录 `analysis.json`；验证输入/请求hash、配对、chunk数、phase归属及G0汇总，不调用模型或服务器。不将仅有JSONL的本目录伪装为 `extract_dtb_phase_metrics.py` 所要求的CSV run。

本轮本地验证：上述离线复算通过；没有执行服务器重测，也没有修改运行时实现。所有表格为上传数据实测复算；源码推导和待验证假设已分别标注。

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-09-08 | 首次G0/G1完整审计；区分语义/协议、保留语义时延样本；补充首轮偏差敏感性与真实phase账本，制定G0-R/G1-R/G2–G3门禁 | 用户要求分析可用性、压缩与E2E，并规划下一轮；防止把短旁路随机输出差异当作DTB压缩收益 |
| 2026-09-08 | 将P1的stream audit实验替换为定向原始流取证与parser固定流回归：明确公共采集点、Vanilla覆盖、归档契约、原版/补丁对照、归因与验收，保留独立phase切换验证 | 两侧共享parser补丁，Vanilla也失败不能排除parser；现有DTB审计不覆盖原生Vanilla，必须先补齐真实输入证据，不能把未实现采集能力当作现有功能 |
