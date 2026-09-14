# Agent DTB 优化展示：数据选择备忘录

日期：2026-09-14。用途：为技术展示选择可追溯的原始数据，不替代各实验的 RESULT.md，也不新增性能实验。

## 1. 选择结论

采用“三层证据”，不要求同一轮数据同时承担正式性能比较和机制解释：

| 展示内容 | 推荐数据 | 用途与限制 |
| --- | --- | --- |
| 全量结果主展示 | Vanilla run-06 对 DTB run-08，全部 9 任务 | 总任务通过率持平、累计 LLM E2E 减少 11.57%、可见 reasoning token 减少 23.70%；采用下述显式质量口径 |
| 长尾分布 | Vanilla run-06 的 391 条有效生成记录 | 展示大量短思考与少数长尾 |
| 原生旁路实现效果 | DTB run-06-closed 的 phase 账本 | 354/364 请求同一原生 child 完成；独立证明没有短请求停后续写，不借此估算 run-08 的实际介入率 |
| 单条业务机制主样例 | run-06-closed case37 主 Agent：测试结果汇总、进入报告阶段，`0480aca87728475f` | 有完整输入、可见思考、实际工具动作和 phase；展示 850 token 后首 probe 接受。与 Vanilla 同阶段轮次仅作描述性对照，不是相同输入配对 |
| 业务语义补充样例 | DTB run-06 case28 子 Agent：patch 信息提取，`0aa42c61e0a5411e` | 展示“已有信息转为结构化输出”，任务最终 12/12；但对应 Vanilla 思考更短，不能作为节省 token 的量化主证据 |
| 同输入压缩备份样例 | run-06-closed case29 标题，`402b1cfd1b7c4fbc` | 请求体去 DTB 参数后与 Vanilla 完全一致，数值比较更干净；放附录，不替代业务机制主样例 |

**核心选择：主展示保留 run-08；单条机制改以主 Agent 业务轮次为主，标题降为严格同输入对照的备份。** 当前审阅的业务候选中，尚未找到同时满足“相同 HTTP 输入、同参数 phase、明显压缩、完整业务质量验证”的完美单条样例，不拼接不同实验制造这类证据。

## 2. 全量端到端结果：Vanilla-06 对 run-08

### 2.1 原始数据与统计口径

仓库相对路径：

```text
Vanilla proxy: proxy/results/qwen/local-task29/local-apiserver/vanilla/run-06
Vanilla eval:  lightcode/session_logs_vanilla_06
DTB proxy:     proxy/results/qwen/local-task29/local-apiserver/dtb/run-08
DTB eval:      lightcode/session_logs_dtb_08
分析入口:      docs/log/perf/agent_run07_08/RESULT.md
机器账本:      docs/log/perf/agent_run07_08/analysis.json
质量集合:      docs/log/perf/agent_run07_08/cohorts.json
来源 hash:     docs/log/perf/agent_run07_08/extraction_manifest.json
```

DTB 结果提交 `e73d099`。9 个主任务输入的完整内容 hash 逐 case 相同，后续 Agent 轨迹、调用数与上下文不同。因此比较单元是任务，不把两侧第 N 个请求强行配对。

用户认可的“无劣化”采用**任务通过率不下降**，不要求每个具体任务严格非劣。这里进一步明确：一个任务的最终 DT 全部通过才计成功；分母固定为全部 9 任务。将 Vanilla case35 原始测试日志中的 36/36 纳入，虽然它缺正式报告；缺报告这一交付异常继续披露，不补造评级。

| 指标（实测） | Vanilla-06 | DTB run-08 | 变化 |
| --- | ---: | ---: | ---: |
| 最终 DT 全通过任务 | 4/9，44.44% | 4/9，44.44% | 持平 |
| 累计 LLM E2E | 2,902.455 s | 2,566.697 s | 减少 11.57% |
| 可见 reasoning tokens | 52,112 | 39,759 | 减少 23.70% |
| 响应 completion tokens | 263,947 | 237,105 | 减少 10.17% |
| LLM 请求数 | 392 | 350 | 减少 42 次 |
| 平台执行时间合计 start→end | 4,299.173 s | 4,129.363 s | 减少 3.95% |
| Excel G 列 create→end 合计 | 5,201.631 s | 5,088.942 s | 减少 2.17% |

