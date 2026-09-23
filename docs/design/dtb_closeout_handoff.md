# DTB 收尾与多 session 交接清单

## 1. 目标与当前基线

整理日期：2026-09-22。源码/原始结果基线为 `884a00a54faf65fa5daca191943677fd2835e8be`；
884a00分析产物及本清单已由 `f342e1276a0d8b9c8b2c02f57917b19be936b799` 提交，其他 worktree
可从该提交读取。下文“先提交分析快照”的准备动作已完成，不代表 A–E 后续任务已经完成。

**用户最新决策优先于此前阶段建议：** 当前收尾需要找到并解释“压缩后正确完成”的请求，不以修复全部Noemate反例或证明所有短请求严格非劣为端到端测试前置门；服务器端到端测试同步推进，数据集尚未确定。暂不继续完整池扫参，也不把新增cue实验作为必做项。

汇报应证明三个不同层次：

1. **算法与工程能力**：按需介入思考、支持probe/直接final、保持原生解析与请求生命周期。
2. **已经取得的案例证据**：长请求有可重复的时延压缩，并选取语义正确且final自包含的实例说明早退有效。
3. **实际任务结果**：服务器端到端结果单独回填，不用Noemate单请求重放替代任务通过率或端到端收益。

“有成功案例”是当前展示目标，不等于总体质量非劣；已知错误/截断留在局限和失败样例中，不从原始统计删除。展示成功样例时标明为选样，不将其成功率推广至全量。短请求同输出37对平均差约+0.10ms可以作为支持性数据，不能改写为所有短请求零开销。

已有证据入口：

- [60f2d7采集分析](../log/perf/noemate_analysis_60f2d7/RESULT.md)：按时间的原始分布、请求类型、完整池及试点来源。
- [7249b3回放分析](../log/perf/noemate_replay_20260920T012159Z/RESULT.md)：probe/阈值探索及缓存顺序问题。
- [4c259c小集分析](../log/perf/noemate_direct750_off_20260921T064359Z/RESULT.md)：长短集合冻结过程。
- [真实phase诊断](../log/perf/noemate_activation_audit_20260922T031321Z/RESULT.md)：7次budget_final、零probe及5次原生同child完成。
- [884a00正式off小集分析](../log/perf/noemate_guardrail_main_20260922T070000Z/RESULT.md)：319平均快44.73%、10/10更快、232次DTB可见思考≤753及语义/短请求限制。
- [DTB实现说明](../../dtb/README.md)、[性能诊断文档](../debug/deer_performance_analysis.md)、[实验流程](think_budget_generalization_plan.md)。

## 2. 建议拆成五个 session

| session | 独立交付 | 依赖 | 修改范围与提交边界 |
| --- | --- | --- | --- |
| A 自动场景配置 | system身份识别、scene预算/原生动作、配置与部署手册 | 复用已有system路由子任务 | dtb路由/config、必要serving patch、对应tests及设计说明 |
| B 汇报数据与成功轨迹 | 时间分布图、配对对比图、人工可读轨迹卡、证据索引 | 现有capture/replay，无需等E2E | docs/report/dtb_closeout/evidence/与生成脚本；先提交当前分析快照 |
| C 端到端测试与结果归档 | 测试manifest、完整性/质量/E2E结果、汇报数据接口 | 当前服务即可开始；自动配置结果单列新run | docs/log/perf/<实际run>/，测试执行/分析脚本 |
| D 思维链观察器SDK分支 | 算法无关观察/接管接口、重复检测集成示例、独立patch及文档 | 独立于A/B/C，不等system匹配 | **专用分支 `codex/thinking-observer`**，建议独立worktree |
| E 最终汇报PPT | 算法、实现、优化、证据、E2E结果的完整叙事 | B为素材；A/C补结论；D作为扩展能力 | docs/report/dtb_closeout/slides/，PPTX/PDF及生成源 |

