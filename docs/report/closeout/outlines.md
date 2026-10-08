# DTB 最终汇报内容稿

状态：**最新版为 [5 页演示稿 r9](presentation/DTB_closeout_20260930_r9.pptx)；第 4 页按用户确认采用 Noemate 全部 50 对长请求回放的时延效果，沿用分段轴、空心点与页脚修复。**
整理日期：2026-09-24，最近更新：2026-10-08。沿用原大纲四部分，不加封面、章节页或演示附录。
演示正文聚焦主要信息；本稿保留具体数据、管道组件、来源与审阅依据，细节不全部上屏。
下文“审阅备注”及末尾备忘只供内容审阅，**不制作成演示页**。

## 完整结构

| 页码 | 内容 | 本页要说明的事 | 主要引用 |
| --- | --- | --- | --- |
| 1 | 技术背景与 DTB 方案 | 为什么控制预算、核心思想、实现方式与架构 | 用户参考 PPT；当前 DTB 文档 |
| 2 | 工程实现：流式与投机解码兼容 | 用一页时间线说明协议难题及处理 | 历史工程复盘时间线 |
| 3 | 工程实现：Agent 场景抓大放小 | 研发极端长尾分布、原生观察、按需接管 | Vanilla-06 研发分布；生产 observation 实现 |
| 4 | 优化效果 | Noemate 分布与长请求回放时延、研发 Agent 累计 LLM 耗时 | Noemate 8,277 条分布、50 对回放；Vanilla-06 对 run-08 |
| 5 | 交付产物 | vLLM 集成 DTB 与独立观察器 SDK | 两个交付分支及文档 |

## 第 1 页：技术背景与 DTB 方案

### 建议正文

- **问题**：Agent 在信息已经足够时仍可能继续复述、检查和反思，增加模型调用时延。
- **核心思想**：为思考设置预算，在允许介入的位置判断是否继续，将生成资源用于必要推理和最终答案。
- **实现方式**：DTB 集成在 vLLM 服务内部，根据请求与场景确定预算。必要时生成隐藏试答，利用 token 概率评分决定继续思考或转入最终回答。
- **整体架构**：请求路由与预算配置、原生生成观察、thinking/probe/final 状态机、原生 reasoning/tool parser。原生路径直接完成，DTB 路径按既有条件接管。

### 紧凑版布局（2026-09-30）

采用参考页的“问题与核心思想＋整体框架”组织：左侧约三分之一宽度讲背景、目标和服务内实现位置，右侧展开可编辑的实现框架。

框架主线为 **Agent 请求 → 场景识别与路由 → 预算参数 → vLLM 生成与观察**。
满足检查条件后进入隐藏试答与 token 概率评分；评分不足且仍有预算时继续思考，评分达标或总预算耗尽时引导收束、生成最终答案，再交给原生 parser 输出。
原生分流与自然结束用简短旁注说明。保留关键分支，不把参考图里未实现的检测与接管能力搬到主图。

### 素材与来源

- **R01**：[动态思考预算控制方案.pptx](reference/动态思考预算控制方案.pptx) 第 1 页，沿用其四项技术组织方式，并按当前源码改写框架图。
  已抽取文字到 [reference_slide_text.json](evidence/reference_slide_text.json)，不改动原 PPT。
- **C01、C02、C03、C05–C08、P01、P03、P05**：当前路由、预算、观察与三状态实现，具体路径和行号见 [来源索引](sources.md)。
- 可支撑“信息已够但仍推敲”的具体记录：**S13 §3.2**，case37 已得知 `3 tests / 1 failure / 2 passed`，之后仍重复计数。本页不新增性能数字。

### 审阅备注，不上屏

- 参考 PPT 的“思考长度增加 4 倍以上”“提升思考速度 40%”缺本次可核对的同条件原始对照。前者保留为原方案背景陈述，后者是原方案目标，不转成实测结论。
- 原 PPT 的“API 封装”调整为当前服务内实现，不恢复已退出交付的 `think_budget`。
- history/difficulty 是可选能力，当前默认关闭（C07）。在线学习最佳预算、信息增益、语义重复检测、人工接管不能统称已实现。
- token 概率评分不是答案正确概率。接受试答后会生成 final，不保证直接复用试答全文。
- 并非每个请求都执行 probe：达到总预算可直接 final，流式短请求沿同一原生 child 完成。
- 图中“预算参数”表示配置职责，流式观察触发后才裁决完整预算；非流式直接进入三状态，不把观察路径泛化为所有请求。

## 第 2 页：工程实现：流式与投机解码兼容

### 建议正文

**工程难点**：一次逻辑响应中接管生成，必须保持文本、token ID、工具结构和结束事件一致。
投机解码一次返回多个 token，思考结束标记可能与正常正文、工具参数处在同一批输出中。