LLM 指标定义为 `Σ任务内全部请求 metrics.e2e_ms / 1000`，包括主 Agent、子 Agent、标题、失败和恢复请求，不用 session 跨度。probe、续写 prefill 等已经包含在外部请求 E2E 内，不再次相加。391 条有效生成之外的 Vanilla 无 SSE 请求仍保留在 392 条请求与耗时账本中。

可直接用于展示的表述：

> 在这组 9 场景完整 Agent 执行中，按最终 DT 全通过任务计数，并纳入 Vanilla 缺报告任务的原始测试证据，两侧通过率均为 4/9；DTB 的累计 LLM 端到端耗时减少 11.57%，可见 reasoning token 减少 23.70%。

详细边界表述：

- Vanilla 全通过集合为 case28、34、35、36；run-08 为 case2、28、34、35。case36 由 4/4 退为 3/4，case2 由编译失败变为 6/6。**总通过率持平不等于逐任务非劣。**
- 仅按正式报告计数则是 3/9→4/9，不与上述 4/9 基线混用。不能称全部 9 任务均正确或交付质量完全等价。
- case29 的测试范围为 8→87，不能以聚合测试条数通过率宣传正确率提升；case2 前期失败也没有同等完整测试结果。当前是任务级展示口径，不是严格固定测试套件因果评估。
- 11.57% 专指**累计 LLM E2E**，不是用户等待的整项评测 wall time；后者本轮只有上述 3.95%/2.17% 降幅。
- run-08 按用户说明关闭 telemetry，保留 close + .90。无 phase，不能给出 hidden probe 或真实总生成量压缩率；reasoning token 减少也包含调用次数、执行轨迹变化。
- 未提供完整部署 manifest，采样 seed 未显式固定，比较使用历史 Vanilla；这是已观测收益，不是统计显著性或普遍因果效果证明。

请求参数核对：`T=850, stride=500, N=5, min=1, budget=20000, gate=.90`，trial 长度 50；共同 `model=qwen3.6-27b, max_tokens=32000, top_p=1, stream=true`。close/off 的部署行为依据用户实验说明，不能由请求体独立验证。

完整质量、协议审计与工作簿来源见 [run-07/08 结果分析](C:/Users/F-O-O/Projects/TB/docs/log/perf/agent_run07_08/RESULT.md)。run-08 的 350 个请求、474 次工具调用未检出已知 SSE/JSON/schema 协议故障，但有一次合法 JSON 中的无效 task_id 导致工具运行时拒绝，随后恢复；不写成工具执行“零错误”，而是工具调用无工程性问题。

### 2.2 为什么不以 run-07 作为主展示

run-07 全量 LLM E2E 减少 25.50%、reasoning 减少 42.58%，数字更大，但报告 7/9 中 case37 只运行 1/1，而 Vanilla 运行 3 条；不能直接把它包装成“完整任务成功率 7/9”。case35 还由原始 36/36 退为编译失败。建议完整列作重复实验附录，不只展示最好数字。

可选稳健补充：固定 case28、34 三方最终 DT 均全通过，run-07/08 的累计 LLM E2E 分别减少 15.08%/19.19%；run-07 case34 经修补 import 后通过、评级下降，仍须披露。此集合的 run-07 reasoning 反而增加 0.94%，不能说两轮都同时满足三项指标。

## 3. 单条机制主样例：case37 主 Agent 汇总测试结果

### 3.1 精确定位与实际停止路径

- DTB：`run-06-closed`，任务 `case37`，proxy request `0480aca87728475f`，parent `chatcmpl-a385fe220c683c95`。
- 完整输入/输出：[DTB 原始分片](C:/Users/F-O-O/Projects/TB/proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/full_log/proxy_session_795d073fadb0/part-00001.jsonl)，第 **20** 行；第 **21** 行的请求历史包含已执行的本轮 assistant 与工具返回，可交叉核验。
- 服务账本：[phase 日志](C:/Users/F-O-O/Projects/TB/proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/server_filtered_part_01)，第 **1077–1081** 行。机器索引为 `docs/log/perf/agent_run06_closed/analysis.json` 的 `triggered` 项。
- Vanilla 同阶段参考：`80ff52c11ff14d4d`，[原始分片](C:/Users/F-O-O/Projects/TB/proxy/results/qwen/local-task29/local-apiserver/vanilla/run-06/full_log/proxy_session_af084cebfded/part-00001.jsonl) 第 **32** 行。