A/B/C/D可并行；E先做讲述结构与占位页，最终版等待B和实际E2E结果。若C尚未返回，只交付明确标注pending_remote的草稿，不把占位数字当结果。D是独立二次开发交付，不要求合入生产DTB主线，也不阻塞主线PPT。

### 并行前一次性整理

- 先将本轮884a00分析文件、两份RESULT更新、debug变更记录及本交接清单做一次独立提交（可由B首先完成），记录SHA；不要连带提交后来其他session的代码。
- A沿用正在负责system识别的子任务，或读取其最新提交后继续，避免启动第二份同功能实现。
- 之后使用各自worktree/分支。A与D都会触及serving/context代码，不能在同一checkout同时切换分支或修改。
- 每个session只提交自己负责的文件，并在交付说明给出commit SHA、测试、实际/待服务器验证边界及需要其他session接收的文件。
- B/C提供数据，E消费数据；图上数字由脚本/数据索引产生，禁止PPT里手改一份新数字。debug总变更记录集中在各成果合并时追加，避免并行重写同一大文件。

## 3. Session A：自动配置与system场景识别

**目的：** agent无需手动给每次请求传fixed参数，系统按配置识别身份并选择DTB预算或真正原生生成。

已存在 [System场景与原生路由设计](dtb_system_scene_native_routing.md) 和 [Agent配置初稿](dtb_agent_scene_budget_plan.md)。
**2026-09-22 Session A 已实现并完成本地验证**：system_prefix 匹配、真正 native 分流、Noemate
候选与 12,214 请求离线路由审计；交付入口为 [部署/回滚手册](dtb_system_routing_deployment.md)。
服务器自动 phase/off 和任务 E2E 仍为 pending_remote；原 fixed 实验不作为自动模式验收。

交付内容：

- 从原始类型化messages取有界system身份视图，复用现有路由模块，规则启动时校验/编译；不扫描整段历史正文、不每chunk分类、不调用模型分类。
- scene、action、预算分离；action=native应回原生serving分支，不用无限大预算模拟旁路。保持legacy默认兼容、fixed覆盖语义，未知/冲突模板有明确处理。
- 从真实Noemate捕获请求提取主Agent与辅助请求模板，给出命中样例/拒识样例/覆盖率。system身份相同但任务难度不同，不宣称身份匹配能自动判断“此题750够不够”。
- 给出可直接部署的候选配置；750只能标为该模型/场景的已测候选，不全局默认覆盖未知数据集。保留更保守配置选择，不强制新增阈值扫描。
- 自动模式测试去掉有效fixed覆盖；单独小phase验证命中、来源、实际动作，再用off做服务器E2E。旧fixed结果不能当作自动路由验收。
- 配置schema/loader、优先级、未知形状、原生旁路、流式/非流式及兼容回归测试；部署/重启、配置SHA、回滚到旧配置/功能关闭说明。

完成标准：已实现接口和YAML行为一致；无需用户逐请求标注即可路由；自动/原生动作可审计；本地验证与pending_remote项分开。

**可复制任务提示词：**

> 阅读docs/design/dtb_closeout_handoff.md的最新目标，负责Session A。接续已有system场景识别子任务，依据dtb_system_scene_native_routing.md完成system身份匹配、自动scene预算和真正native旁路，提供Noemate候选配置、命中/拒识审计及部署回滚手册。先确认已有工作避免重复实现；不修改早退算法/cue，不把750推广到未知场景。自动验证不得被fixed overrides绕过。做必要本地测试，服务器验证未执行则标pending_remote。仅提交本任务代码/文档，交付commit SHA及接口摘要。

## 4. Session B：分布图与早退成功样例

### 图表和来源

建议至少三组图，均交付生成脚本、源数据表、PNG及SVG/PDF：