沿用原大纲的**一页工程时间线**：

| 阶段 | 问题与处理 | 可引用的历史验证数据 |
| --- | --- | --- |
| 8/13–8/14 | XML 泄漏、tool 后仍出正文或 role 尾流；修正状态边界和 text/token IDs 交接 | tool 后 content：50/100 → 0/100；后续前缀对齐后 XML 泄漏：13/100 → 0/100 |
| 8/17–8/19 | 参数末尾与结构标记同批；处理连续工具调用及同批收尾 | 180 对回放中，170 条含工具的 DTB 响应、265 个调用，目标结构异常为 0 |
| 9/1 | 参数 JSON 未闭合；区分生成未完成与 parser 收尾 | DTB 非法 JSON：133/300 → 0/300 |
| 9/4 | 思考 stop 与正常尾句同批，正文被吞；保留正常文字及结构 ID 的连续性 | 新轮 60/60 DTB 公开正文与 pre-parser 一致；48 个混合 chunk 的 344 个字符全部保留 |

**实现结果**：形成 serving、Qwen tool parser、Qwen reasoning parser 三份 vLLM 0.23.0 部署补丁。

### 素材与来源

- **S14**：历史 `docs/report/sum0911/工程复盘时间线_报错样例.md`，A–D 与“时间线和结果口径”，已经列出请求 ID 和日志行号。
- 历史视觉参考：同目录 `工程复盘时间线_含报错示例.pptx`。本轮只复核内容，不重制图。
- 如选一个报错原文，指定 **S14-D**：`dtbtool-l5-a10-dtb-run04-token-stop-audit-01`。
  原有尾句 `Let me try a`，同批正常增量 ` different approach.\n`，结构 stop ID `248069`。
  完整公开结果是 `The task call format is problematic. Let me try a different approach.`

### 审阅备注，不上屏

- 各项对应不同定向回放，不能相加为统一通过率，也不是同一批输出逐字前后对照。
- 8/14 两轮仍有其他异常 22/100、12/100；9/1 仍有 DTB 缺 terminal、Vanilla 空参数。只写具体目标项，不写“当轮全部通过”。
- 8/17–19 的另外 10 条无工具响应中，8 条有 raw 支持模型未生成工具，2 条缺证据未分类。
- 历史 MTP 回放覆盖同批收尾；9/4 未补录完整 MTP 启动身份，不将其作为新的 MTP 性能实验。
- 9/4 来自两条固定历史上下文，DTB 均为短思考，不代表 probe、迭代和强制接管全路径验收。

## 第 3 页：工程实现：Agent 场景抓大放小

### 建议正文

**分布依据**：辅助研发 Vanilla-06 的 391 条有效请求中，380 条思考不超过 850 token（97.19%）。超过阈值的 11 条请求仅占 2.81%，却贡献 38.88% 的可见思考 token，最长达到 3,482 token。

本页使用**研发 Agent 全链路请求（含标题）**的分布，说明短请求占绝大多数、少数极长请求值得重点观察；Noemate 分布移到第 4 页，与其效果案例相邻。

**处理策略**：先保持原生生成，在输出传输过程中增加观察环节。短请求沿原生路径完成，仍在思考且达到阈值的请求才进入接管判断。

**接管过程**：保留已接受输出，停止并排空当前 child，在合法边界续写，维持最终答案与工具调用的 parser 契约。

“给模型输出传输过程加一段观察管道”可作直观解释。本页讲生产 DTB 观察环节，独立 SDK 放第 5 页。

### 管道示意：评价与展示目的

**建议采用。** 分布数据解释“为什么抓大放小”，管道解释“在哪里观察、怎样接管、为什么短请求不用重新生成”。
它能把预算算法与流式服务工程连接起来，比再增加一张完整状态机图更适合这一页。

建议讲述为：**“在引擎输出进入原生 parser 之前，增加一段观察与接管环节。短请求沿原来的生成过程完成；需要介入时停止当前生成，保留已有前缀，再引导模型生成答案。”**

这是一条带控制回路的**逻辑输出管道**。实线表达输出数据流，返回引擎的虚线表达 `abort` 和新 `generate` 请求。
控制发生在服务内，不是沿 HTTP 字节流反向修改内容。图中必须保留这个区别。

### 根据源码核对的完整组件顺序，供绘图备查

范围：vLLM **v0.23.0** 固定提交 `0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665` 的 V1 AsyncLLM、多进程 Chat Completions 流式路径，叠加本仓库 DTB patch。
依据为本地只读源码，属于**源码确定**，不表示本次已追踪服务器运行链路。