本轮为真实业务请求，不是标题，也不是只返回不执行工具的孤立回放：主 Agent 已收到 Maven 的 DT 执行结果，需要汇总并进入生成评测报告阶段。

```text
工具结果已返回
  → 原生思考 850 token（9.769 s）
  → 达阈值 abort，收到 abort terminal，排空新增 token=0
  → closed trial 50 token（1.416 s），得分 .904024 ≥ .90
  → 接受，不再 iterative_think
  → fresh-final 174 token（2.211 s），输出结果摘要并调用 bash 创建报告目录
```

decision 为 53.795 ms；外部请求 E2E 为 **13.843899 s**。上面的 phase 数值不是全部客户端耗时，不能强行令其加和等于 E2E。可见 thinking=850、hidden probe=50、final=174，已记录模型生成为 1,074 token；对外 completion=1,025，含合成边界但不含 hidden probe。账本 `model_work_complete=false`，1,074 只能称已记录工作量下界。

### 3.2 语义层面：哪些信息已经足够，哪些思考在重复

| 位置 | 实际文本/事实 | 对早退的意义 |
| --- | --- | --- |
| 输入工具结果 | `Tests run: 3, Failures: 1, Errors: 0, Skipped: 0`；失败为 EsIndexUtilsTest，首次 expected32/actual31，重试 actual0 | 汇报测试数量、通过数和失败项所需的外部事实已经齐备；不能因日志有 BUILD SUCCESS 就报 DT 全通过 |
| 思考开头 | `I see the results:`，随即列出 3 tests、1 failure、2 passed | 本轮最重要的结果汇总在很早就已得到，不必等长段推敲结束才可输出 |
| 中间 | 逐项重列 index.json 的 31 个索引；随后 `Let me count: That's 31 indices total` | 大量生成用于复述已有列表和再计数，而非获取新工具证据 |
| 850-token 附近 | `But then the count should be 34, not 32`，又转回 `I'm getting 31 items total ... but the test expects 32` | 汇报所需信息没有增加，模型仍在数目与原因之间反复推敲；但这也说明根因解释尚未完全收敛 |
| 早退后 | 正文保留 Tests run=3、Failures=1、Passed=2；随后“步骤8：生成评测报告” | 可见交付保留了核心测试事实，没有将失败抹掉 |
| 实际动作 | `bash` 调用 `mkdir -p /root/.local/share/opencode/log`，工具返回正常；下一轮获取报告文件名时间戳 | Agent 继续执行报告流程，不是把任务直接截断或跳过报告 |

**语义判断：对“汇报已完成测试的计数与失败项、开始报告流程”这一子目标，早退具有合理性。** 所需事实已经来自工具，思考开头已正确抽取；后续内部复述/再计数不改变已执行测试的结果。DTB 停的是这一轮内部思考，而不是取消后续工具或提前宣告整个任务成功。

但是，“为什么可以早退而不影响正确率”的证据必须分层：

1. **实测已确认：**早退后的摘要保留 2/3，不伪报 3/3；Agent 继续报告流程，最终记录仍为 2/3。工具参数合法，未观察到本轮结构泄漏或异常回显。
2. **语义上支持：**核心汇总信息在阈值前已充分，继续反复计数对该子目标的新增价值较低。
3. **尚未证明：**早退前已准确查明根因，或如果不早退就一定还会多思考 1,035 token。DTB 正文将失败解释为测试数据问题并断言“与 AI 生成的代码无关”，这一归因不能仅靠模型自己的说明成立；重试 actual0 也没有被充分解释。因此不把该样例称为“诊断完全正确”。

该任务 DTB 为 2/3，Vanilla 为 1/3，**两侧都不是全通过任务**。这不妨碍展示报告阶段的停止机制，但不能用最终 2/3 证明早退提升了代码正确率：测试是在该次早退之前完成的。

### 3.3 如何展示压缩数字而不混淆配对性质

| 同 case、同阶段参考 | Vanilla-06 | DTB run-06-closed |
| --- | ---: | ---: |
| 可见 reasoning token | 1,885 | 850 |
| 外部请求 E2E | 31.077132 s | 13.843899 s |

描述性差异为 reasoning **减少 54.91%**、E2E **减少 55.45%**。但两者输入不同：Vanilla 的工具结果为 3 tests、1 failure、1 error，DTB 为 3 tests、1 failure、0 error；历史上下文和生成轨迹也不同。