1. **原始流量时间分布**：按请求到达时间画可见思考token散点和ECDF/直方图，分主Agent/辅助请求，标记750等参考线；显示有效样本分母、未支持/解析异常数。请求不强行归session；历史messages能重建的轨迹另列可信关系。
2. **冻结长短集配对结果**：319等长例的Vanilla/DTB长度与E2E逐对图、短集的首输出/全样本E2E与同输出敏感性。完整池、冻结小集、成功选例分别标识，不能把三种分布混画成全流量收益。
3. **机制轨迹图**：native observation→触发→abort/drain→final，标明actual phase的token与时间；主线probe分支另画概念图。phase示例与off时延例若不是同一请求，必须分别注明ID/运行身份。

使用标准绘图库作可导出科学图，不凭肉眼重画数据。原始代理到达时间可画时间轴；回放chunks没有逐token精确时间戳时，只画token位置，不能生成伪token时间曲线。

### 成功轨迹卡

每张卡包含：原始问题与必要工具结果、case/request ID、两侧参数与E2E、思考长度、信息何时已足够、阈值附近片段、final正文与语义检查、来源文件SHA。长度足够不是自动语义正确；不能仅因API stop或更快就入选。

优先候选：

- 178的DTB attempts **1、2、5、6**：final包含12条ID，可继续核对级别/名称/时间是否正确，用来说明分类信息已就绪；不要拿有遗漏的3/4/7/8/9/10作为成功例。
- 399的DTB attempts **3、5、6、7、8、9、10**：数值项正确；继续检查表达与源数据日期，挑实际可见思考约750且有净收益的一对。
- 319：收益最强，需按输入知识核对告警清单后再选成功对，不能先认定全部正确。
- 真实phase中178或399的budget_final可作为机制证据；和off成功例不同时，不冒充同一条轨迹。

交付2–3个语义说明清楚的成功样例即可，再留1张已知局限卡。候选列表不是正确性认证；证据不足就换候选或标待审，不造参考答案。需要服务器本地模型长上下文审阅时输出完整请求和检查表，由服务器执行，结论回填。

汇报数据接口建议固定为 `evidence/index.json`：`claim_id`、陈述、证据强度、source_run/request_ids、分母/选样方式、指标单位、source_sha、图文件、语义检查状态与限制。E只引用已核对条目。

**可复制任务提示词：**

> 阅读docs/design/dtb_closeout_handoff.md，负责Session B。先按文件边界独立提交当前884a00分析快照与交接清单，记录SHA供其他worktree使用。随后整理DTB汇报数据，生成按到达时间的思考长度分布、冻结长短集配对图、phase机制轨迹图；收集2–3个压缩后final正确且自包含的轨迹卡，优先核验178成功候选、399正确聚合候选及319清单。保留已知失败说明，不宣称全量质量非劣。产物放docs/report/dtb_closeout/evidence/，包括可复现绘图脚本、可编辑图和index.json证据索引。本地无法语义裁决的大上下文样例准备服务器审阅材料。仅提交本任务产物并交付SHA。

## 5. Session C：端到端测试同步推进

不等待B的成功例或短请求统计完全闭合才开始。数据集未定时先准备归档模板和分析入口；**不要擅自把411回放池当作最终端到端数据集**。由服务器实际测试确定数据集、任务成功标准、并发与到达方式并保存。

最小归档：

- 模型/权重身份、TB与vLLM/vLLM-Ascend源码SHA及dirty、安装版本、启动命令、YAML/patch hash、observability、APC、sampling/seed、并发、数据集hash与变体。
- 同身份Vanilla与DTB任务级结果；任务ID→LLM request ID关系；任务端到端时间与累计LLM E2E分开。工具执行时延、额外工具轮数影响实际任务收益，不能只看模型usage。
- 成功/失败/未判定的定义、工具协议错误、空final与截断；实际有参考答案则记录Judge版本/人工覆盖。没有参考答案时明确能检查的结果，不能以HTTP200替代成功。
- 正式off性能与独立phase诊断分开；若测试时A尚未完成，记录fixed/旧配置身份，A合入后自动模式另建run，不覆盖前轮结果。
- 结果归档后按仓库标准生成RESULT；将可汇报条目提交B/E，E2E尚未返回时保持pending_remote。