| 顺序 | 管道中的实际组件 | 经过的数据与职责 | 源码引用 |
| --- | --- | --- | --- |
| 1 | `EngineCore / EngineCoreProc` | 调度/执行一步后产出 `EngineCoreOutputs`，放入 core 的 `output_queue` | U01 |
| 2 | `process_output_sockets`、ZeroMQ、`EngineCoreClient`（`AsyncMPClient`） | Msgpack 编码与跨进程发送，客户端接收、解码并放入 `outputs_queue`；这里尚非 HTTP/SSE | U01、U02 |
| 3 | `AsyncLLM.output_handler`、`OutputProcessor` | 消费引擎输出，增量 detokenize、stop 检查、logprobs 处理，构造带文本和 token IDs 的 `RequestOutput` | U03、U04 |
| 4 | 每请求 `RequestOutputCollector`、`AsyncLLM.generate()` | 聚合/交付该请求的输出增量，由异步 generator 向调用方 yield | U03、U04 |
| 5 | **DTB 观察与接管段** | `_DeferredInterventionState` 读取增量文本/token IDs、累计思考长度并识别边界；`stream_output → result_queue → stream_queue` 将公开输出送向下游 | C06、P01–P03 |
| 6 | `OpenAIServingChat.chat_completion_stream_generator` 中的原生 parser | `parser.parse_delta` 解析 reasoning、正文与结构化 tool calls，组织 Chat Completion chunk | U05、P03 |
| 7 | 同一 serving generator 的 JSON/SSE 序列化 | `chunk.model_dump_json()`，yield `data: {JSON}\n\n`，最后按协议结束流 | U05 |
| 8 | FastAPI 路由的 `StreamingResponse`、Uvicorn ASGI/HTTP server | 以 `text/event-stream` 提供 generator，Uvicorn 将响应交给 HTTP 连接与 socket | U06、U07 |
| 9 | 请求方的 HTTP/SSE 接收与消费 | 从事件中获得 reasoning/content/tool_calls；具体客户端 SDK 不在当前服务端源码内固定 | U06、U07 所确定的协议边界 |

完整文件、函数与固定行号见 [来源索引中的第 3 页源码核对](sources.md)。
表格用于审阅，不要求把全部类名、队列和函数都画上屏。

### 单页图的层次

本页保留一张简洁的研发分布图和一根输出管道，分别说明“为什么”和“怎么做”。管道按职责压缩为以下节点：

```text
EngineCore → 跨进程传输与输出处理 → AsyncLLM.generate
           → [DTB 观察与接管] → 原生 parser / SSE → HTTP 服务 → 请求方
```

- `跨进程传输与输出处理` 下方小字容纳 ZeroMQ、EngineCoreClient、OutputProcessor；`HTTP 服务` 下方列 FastAPI StreamingResponse / Uvicorn。
- 唯一强调的新增段是 DTB。其前后的主数据流保持连贯，管道中的数据类型从 token 输出变为文本/token IDs，再变为 SSE 事件。
- 图的新增段对应路由到 DTB 的请求；明确配置为 `native` 的请求直接绕过该段，原生短请求则是进入观察后由同一 child 完成，两种情况不混称。
- 从 DTB 段向左画控制线，连接 `AsyncLLM` 的请求/控制接口，并注明请求经 EngineCoreClient 返回 EngineCore。输出方向与控制方向使用不同线型。
- 分布旁优先保留“97.19% ≤850 token”“2.81% 的长尾贡献 38.88% 的思考 token”，不再附旧图右侧的长尾放大图，也不展示完整组件表或完整状态机。
  这些比例是历史长度分布，**不是当前自动路由或实际介入比例**。

### 这段“管道”具体怎么调整后续输出

1. **未触发介入**：读取并转发增量，累计 token、识别自然 reasoning/tool 边界；沿同一 engine child 完成，不额外启动 final child。
2. **触发且可安全接管**：调用 `engine_client.abort(child_id)`。AsyncLLM 同时处理本地输出状态，并经 EngineCoreClient 发出 ABORT 控制消息；DTB 继续 drain 原 generator，接收已在途的输出和终态。
3. **进入强制 final 的路径**：保留已接受前缀，将其接入内部续写 prompt，再追加 answer cue、模型对应的思考闭合 token 和分隔符；通过 `generate()` 发起新的 final child。
4. **继续同一外部响应**：final child 的输出再次经过引擎输出链路，DTB 对齐到同一个外部逻辑响应，交给原生 parser 和 SSE generator。请求方无需发第二次 HTTP 请求。