展示时将数字标签写为“**同业务阶段的实测对照，非同输入配对**”。不要把两个 reasoning 文本拼成同一条轨迹的“原文/截断版”，不要将 1,035 token 或 17.233 s 称为这次 abort 已证明的反事实净节省。主展示的可靠量化收益仍来自第 2 节完整任务累计结果；此处负责解释机制。

还需明确：`.904024` 是 `average_fallback` 的试答 token 概率分数，不是 90.40% 的答案正确概率；50-token trial 因 length 结束，且未保存其全文，不能画出伪造的“隐藏试答完整正确答案”。接受后是重新生成 final，不是原样复用 trial。

## 4. 子 Agent 备选与同输入备份

### 4.1 case28：更干净的“信息已齐备，直接整理输出”语义案例

原始位置：`proxy/results/qwen/local-task29/local-apiserver/dtb/run-06/full_log/proxy_session_1c94a63f8a4f/part-00001.jsonl` 第 1 行，request `0aa42c61e0a5411e`。分析见 [PROBE_ANALYSIS.md](C:/Users/F-O-O/Projects/TB/docs/log/perf/agent_run06/PROBE_ANALYSIS.md)。

子 Agent 从给定 patch 中提取变更文件、实体与测试方法。思考已列出 BlockAllocCalculator、ResponseAssembler、ThirdPointAllocationServiceDelegateImpl、CapacityDBMapper.xml 等文件和相关测试方法，随后又以 `Now let me look more carefully at the test method signatures from the diff` 开始复述已经列过的方法签名。851 token 处 probe=.985006 接受，最终直接交付含 `file_info/entities/test_method` 的 JSON。这里“输出就绪”的直观含义是：可以把已经抽取的信息交付为结构化结果，而不是先在 reasoning 中再次写一遍。

原任务最终 12/12，Vanilla 对应任务也是 12/12，说明完整流程存在正向质量结果；但没有逐字段参考答案审计，不能仅凭编译/测试通过证明这份提取 JSON 每项都完整正确。

**不选为量化压缩主例的原因：**对应 Vanilla 子 Agent `d9dda152fd534b14` 只有 344 reasoning token，DTB 为 851；E2E 25.644906→25.487352 s，几乎持平。不能因 DTB 在 851 早退就把它宣传为相对 Vanilla reasoning 压缩。且该样例属于旧版未 close、gate=.93，不冒充 run-08 的机制轨迹。

后续 closed 固定输入回放也保存了该业务上下文：`docs/log/perf/agent_run06/trial_close_experiment/requests.jsonl` 第 9 行，结果在 `docs/log/perf/agent_trial_close/run_server_01/closed/`。4 次中 3 次首次 probe 接受、1 次旁路，可复用作业务停止机制补证，但工具未真实执行、未逐字段裁决，不以挑一条较快回放替代端到端质量结论。

### 4.2 标题备份：用于回答“有没有严格同输入的明显压缩”

case29 标题的 Vanilla `4b40058e71eb45f8` 与 DTB `402b1cfd1b7c4fbc` 去 DTB 参数后的请求体 SHA-256 均为：

```text
d917865ebf93477bdb8f2c2a81828fbccf9265e0078fb16184ca34036a519886
```

Vanilla reasoning **3,482**→DTB **850**（减少 **75.59%**）；E2E **53.458545→15.126337 s**（减少 **71.70%**）。DTB 首 probe 8 token、得分 .991634 接受，final 8 token。介入前已选定标题并反复核对约束，最终交付“场景任务创建分布式锁实现评测”，适合说明已形成答案仍继续推敲。

原始定位：

- Vanilla：`proxy/results/qwen/local-task29/local-apiserver/vanilla/run-06/full_log/proxy_session_78f9b3bc5ec6/part-00001.jsonl` 第 6 行。
- DTB：`proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/full_log/proxy_session_4ee2844dc607/part-00001.jsonl` 第 1 行；同 run 服务 phase 第 326–330 行。

该对输入更可比，但仍是两次随机生成，不是相同前缀的受控反事实实验。标题计入约定的 LLM E2E 收益合法；不可用它替代业务 Agent 样例，也不可把标题时延直接等同平台关键路径缩短。

## 5. 长尾分布与“抓大放小”的展示方式