**可复制任务提示词：**

> 阅读docs/design/dtb_closeout_handoff.md，负责Session C。支持服务器端到端测试同步开展，不以前轮Noemate个别失败或短请求非劣未证明为阻塞条件。数据集由实际服务器测试确定；先准备实验身份/质量定义/数据归档模板和可复算分析入口。获得结果后区分任务E2E、累计LLM E2E、工具轮次、任务成功与未判定，使用同身份Vanilla配对；off与phase分开，fixed与自动路由run分开。结果写docs/log/perf/<run>/RESULT.md并向汇报提供证据索引。本机无法运行服务则明确pending_remote，不能假造通过。仅提交本任务产物及SHA。

## 6. Session D：独立思维链观察器分支

### 6.1 目的和边界

在专用 `codex/thinking-observer` 分支提供给重复检测算法开发者：复用DTB经过验证的vLLM观察、边界解析、abort/drain、final续写及生命周期；去掉预算/probe/置信度/scene/history的运行时依赖。首版是可二次开发的接口与接入示例，**不负责研发或宣称重复检测算法的准确率**。

建议交付独立 `thinking_observer/` 包和独立serving patch，包可在不安装/导入dtb算法模块时运行。历史DTB结果/文档不需要在当前主线删除；分支交付物不再依赖整套DTB。是否在分支保留历史目录仅为维护选择，不应影响SDK可独立安装/测试。

### 6.2 复用清单：按能力提取，不能直接整文件搬运

| 当前来源 | 保留/提取内容 | 去掉的DTB耦合 |
| --- | --- | --- |
| `reasoning/deferred_observation.py` | 原生观察、DELTA校验、能力拒绝条件、abort/drain、自然终止抢先处理 | 750等阈值、activate_budget、probe可用性及状态跳转 |
| `reasoning/parser_boundary.py` | 原生reasoning/tool边界绑定、text/token连续性、一次性closure | 只属于DTB阶段的配置；不能删掉结构边界保护 |
| `reasoning/request_context.py`、`request_state.py` | prompt/token快照、completion额度、engine上下文、输出队列、取消/terminal、必要计数 | DtbBudgetDecision、DtbAlgorithmConfig、TrialObservation、probe/history字段 |
| `reasoning/generation_states.py` | 仅提取final prompt组装、answer cue、剩余completion额度、最终续写 | Thinking/ConfidenceProbe三状态、概率裁决、迭代预算 |
| `request_controller.py` | 生命周期、producer/consumer清理、流式/非流式原生response适配 | 路由、budget resolver、history配置与构造；需要新薄门面，不能直接保留Dtb facade |
| `integration/vllm/*` | 原生输入/输出parser集成及priority/LoRA/trace/DP等透传 | response_adapter当前引用DtbRequestContext，需改为中立协议 |
| `observability/*` | 可选观察/决策/接管计时及child账本，off保持低开销 | scene/confidence/budget专属事件；定义新命名及schema |
| `config/*`、版本化patch | 最小开关、能力声明、cue/telemetry及vLLM接入 | DTB schema、fixed_*、预算YAML及专属请求字段 |
| `budgeting/`、`history/`、confidence/token_filter模块 | 不进入观察器SDK依赖 | 删除其运行时引用；token_filter当前服务置信度，不是通用边界层 |

当前源码确有传递依赖：request_context/state引用预算类型、response_adapter引用DTB上下文、观察状态继承generation_states；“只拷贝deferred_observation.py”不足以成为独立SDK。

### 6.3 最小接口提案（由D细化并实现）