管道图只标一般性的“阈值 T”，不将 Noemate 的 750/753 token 轨迹贴到研发的 850 分布上。
通用 DTB 可能进入 hidden probe / 继续思考，也可能 `budget_final`；不把“到 850 就直接回答”画成所有请求的行为。
Noemate **S19 的 phase 399/a2** 仍可供审阅核对直接 final 分支，但不在第 3 页展开。研发的实际 probe 接受过程见第 4 页备选 case37。

### 比喻的准确边界，绘图和讲述时保留

- “读取内容”指读取增量文本/token IDs、边界与长度。当前抓大放小不是语义重复检测，不能画成已经判断内容无用。
- **answer cue 写入内部 prompt，不是插入发给用户的 SSE 正文**。parser 所需的思考闭合边界仍需正确衔接。
- **不撤回或改写已经发出的内容**。调整的是后续生成；新 child 使用已接受 token 前缀，而不是在原 child 上原地改 prompt。
- “停止”不是收到阈值的瞬间硬切：batch、已有缓冲和 abort 尾部可能造成越过阈值；自然终止/边界可能优先完成，不能画成精确截断任意 token。
- “一小截”表达插入位置集中，不能暗示只有一个无状态过滤函数或零成本。实际实现有每请求状态、producer/consumer、输出队列和生命周期清理。
- 图的方框是逻辑处理环节，不表示每个组件都有独立进程，也不表示一个 token、一个 RequestOutput、一个 SSE 事件和一个 TCP 包一一对应。
- HTTP 层不固定 h11/httptools、TLS 或额外反向代理；按实际部署才能展开。`vllm_gateway` 是可选外部透传采集层，不画成 DTB 必经节点。

本段于 2026-09-28 按固定 vLLM 与当前未改动的 DTB 源码核对。2026-09-30 据此绘制演示示意，不新增服务器实测结论。

### 展示用数据与来源

| 指标 | 值 |
| --- | ---: |
| Vanilla-06 有效请求 | 391，含主 Agent、子 Agent、标题 |
| 思考 ≤850 token | 380，97.19% |
| 思考 >850 token | 11，2.81% |
| 全体可见思考 | 52,112 token |
| 11 条长尾的可见思考 | 20,263 token，占 38.88% |
| P50 / P95 / 最大值 | 29 / 578.5 / 3,482 token |

- 首要图源：`debug_agent/docs/report/sum0911/统计图/思考长度长尾与850阈值.png` 及同名可编辑 PPTX。
  **V01** 记录该图来源；它提供数据和原始图形参照，不原样复制旧布局。
- **S10** 的 `vanilla_chronological_requests` 保存按实际发起次序的 391 个点，已提取到 [development_distribution.csv](evidence/development_distribution.csv)。
  每行包含原 request ID、case、role、时间、思考 token 与阈值标记，并对照 **S11** 请求账本逐 ID 核对。
- 汇总见 [display_data.json](evidence/display_data.json) 的 `development.distribution_850` 与 `development.long_tail_850`；口径解释见 **S13 §5**。
- 生产观察与接管实现引用仍为 **C01、C02、C06、U01–U07、P01–P04**。

### 与 Noemate 分布图统一风格

**同意取消旧图 850 处的宽隔带，改为一条细阈值线。** 已核对旧图：它在 850 上下分别缩放，使用两条边界线和较宽空白带，视觉上像两个分开的坐标区。

按 2026-09-30 的修订意见，研发图采用**分段线性纵轴：下段 0–850 token、上段 850–3,500 token，各占一半绘图区高度**。两段在 850 处直接相接，仅保留一条细金色分界线，不留宽空白带。图下注明“分段等高：0–850 / 850–3500 token”。Noemate 仍用连续线性 0–2,000 纵轴。

- 两图统一白底、浅网格、字体、普通点的浅蓝灰色、超过各自阈值点的金色，以及一条细阈值线；不保留宽断轴带或双横线。研发短请求使用空心描边点，让重叠区域的密度更直观。
- 研发图横轴按实际请求发起序号 1–391；Noemate 图沿用实际到达时间（小时）。统一视觉语言，不强行把不同数据改成相同横轴单位。
- 两图都保留全部有效数据点，不通过抽样、排序或截掉最大值来加强长尾。阈值分别为研发 850、Noemate 750，标注各自分母与单位。
- 不沿用旧图右侧“放大 11 条长尾”的第二幅图和大块统计文字，把页面空间留给管道示意。
- 用两个原生散点图区保留真实 token 值：下段 380 点、上段 11 点，绘图区高度相同且相接。没有删点或映射数值；各段内线性，不能跨段比较视觉距离或斜率。
- 2026-10-08 在本机 Microsoft PowerPoint 放映中修复并核对两段纵轴在 850 处对齐、阈值线连续及短请求空心样式；保留 r5 手工调整的连接线。图表工作簿与全部数据点不变。
- 统一风格参考 **V02**：历史 `docs/report/dtb_closeout/evidence/figures/vanilla_distribution.png` 及同名 PPTX，作为第 4 页 Noemate 分布图依据。