使用 Vanilla-06 的 `docs/log/perf/agent_run06/analysis_evidence.json` 中 `thinking_distribution`；说明见 [run-06 RESULT 第 7.3 节](C:/Users/F-O-O/Projects/TB/docs/log/perf/agent_run06/RESULT.md)。长度是 proxy 本地 tokenizer 对 reasoning 文本的计数，不是字符数，也不是 API usage 中可能为零的 reasoning 字段。

| 指标 | 全部有效请求 | 主/子 Agent 业务请求（去标题） |
| --- | ---: | ---: |
| 请求数 | 391 | 382 |
| reasoning P50 | 29 | 29 |
| reasoning P95 | 578.5 | 398.1 |
| reasoning 最大值 | 3,482 | 1,885 |
| ≤850 token 请求 | 380/391，97.19% | 380/382，99.48% |
| >850 token 请求 | 11/391，2.81% | 2/382，0.52% |

11 条长尾累计 20,263 reasoning token，占全部 52,112 的 **38.88%**。20,263 是这些请求的完整思考，不是可全部删除的冗余；阈值之后的尾部只有 10,913 token，且尚未扣减必要推理和控制器成本。

建议展示“请求数量占比”与“reasoning token 占比”两个相邻图，再附标题/业务拆分；不要只画平均数。长尾应标记为“潜在介入对象”，不直接标为“无用思考”。

机制说明：短请求在原生生成中跨过 think-end 后继续完成正文/工具调用，不因 think-end 提交第二个 child；只有达到介入阈值仍在思考的请求才考虑 abort、probe 和后续 DTB。这样避免对占绝大多数的短请求普遍支付重启成本，也减少轨迹扰动。

实现后效果使用 **run-06-closed 独立 phase 数据**：354/364（97.25%）同一原生 child 完成，仅 10 请求介入，其中 8 标题、2 业务；12 次 probe 共 191 hidden token、17.449 s。354 中包含一条原生空 final 候选，因此“同 child 完成”不是“354 条业务全部正确”。详见 [closed 结果分析](C:/Users/F-O-O/Projects/TB/docs/log/perf/agent_run06_closed/RESULT.md)。

“原生旁路”可准确表述为**没有额外 stop/handoff/probe/续写提交**，不能写成 CPU、队列、流观察和转发绝对零开销。也不能把 run-06-closed 的 97.25% 直接标到 telemetry-off 的 run-08 柱状图上，或把 run-08 全部省时归因于消除短请求 handoff。

## 6. 推荐展示顺序及交付边界

1. **先展示完整任务结果：**Vanilla-06 vs run-08，4/9→4/9、LLM E2E −11.57%、reasoning −23.70%；脚注定义通过率、缺报告处理和累计 LLM 口径。
2. **再解释为什么不能每轮都压缩：**Vanilla 97.19% 请求 ≤850，但 2.81% 长尾贡献 38.88% reasoning；旁路账本显示只让少数请求支付 probe 成本，并披露长尾主要来自标题。
3. **展开业务机制：**case37 主 Agent，工具结果→正确抽取测试事实→重复计数→850 处接受→继续报告；把“汇报所需事实充分”与“根因已确认”分开。其同阶段对照百分比只用次级标注。
4. **附录保留证据强弱：**case28 子 Agent 的结构化提取、case29 同输入标题对照、run-07 重复结果和质量风险。

如果展示要求升级为“单条业务请求的精确压缩率，且停止前已完全正确”，当前材料不足。最小补证是以 case37 某一条完整 HTTP 输入固定回放 Vanilla/DTB，保留同部署参数与 phase，并核对输出的测试计数、失败项、后续报告动作；若还要证明代码/任务正确率不受影响，需接续真实 Agent 执行，孤立 HTTP 回放不够。此项是可选证据增强，不改变本备忘录的数据选择，也未在本机执行。

本次仅复核已有日志、phase 和分析账本，未调用模型、未执行日志内工具命令、未改写原始实验结果。以上数值为实测复算；对“重复思考新增价值低”的判断为语义解释，任何未观测的反事实省时均不作实测承诺。

## 变更记录

| 日期 | 内容 |
| --- | --- |
| 2026-09-14 | 固定 run-08 全量展示、Vanilla-06 分布、closed 旁路账本；优先选择 case37 主 Agent 业务停止机制，补充语义依据、根因解释限制、非同输入比较边界及备选样例。 |