- **ObserverContext**：不可变请求身份、模型/边界协议、completion限制；tokenizer按服务长期复用，算法状态按请求隔离。
- **ReasoningDelta**：新增reasoning token IDs、对应文本片段、reasoning绝对token偏移、当前已输出位置、是否遇原生边界；不每chunk复制整个prompt/全文。文本与token边界不同，不以字符数充token位置。
- **Detector**：`start(context)`创建请求状态；`observe(delta)`返回 `CONTINUE` 或 `STOP_AND_ANSWER(reason, detected_at, repeat_start?)`；`close(outcome)`清理。事件在同一请求内有序、无并发重入；检测器不能自行调用engine或操控parser。
- **Handoff**：框架负责停止观察child、drain、解决自然终止竞态、按已接受前缀拼cue并关闭reasoning、在外部剩余额度内生成final。停止只触发一次，不运行hidden probe。
- **诊断字段**：检测位置、重复起点（如算法提供）、实际接管位置、已对外输出位置、尾部超出、检测成本、abort/接管成本和final结果分别记录。

调用生命周期：每请求一个detector state；仅在reasoning期间观察，未检测重复则原生同child生成到结束；重复后由框架接管一次，正文/工具调用不再喂给重复检测器。关功能时走真正原生分支，不构造detector。

关键取舍：

1. **位置语义**：已经流给客户端的token不能撤回。首版默认在检测后的合法batch/drain前缀处接管，记录repeat_start与actual_handoff的差；不能承诺删除已经输出的重复段。若业务必须精确在较早重复起点切断，需另设计有界holdback缓冲及text/token对齐，明确首输出/延迟代价，不能悄悄改prompt前缀与客户端已见内容。answer cue通常进入内部续写prompt，是否对外显示沿原生reasoning闭合契约处理，不直接把内部cue塞进最终正文。
2. **检测成本与失败**：回调必须增量且成本有界；不能在事件循环每token全量重算或同步网络请求。昂贵异步检测需明确队列容量、过期决策、回压及可见前缀规则。普通detector错误可记录并关闭本次检测、继续安全原生路径；取消必须传播，框架/parser错误不能吞掉或重新启动原生请求。
3. **能力覆盖**：对外stream=true/false都要给出明确支持路径，尽量复用内部DELTA观察并让非流式继续走原生完整parser；不支持的模型/采样参数显式原生回退并记原因，不能宣称介入生效。不把当前DTB非流式预算状态机整套带入SDK。

### 6.4 交付与验证

- 独立包、public API、一个不触发detector、一个确定阈值测试detector、一个简单重复片段示例（仅演示接入，不代表算法准确性）。
- 固定vLLM v0.23.0 / vLLM-Ascend目标的独立patch、最小启动例、算法替换步骤、保留/移除依赖图、API/线程与生命周期说明。不能同时叠加DTB旧serving patch与观察器替换patch而不说明冲突。
- 无DTB预算/probe模块的导入/安装检查；原生无触发、触发直接final、自然边界赢过检测、batch超出、tool结构、空delta、completion耗尽、取消、异常、并发状态隔离、流式/非流式测试。
- 服务器runbook：无触发对照开销、重复样例接管、raw parser契约、并发取消；本地测试只证明CPU/协议行为，不宣称目标设备性能。安全能力不复用旧算法成功率。
- D分支不混入A的scene匹配、不因删除DTB而修改上游只读checkout；可通过模块依赖表快速指导外部算法集成。

**可复制任务提示词：**

> 阅读docs/design/dtb_closeout_handoff.md，负责Session D。在独立worktree创建codex/thinking-observer分支，基于已提交的DTB基线提取算法无关思维链观察器SDK及独立vLLM patch。目标是外部重复检测器增量观察reasoning；未触发保持原生同child生成，触发后由框架abort/drain、拼answer cue、直接final，不保留DTB预算/probe/置信度/scene/history运行时依赖。先按文档模块表完成依赖与接口设计，再实现最小可用包、示例detector、集成/迁移手册和测试。明确重复起点、检测位置与实际接管位置；不得撤回已流出内容或破坏原生parser/取消/completion契约。非流式路径明确支持或回退范围。无需研发重复检测算法本身，服务器性能标pending_remote。提交仅位于专用分支，交付SHA、安装说明和测试边界。