### 审阅备注，不上屏

- 研发共 392 请求，其中 1 条无有效 SSE（`7774ec46a9f245c9`），分布排除后为 391；耗时总账仍保留 392，不以 391 替换第 4 页的总请求数。
- 11 条长尾中 9 条是标题、2 条是业务请求；去标题后业务为 382 条，仅 2/382 超过 850（0.52%），最大 1,885。图名保留“全链路请求（含标题）”，不将标题长尾泛化成业务推理普遍膨胀。
- 20,263 是长尾请求的完整可见思考，不能全称冗余。阈值以上部分为 10,913 token，也未经质量和额外成本扣减，不是净节省量。
- token 长度来自历史本地重编码。研发 P95=578.5 沿用 S10 的插值口径；Noemate 使用 nearest-rank。两图外观一致不代表同模型身份、采样或同口径跨实验比较。
- “同 child 完成”仍有观察/转发 CPU 成本。上述 observation 针对流式；非流式 DTB 直接进入三状态。
- 第 3 页的研发分布为 Vanilla-06，机制源码为当前交付版本；二者不合称当前代码的实测介入结果。

## 第 4 页：优化效果

### 正文与图表文案

**页标题**：历史回放与研发任务的效果。

**副标题**：Noemate 回放请求与研发任务组，分别展示请求时延和累计 LLM 耗时。

本页左侧为 Noemate 分布，右上为全部 50 对长请求回放的平均请求 E2E，右下为研发 9 任务累计 LLM E2E。正文聚焦两组耗时对比；178/a1 仅保留在讲者备注与备查材料中。

| 图表元素 | 文案 / 数值 |
| --- | --- |
| Noemate 效果区标题 | **Noemate 长请求回放（50 对）** |
| 指标 | **平均请求 E2E / 秒** |
| Vanilla → DTB 柱值 | **24.87 → 18.51** |
| 主要数字 | **−25.58%** |
| 图下样本说明 | **5 个输入 × 10 对回放** |
| 研发效果区标题 | **辅助研发 9 任务** |
| 研发指标与柱值 | **累计 LLM E2E / 秒：2,902.45 → 2,566.70，−11.57%** |

### Noemate 回放集合与复算

这组数据按测量前冻结的 178、259、319、353、399 五个输入各取 10 对，选取 `S04.pairs[group="long"]`，不混入预热、smoke 或短请求。已逐 request ID 对照 **N178S–N399S** 的 `elapsed_ms`，并核对五份 manifest 的 10 次重复、Vanilla/DTB 和交替执行顺序。

- 精确均值为 **24.8744579 → 18.51114568 秒**，50 对总 E2E 为 **1,243.722895 → 925.557284 秒**。
- 降幅按 **`(1 − Σ DTB E2E / Σ Vanilla E2E) × 100% = 25.5817%`** 计算，不用逐对百分比的均值或中位数替代。
- **43/50 对更快**；这是 5 个不同输入的重复执行，不是 50 个独立业务问题。
- 本地逐对数据见 [noemate_long_pairs.json](evidence/noemate_long_pairs.json)，各输入汇总见 [display_data.json](evidence/display_data.json) 的 `noemate_long`；固定来源和原字段见 [来源索引](sources.md)。

### Noemate 分布，作为场景背景

使用 **V02** 的统一风格分布图，与长请求回放效果区相邻。它说明该场景的思考长度与 750 介入参考，不作为整体性能收益证明。

| 指标 | 值 |
| --- | ---: |
| 有效主 Agent 请求 | 8,277 |
| 思考 ≤750 token | 7,862，94.99% |
| 思考 >750 token | 415，5.01% |
| P50 / P95 / P99 | 207 / 753 / 1,002 token |
| 最大值 | 1,841 token |

- **S01–S03、S16**；绘图点在 [noemate_distribution.csv](evidence/noemate_distribution.csv)，汇总为 `display_data.json` 的 `noemate_distribution`。
- 图保留全部 8,277 个点，连续线性纵轴 0–2,000，在 750 处画一条细金色水平线；配色、字体与第 3 页一致。
- 分布图作为 E2E 效果区的背景，不另增页面；上表为备查，不要求和完整图同时展示。

### 展示数值与备查数据

| 对象 | Vanilla | DTB | 可用表述 |
| --- | ---: | ---: | --- |
| Noemate 50 对平均请求 E2E | 24.8744579 s | 18.51114568 s | 这组回放时延降低 25.58% |
| 研发 9 任务累计 LLM E2E | 2,902.454646 s | 2,566.696963 s | 该组累计 LLM 耗时减少 11.57% |
| 研发可见 reasoning 合计，备查 | 52,112 token | 39,759 token | 减少 23.70%，包含调用轨迹变化 |

正文只展示两组 E2E，不要求把本表备查项放入页面。

### Noemate 备注示例：告警分类 line 178 / attempt 1

此例仅供讲者备注与输出备查，不承担集合效果结论。E2E 为 24.794401→18.603930 秒，下降 24.97%；可见 reasoning 为 1,237→753 token，final 保留全部 12 条告警分类字段，严重/重要/次要各 4 条。

- 输入：“按告警级别分类”，`case_id=e1990c9fd1fb464d90f7d8020449117c`。
- 回放目录：`noemate_guardrail_main_20260922T070000Z/long_l178_measured`，attempt 1，`off`，流式。
- Vanilla ID：`dtbtool-l178-a1-vanilla-main_20260922T070000Z_long_l178_measured`。
- DTB ID：`dtbtool-l178-a1-dtb-main_20260922T070000Z_long_l178_measured`。
- 首要引用 **S06**：历史 `docs/report/dtb_closeout/evidence/cards/l178_a1.json` 及同名 Markdown；**S09** 原请求；**N178S / N178M** 原 summary/manifest。
- 本地素材：[noemate_cases.json](evidence/noemate_cases.json) 的 `cards[0]` 保留两侧完整 reasoning/final、参数、ID 和审核结论；[alarm_reference.csv](evidence/alarm_reference.csv) 是 12 条输入真值与 message 序号。
- 可摘出的三条记录：`ALM-2001` 严重、`ALM-2002` 重要、`ALM-2003` 次要。完整结果仍按全部 12 条核对，末尾一般建议不解释为已执行运维动作。

### 确定的研发汇总，以及可选早退记录

**汇总选 Vanilla-06 对 DTB run-08**，引用 **S10、S11、S13 §2、S15**：

- [development_tasks.csv](evidence/development_tasks.csv) 为 18 行逐任务双侧数据；总账在 `display_data.json` 的 `development`。
- 按 case 配对 9 个相同主任务输入，纳入主/子 Agent、标题、失败和恢复请求。Vanilla 共 392 请求，run-08 共 350 请求。
- LLM E2E 是各请求 `metrics.e2e_ms` 之和，不是平台整项评测 wall time。

**如需展开研发早退过程，选 case37 的报告阶段**，引用 **S13 §3、S20**：

- 来源是实际 Agent 执行记录，**不是独立 HTTP 回放**。run 为 `run-06-closed`；request `0480aca87728475f`；parent `chatcmpl-a385fe220c683c95`。
- 原始 `full_log/proxy_session_795d073fadb0/part-00001.jsonl` 第 20 行，第 21 行可核对后续工具返回。S20 提供完整仓库路径。
- 提取到 [development_case37.json](evidence/development_case37.json)：思考 850 token / 9.769065 s，hidden probe 50 token / 1.415741 s，final 174 token / 2.210565 s，请求 E2E 13.843899 s。
- 可用原文：开头 `I see the results:`、`- Tests run: 3`、`- Failures: 1`、`- 2 tests passed:`；之后 `Let me count: That's 31 indices total`、`I'm getting 31 items total`。
- 早退后仍保留 2/3 测试结果并继续报告流程，只说明停止机制，不声称根因诊断完全正确或提高代码正确率。
- 不为此例展示“节省 55.45%”：历史 Vanilla 同阶段输入不同，非严格同输入配对。

### 审阅备注，不上屏