## 7. Session E：最终PPT

先建立故事线，最终数字只从B/C索引读取。建议约10–12页，按内容适当调整：

1. 问题与目标：长思考占用、短路径代价、控制可用性；Noemate缺少极端长尾的事实。
2. 原始请求时间分布及为何关注少数值得优化的请求。
3. DTB算法：观察→介入→probe/置信度→final，direct-budget作为明确特例。
4. 参数含义：介入阈值不等于总预算；750=750为何直接final；不要讲成关闭num_thinking。
5. vLLM实现：token边界、原生parser、abort/drain、completion统一额度、取消及透传。
6. 优化：短路径延迟决策/避免额外生成，长路径直接final，APC与日志off/phase分离。
7. 自动场景配置：A实际交付的识别/原生旁路；尚未完成时标待实现，不照设计当事实。
8. 语义成功轨迹：1–2张有配对时延和正确final的卡。
9. 长请求压缩与短路径数据：区分全样本、同输出子集、选例。
10. 端到端结果：C的真实测试集与质量/E2E；未到则明确占位。
11. 能力边界与失败例：已知遗漏/算术/额度问题、算力账本边界；简明不夸大。
12. 复用与后续：观察器SDK供重复检测集成，明确与生产DTB是两份交付。

交付可编辑PPTX、PDF预览、生成源、证据页/讲稿；逐页渲染检查裁切、中文字体和图表可读性。仅准备交接不必现在生成PPT；E执行时使用presentation技能。

**可复制任务提示词：**

> 阅读docs/design/dtb_closeout_handoff.md，负责Session E。先组织DTB算法、vLLM实现、优化历程、自动场景配置、正确早退实例、长短请求数据和端到端结果的汇报结构。数据从docs/report/dtb_closeout/evidence/index.json和实际E2E RESULT读取；已有事实、设计、pending_remote严格分开。服务器结果未回时做明确占位草稿，回来后定稿。解释成功选例，不推广为全量质量非劣或零开销。使用presentation技能生成可编辑PPTX、PDF、生成源与讲稿，逐页渲染验证，放docs/report/dtb_closeout/slides/，仅提交本任务文件并交付SHA。

## 8. 还需补齐的收尾项

这些并入上述session，不额外开一轮大实验：

- **可复现版本包**（A/C/D）：部署代码/配置/patch/模型身份、重启方式、功能关闭与回滚；新自动配置与旧fixed结果不混淆。
- **证据索引与发布前检查**（B/E）：每张图/成功卡可定位输入、request ID、分母和脚本；投屏材料按受众处理不必要的内部地址/路径，审计原件保留。
- **成功定义**（B/C）：本轮只需有可解释的成功压缩例，但具体样例仍要核对final；E2E的成功标准由实际任务定义，不将推荐问题/对话压缩混作主任务成功率。
- **核心可靠性回归**（A/D）：取消、错误、工具parser、外部额度和非流式；这些是接口交付的基本要求，不是要求所有问题都答对。
- **统一收口**：汇总五个session的SHA与pending项；主线合并A/B/C/E，D保留独立分支/制品。任何尚未返回的服务器结果明确挂起，不凭历史数字补齐。

## 9. 变更记录

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-09-22 | 从严格小集前置门调整为成功案例证据与端到端并行；拆分五个session，增加观察器SDK提取边界、接口位置语义、部署/证据/提交交接 | 用户明确收尾目标及二次开发交付，避免继续把定向失败修复作为所有测试的阻塞条件 |