- **集合定义**：capture 中主 Agent 且 `exclusions=[]`、`reasoning_tokens>750` 的 415 条，仅表示历史长尾候选。正文的 off 50 对有同轮 V/DTB 耗时，却没有请求级 phase，不能按 `>750`、`finish_reason=stop` 或约 750 的最终长度确认真实早退。因此正文称“长请求回放”，不称“已触发早退集合”。以下证据与质量问题仅留在大纲审阅及讲者备注，不新增正文或页脚声明。
- **不推荐 48 对长度代理作为主指标**：50 对中 48 次 DTB 本地重编码思考为 750–753 token；另两次是 353/a4 的 513 和 399/a1 的 533。事后筛 `750<=d_reasoning<=753` 可得到 48 对、均值 25.045980→18.655950 秒、降幅 25.5132%，但该筛选不能证明接管发生，故不以“早退请求加速率”展示，也不替代冻结的全 50 对。
- **实际接管集合没有可比加速率**：S19 的 `all_requests` 中，12 次 phase 请求有 7 次 `intervention_status=triggered`、`abort_count=1`、`post_drain_branch=budget_final` 且存在 `final_answer`；其余 5 次为 `native_complete`。7 次均为预算耗尽后转 final、没有 confidence probe，不称为置信度接受早退。**S25** 的 `variants=["dtb"]`、**S26** 的 `all_pairs.pairs=0`，确认该轮没有 Vanilla 对照；不得把该轮状态套到 off 中相同 line/attempt，或跨 off/phase 计算加速率。
- **早退定义**：算法主动停止思考并转 final，须有实际分支证据；仅到达阈值或进入 probe 不充分。C06 的自然完成、`model_boundary_final`、`confidence_probe` 与 `budget_final` 分支需分开；若是 probe 路径，还要核对接受后确实进入 final。更早名称含 phase 的 2026-09-21 目录实际仍为 off，**S30** 已记录日志未开启，不能补作触发证据。
- **50 对质量与完成量**：冻结集合保留全部慢请求、失败、截断和动作变化。178 的 6/10 final 遗漏 4 条严重告警；S05 中完整分类字段为 a1/a2/a5，a2/a5 有错误建议，a6 有草稿/闭合标记。399 的 a1/a4 总和错误、a2 未完成聚合，其余 7 次仅数值项核对通过，日期问题仍在。319 独立内容审阅待完成；353 的 DTB 有 4/10 `length`、一次转为 Rag 工具调用，不能当作同等业务完成量。全 50 对 V/DTB 的 `length` 为 9/4、工具调用为 0/1；自动协议异常均为 0 不等于业务正确。依据 **S05、S24、S31、S32**，不把 25.58% 写成整体质量非劣或全流量收益。
- **50 对选样与运行口径**：这是测量前冻结的小集合，来自此前探索中的四个长输入及一个算术边界输入（S33），不是随机或独立 holdout。单并发、原采样与总 completion 额度、seed=20260918、fixed check/budget=750、顺序交替，各例紧接预热；正文长度和工具动作仍有变化，E2E 差不能全部归因于删去冗余思考。补充固定来源见 [来源索引末尾](sources.md)，本次只做历史数据复算，没有服务器重跑。
- Noemate 捕获共 12,214 条：主 Agent 8,306、追问推荐 3,897、compaction 11。主 Agent 排除 29 条后得到 8,277；排除项为 12 条 length/其他终止、16 条 reasoning 不可观测、1 条解析错误。
- Noemate 分布沿用主 Agent 且 `exclusions=[]`，长度为本地重编码 reasoning；基线标签缺完整服务端 native 开关和权重身份，不能称当前部署的原生行为证明。
- Noemate >750 请求占可见思考的 16.34%，超阈值部分占 2.93%，均非安全可删或净收益比例。不与研发 850 阈值、391 分母或 38.88% 混用。
- **S19 的 phase 399/a2** 仅供机制审核：阈值 750、实际前缀 753、abort 1、drain 新增 0、final 168、无 probe；见 [noemate_phase.json](evidence/noemate_phase.json)。它与 off 的 178/a1、399/a10 是不同请求，不拼接成一次实验。
- Noemate 成功例是 10 对重复中的事后选例，同输入另有失败输出，不能推出整体质量不下降。
- 178/a1 正文长度也从 738 变为 727 token；E2E 差异不能全部归因于 reasoning 缩短，off 无 hidden probe 总工作量账本。
- 研发最终 DT 全通过任务按显式补充原始证据为 4/9→4/9，但 case36 为 4/4→3/4，case2 从编译失败变为 6/6。Vanilla case35 缺报告，36/36 来自原始测试。
- case29 测试范围为 8→87。总数持平不等于逐任务非劣，正文不展示“无劣化”。
- 平台 start→end 合计 4,299.173407→4,129.362689 s，减少 3.95%；create→end 合计减少 2.17%。不要改写为用户整项等待时间缩短 11.57%。
- run-08 缺完整部署 manifest、未显式固定 seed、使用历史 Vanilla。观察结果不能当作严格受控因果收益。
- case37 phase 合计 13,449.166 ms，与客户端 E2E 差 394.733 ms；不归因到未测环节。已记录 1,074 生成 token，但 `model_work_complete=false`，只是工作量下界。

## 第 5 页：交付产物

### 建议正文

| 交付 | 已提供内容 | 后续使用 |
| --- | --- | --- |
| vLLM 集成 DTB | 模块化 controller、预算与路由、原生观察及状态机、三份部署 patch、通用采集网关、replay 验证入口 | 按部署手册应用成套代码和 patch，在目标服务验收 |
| 独立思维链观察器 SDK | 增量观察接口、每请求检测状态、合法 stop/abort/drain、可选 cue 引导 final，与 DTB 算法解耦 | 接入重复检测等自定义算法，验证触发准确性与净收益 |

**可维护性**：README/AGENTS、模块导航与验证文档提供统一入口，便于人和新 Agent Session 定位实现与边界。

**后续扩展**：在观察器接口上开发重复检测，在检测触发后按合法边界停止思考并续写答案。

### 来源与版本

- **C01–C04**：`codex/dtb-static-refactor`，本稿代码快照 `f5512aa35152a13a9ee164a64928674badc26ceb`。
  入口 `dtb/`、`vllm_gateway/`、`patches/vllm/v0.23.0/`。已合入 system 路由 `34f39983`，行尾修复 `0777aef4`。
- **O01、O02**：`codex/thinking-observer`，固定提交 `7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d`。
  入口 `thinking_observer/README.md`，接口在包内 `docs/api.md`。
- 分别列出两个已有交付入口，本轮不将 SDK 实现复制进 DTB 交付树。

### 审阅备注，不上屏

- C04 的历史本地记录：541 项测试、1,966 子测试和三份 patch 可应用性通过。此次文稿取数未重跑这些测试，当前真实模型、并发和服务器 E2E 仍为 `pending_remote`。
- O02 的历史本地记录：34 项单测、7 组 parser 源码场景、8 条 patched serving 路由及独立打包通过，均非真实 HTTP/模型/Ascend 性能验收。
- SDK 没有实现重复检测算法；`TokenLimitDetector` 仅是接线示例，不宣称检测准确率或收益。
- “立刻输出”改为“在合法边界停止并续写答案”：不撤回已发文本，保留整个已接受 batch 与 abort 尾部，有检测、abort 和 final 的实际成本。
- SDK serving patch 与 DTB serving patch 是独立入口，不直接叠加部署。

## 内容审阅备忘，不制作演示页

### 数据来源与复算

- 历史来源固定 `debug_agent` 的 `32ab2268335e00d79e0aeda4395c6ecdc06d81eb`，主要引用该提交的 `docs/report/`，必要时下钻到原 summary、manifest 和请求账本。
- [来源索引](sources.md) 提供不可变 Git 链接与字段/段落定位；[source_manifest.json](evidence/source_manifest.json) 记录原始 Git blob SHA-256 与提取结果哈希。
- [提取脚本](sources/extract_evidence.py) 使用 `git show <固定提交>:<路径>`，不切分支、不创建 worktree、不恢复大目录、不发送模型请求。
- 本轮所选材料在 Git 中已足够，**未使用仓库外备份**，不依赖未提交的 replay pool。178 原请求已由历史报告独立保存。
- 展示使用精简证据，复算需要 Git 历史及用户参考 PPT。命令和验证范围见 [evidence/README.md](evidence/README.md)。

### 失败样例与限制

| 事项 | 精确定位 | 对正文的约束 |
| --- | --- | --- |
| final 遗漏严重组 | S07，178/a3；`noemate_cases.json` 的 `cards[1]` | 只保留 8/12，不用 reasoning 里的记录补成 final 成功 |
| 建议错误与草稿泄漏 | S05，178 全部 10 次审核 | 3/10 有完整分类字段，但 a2/a5 有建议错误，a6 有草稿/闭合标记，不是全量质量通过 |
| 算术正确但日期非“今日” | S08，399/a10；`cards[2]` | 4 个值合计 338.001、均值 84.50025，仅证明返回数据聚合，不选为主成功例 |
| 319 质量待审 | S05、S18，long_l319_measured attempts 1–10 | 10/10 更快、均值 40.662→22.476 s，仍不作为无损成功例 |
| 研发质量集合变化 | S13 §2、S15 §4 | 4/9 持平不能变成逐任务非劣 |
| 短请求非劣未闭合 | S18 的 B03_short | 不声称 Noemate 短请求零开销或全体不变慢 |

### 待验证项

1. 当前交付 commit 与 system 自动路由的服务器功能、并发、质量和 E2E，按根 RESULT 与 replay 手册执行。
2. 整体“质量不退化”需要预先固定完整质量标准并审核全样本，不能以单个成功例代替。
3. 如要把收益归于本次静态重构，补同一服务器、同配置、同负载的重构前后对照。
4. SDK 的真实模型、HTTP/SSE、设备行为、检测算法与净收益另行验收。

### 演示稿使用

演示稿采用 Noemate 全部 50 对长请求回放和研发 run-08 汇总。178/a1、case37、失败样例、完整参数及源码组件表留在本稿与讲者备注，不另增演示页。五页原灰色页脚说明均已移入备注，正文保留必要的样本定义与单位。
打开 [演示稿 r9](presentation/DTB_closeout_20260930_r9.pptx)；编辑与重建入口见 [使用说明](presentation/README.md)。
