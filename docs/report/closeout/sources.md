# 汇报引用源索引

## 第 1 页紧凑背景与框架改写（2026-09-30）

已实际查看 R01 原页，继承“背景与问题、核心思想、实现方式、整体架构”的组织方式，框架按当前服务内实现重画。
源码仍固定到 `f5512aa35152a13a9ee164a64928674badc26ceb`，本轮不修改实现。主图只包含以下职责：

| 图中职责 | 源码定位 |
| --- | --- |
| 场景识别、native 分流 | P01 `request_controller.py:128–173`；C03 |
| 阈值、步长、上限配置与裁决 | P05 `budget_resolver.py:62–78,157–214,336–358`；完整预算在流式观察触发后裁决 |
| 原生生成与观察、短请求自然完成 | C06 `deferred_observation.py:128–146,226–305` |
| 隐藏试答、token 概率评分、继续思考 | C05 `generation_states.py:401–428,531–584,613–668`；C08 |
| cue 引导最终生成、原生 parser 输出 | C05 `generation_states.py:758–780`；P03 `response_adapter.py:73–101` |

history/difficulty 已实现但默认关闭（C07），不放主图。原图中的在线学习最优预算、信息增益、语义重复/工具循环检测、熵变化、人工接管、Planning/反思/Tool 独立子预算不列为当前实现。
参考材料中缺乏同条件原始对照的 4 倍、40% 和耗时数字不上屏；问题动机保留定性表述，S13 的 case37 支撑“已有信息仍反复推敲”。

## 第 3、4 页分布图的分工与风格（2026-09-30）

第 3 页采用辅助研发 Vanilla-06 的 391 条分布，第 4 页采用 Noemate 的 8,277 条主 Agent 分布。
下列两张历史图已实际查看；交付演示稿依据精简点数据重画，历史源图不作修改。

| ID | 固定图源 | 本稿使用方式 |
| --- | --- | --- |
| V01 | [研发思考长度长尾与 850 阈值](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/sum0911/统计图/思考长度长尾与850阈值.png) | 第 3 页数据与旧图参照。同目录有同名 PPTX；下段 0–850、上段 850–3,500 各占一半绘图区，850 处一条细分界线；无宽隔带或右侧放大图 |
| V02 | [Noemate Vanilla 思考长度分布](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/figures/vanilla_distribution.png) | 第 4 页图源与两图统一风格参照。同目录有同名 PPTX；连续线性轴、浅色普通点、金色长点、细 750 阈值线 |

V01/V02 的 PNG Git blob 哈希已纳入本轮更新的 `evidence/source_manifest.json`，未把大图复制进交付证据目录。
研发点数据在 [development_distribution.csv](evidence/development_distribution.csv)，来自 S10 的 `vanilla_chronological_requests`，逐 ID 对照 S11。
Noemate 点数据仍为 [noemate_distribution.csv](evidence/noemate_distribution.csv)。统一外观不代表两组实验可以直接比较性能。
研发图采用两个相接的原生散点图区，不映射 token 数值；两段内各自线性，图下注明分段等高，不能跨段比较视觉距离或斜率。Noemate 保持连续线性轴。

## 第 3 页管道示意源码核对（2026-09-28）

管道源码引用在本节单独列出；下文历史数据仍固定到原始 commit。`evidence/source_manifest.json` 本轮补充了分布图来源及研发分布数据，不代表重新执行服务器实验。
本节直接核对了本地只读子模块及无代码改动的主仓库文件，不依赖“最新版”网页或未确认的服务器包版本。
证据类型为**源码确定**；没有执行真实模型/HTTP 追踪。这里只描述 V1 AsyncLLM 的多进程流式 Chat Completions 路径。

| 集合 | 固定源码身份 |
| --- | --- |
| vLLM v0.23.0，只读子模块 | `0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665` |
| DTB 与 serving patch | `f5512aa35152a13a9ee164a64928674badc26ceb`；本次未修改代码 |

| ID | 固定来源 | 函数、行号与图中职责 |
| --- | --- | --- |
| U01 | [v1/engine/core.py](https://github.com/vllm-project/vllm/blob/0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665/vllm/v1/engine/core.py#L1264) | `_process_engine_step` 1264–1271：EngineCoreOutputs 入队；`process_output_sockets` 1545–1603：Msgpack / ZMQ PUSH；1348–1349：处理 ABORT |
| U02 | [v1/engine/core_client.py](https://github.com/vllm-project/vllm/blob/0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665/vllm/v1/engine/core_client.py#L990) | `AsyncMPClient` 的接收任务 990–1047：收包、解码、outputs_queue、get_output_async；1111–1113：发 ABORT |
| U03 | [v1/engine/async_llm.py](https://github.com/vllm-project/vllm/blob/0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665/vllm/v1/engine/async_llm.py#L557) | `generate` 557–586：add_request、消费 collector 并 yield；`output_handler` 656–678：OutputProcessor；`abort` 709–718：本地输出与引擎取消 |
| U04 | [v1/engine/output_processor.py](https://github.com/vllm-project/vllm/blob/0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665/vllm/v1/engine/output_processor.py#L45) | `RequestOutputCollector` 45–95：增量聚合与交付；`process_outputs` 618–663：detokenize、stop、logprobs、RequestOutput 入每请求 collector |
| U05 | [openai/chat_completion/serving.py](https://github.com/vllm-project/vllm/blob/0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665/vllm/entrypoints/openai/chat_completion/serving.py#L399) | `chat_completion_stream_generator`；720–726：原生 parser.parse_delta；872–873：JSON/SSE；936：[DONE] |
| U06 | [openai/chat_completion/api_router.py](https://github.com/vllm-project/vllm/blob/0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665/vllm/entrypoints/openai/chat_completion/api_router.py#L53) | `create_chat_completion` 53–74：调用 serving，流式返回 FastAPI StreamingResponse，media_type=text/event-stream |
| U07 | [entrypoints/launcher.py](https://github.com/vllm-project/vllm/blob/0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665/vllm/entrypoints/launcher.py#L26) | `serve_http` 26–35、71–82：FastAPI app、Uvicorn Config/Server 与监听 socket |
| P01 | [dtb/request_controller.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/request_controller.py#L237) | `_managed_generator` 237–276：独立 producer 与 queue consumer；511–535：result_queue、状态机与 native_chat_response |
| P02 | [dtb/reasoning/request_context.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/reasoning/request_context.py#L159) | `stream_output` 159–207：输出额度及队列；`close_reasoning` 388–403：cue、闭合 token 与分隔符进入内部 prompt |
| P03 | [dtb/integration/vllm/response_adapter.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/integration/vllm/response_adapter.py#L32) | `stream_queue` 32–41、`native_chat_response` 73–97：保持原生 chat_completion_stream_generator / parser 路径 |
| P04 | [dtb_serving.patch](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/patches/vllm/v0.23.0/dtb_serving.patch#L97) | 97–99：serving 分派到 DTB controller；native 动作继续走原生路径 |
| P05 | [dtb/budgeting/budget_resolver.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/budgeting/budget_resolver.py#L62) | 第 1 页预算参数补充：62–78 阈值；157–214 固定/场景预算；336–358 请求/场景硬上限 |
| C06（补充定位） | [deferred_observation.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/reasoning/deferred_observation.py#L193) | 193–214：abort/drain；226–243：观察、阈值和自然边界；256–297：接管尾部、保留前缀、选择 probe 或 final |
| C05（补充定位） | [generation_states.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/reasoning/generation_states.py#L758) | `_prepare_boundary` 758–780：内部 answer cue；final 704–718：新 child_request_id 与新的 generate |

图中“HTTP 服务”仅展开 vLLM 源码明确引用的 FastAPI StreamingResponse 与 Uvicorn，
不假定目标环境固定使用 h11/httptools，也不假定另有反向代理或 vllm_gateway。
图是逻辑数据流与控制回路，不把每个组件画成独立进程，不把 HTTP chunk 当成 token 边界。

## 原内容稿的数据与实现引用

与 [五页内容稿](outlines.md) 配套。所有 `S*` / `N*` 源固定来自 `debug_agent` 的提交，
`C*` 是当前交付代码快照，`O*` 是独立 SDK，`R01` 是用户参考文件。

| 来源集合 | 固定提交 | 用途 |
| --- | --- | --- |
| debug_agent | `32ab2268335e00d79e0aeda4395c6ecdc06d81eb` | 历史报告与实验数据 |
| codex/dtb-static-refactor | `f5512aa35152a13a9ee164a64928674badc26ceb` | 说明已交付能力，不承接历史收益归因 |
| codex/thinking-observer | `7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d` | 独立观察器 SDK |

在线链接指向不可变提交，本地有完整 Git 历史时无需访问网络：

```bash
git show 32ab2268335e00d79e0aeda4395c6ecdc06d81eb:docs/report/agent_dtb_demo_data_memo.md
```

## 优先阅读的历史报告

| 引用 | 文件 | 定位 / 用途 |
| --- | --- | --- |
| S13 | [agent_dtb_demo_data_memo.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/agent_dtb_demo_data_memo.md) | §2 研发整体口径；§3 case37；§4 备选；§5 研发长尾 |
| S14 | [工程复盘时间线_报错样例.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/sum0911/%E5%B7%A5%E7%A8%8B%E5%A4%8D%E7%9B%98%E6%97%B6%E9%97%B4%E7%BA%BF_%E6%8A%A5%E9%94%99%E6%A0%B7%E4%BE%8B.md) | A–D、时间线和结果口径；第 2 页首要来源 |
| S15 | [RESULT.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/agent_run07_08/RESULT.md) | §2 实验身份；§4 质量；§5 汇总；§7 工具错误边界 |
| S16 | [README.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/README.md) | 分母、阈值、历史样例与报告边界 |
| S21 | [l178_a1.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l178_a1.md) | 178/a1 可读样例卡，含完整输出、参数、输入充分性与边界 |
| S22 | [l178_a3.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l178_a3.md) | 178/a3 可读失败卡，只供审阅 |
| S23 | [l399_a10.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l399_a10.md) | 399/a10 可读有限成功卡，只供审阅 |
| S18 | [index.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/index.json) | claims B01/B02/B03/B04/B05；历史结论与限制 |

## 全部读取源

精确 Git blob SHA-256、字节数与提取结果哈希见 [source_manifest.json](evidence/source_manifest.json)。
下表描述定位，不把历史报告中未验证的推断升级为事实。

| ID | 固定来源 | 字段 / 定位 |
| --- | --- | --- |
| S01 | [docs/report/dtb_closeout/evidence/distribution.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/distribution.json) | summary / requests；第 4 页分布 |
| S02 | [docs/log/perf/noemate_analysis_60f2d7/request_features.jsonl](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_analysis_60f2d7/request_features.jsonl) | 按 request_id 对账 reasoning_tokens_local / exclusions / system_sha256 |
| S03 | [docs/log/perf/noemate_analysis_60f2d7/prompt_roles.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_analysis_60f2d7/prompt_roles.json) | 按 system_sha256 固定角色，沿用原分析分类规则 |
| S04 | [docs/report/dtb_closeout/evidence/paired.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/paired.json) | pairs[group=long] / rows；备查 5 个 case × 10 对 |
| S05 | [docs/report/dtb_closeout/evidence/semantic_review.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/semantic_review.json) | alarm_source / all_178_dtb_attempts / 319；字段与质量审核 |
| N178S | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l178_measured/summary.jsonl](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l178_measured/summary.jsonl) | 20 条 summary；按 client_request_id 与 attempt 对账 |
| N178M | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l178_measured/manifest.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l178_measured/manifest.json) | 10 次固定输入、交替 V/D 回放 manifest |
| N259S | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l259_measured/summary.jsonl](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l259_measured/summary.jsonl) | 20 条 summary；按 client_request_id 与 attempt 对账 |
| N259M | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l259_measured/manifest.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l259_measured/manifest.json) | 10 次固定输入、交替 V/D 回放 manifest |
| N319S | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l319_measured/summary.jsonl](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l319_measured/summary.jsonl) | 20 条 summary；按 client_request_id 与 attempt 对账 |
| N319M | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l319_measured/manifest.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l319_measured/manifest.json) | 10 次固定输入、交替 V/D 回放 manifest |
| N353S | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l353_measured/summary.jsonl](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l353_measured/summary.jsonl) | 20 条 summary；按 client_request_id 与 attempt 对账 |
| N353M | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l353_measured/manifest.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l353_measured/manifest.json) | 10 次固定输入、交替 V/D 回放 manifest |
| N399S | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l399_measured/summary.jsonl](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l399_measured/summary.jsonl) | 20 条 summary；按 client_request_id 与 attempt 对账 |
| N399M | [docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l399_measured/manifest.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/long_l399_measured/manifest.json) | 10 次固定输入、交替 V/D 回放 manifest |
| S06 | [docs/report/dtb_closeout/evidence/cards/l178_a1.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l178_a1.json) | 178/a1 的 pair、vanilla、dtb、parameters、semantic_verdict；讲者备注中的输出示例 |
| S07 | [docs/report/dtb_closeout/evidence/cards/l178_a3.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l178_a3.json) | 178/a3；缺严重组的失败对照，只供审阅 |
| S08 | [docs/report/dtb_closeout/evidence/cards/l399_a10.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l399_a10.json) | 399/a10；日期限制下的数值汇总，只供审阅 |
| S09 | [docs/report/dtb_closeout/evidence/review/requests/l178.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/review/requests/l178.json) | messages[32,34,...,54]；12 条告警原输入 |
| S10 | [docs/report/sum0911/sources/evidence.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/sum0911/sources/evidence.json) | variants / quality_policy / case37；第 4 页汇总和原文 |
| S11 | [docs/log/perf/agent_run07_08/analysis.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/agent_run07_08/analysis.json) | variants.vanilla 与 variants.run08 的 requests / tasks；逐请求复算 |
| S12 | [docs/log/perf/agent_run06_closed/analysis.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/agent_run06_closed/analysis.json) | intervention / probe；独立 closed 轮，非 run-08 |
| S20 | [proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/full_log/proxy_session_795d073fadb0/part-00001.jsonl](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/full_log/proxy_session_795d073fadb0/part-00001.jsonl) | JSONL 第 20 行 request_id=0480aca87728475f；第 21 行可核对后续工具返回 |
| S13 | [docs/report/agent_dtb_demo_data_memo.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/agent_dtb_demo_data_memo.md) | §2 研发整体口径；§3 case37；§4 备选；§5 研发长尾 |
| S14 | [docs/report/sum0911/工程复盘时间线_报错样例.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/sum0911/%E5%B7%A5%E7%A8%8B%E5%A4%8D%E7%9B%98%E6%97%B6%E9%97%B4%E7%BA%BF_%E6%8A%A5%E9%94%99%E6%A0%B7%E4%BE%8B.md) | A–D、时间线和结果口径；第 2 页首要来源 |
| S15 | [docs/log/perf/agent_run07_08/RESULT.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/agent_run07_08/RESULT.md) | §2 实验身份；§4 质量；§5 汇总；§7 工具错误边界 |
| S16 | [docs/report/dtb_closeout/README.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/README.md) | 分母、阈值、历史样例与报告边界 |
| S17 | [docs/report/dtb_closeout/evidence/source_manifest.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/source_manifest.json) | 历史源清单，仅追溯；其中旧 pool 不作为当前提取脚本的依赖 |
| S18 | [docs/report/dtb_closeout/evidence/index.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/index.json) | claims B01/B02/B03/B04/B05；历史结论与限制 |
| S21 | [docs/report/dtb_closeout/evidence/cards/l178_a1.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l178_a1.md) | 178/a1 可读样例卡，含完整输出、参数、输入充分性与边界 |
| S22 | [docs/report/dtb_closeout/evidence/cards/l178_a3.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l178_a3.md) | 178/a3 可读失败卡，只供审阅 |
| S23 | [docs/report/dtb_closeout/evidence/cards/l399_a10.md](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l399_a10.md) | 399/a10 可读有限成功卡，只供审阅 |
| C01 | [dtb/README.md](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/README.md) | 结构 / 请求执行入口 / 配置生命周期 / Telemetry |
| C02 | [dtb/AGENTS.md](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/AGENTS.md) | 按问题定位 / 生命周期与并发所有权 |
| C03 | [docs/design/dtb_system_scene_native_routing.md](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/docs/design/dtb_system_scene_native_routing.md) | system 身份、native 分流、路由优先级 |
| C04 | [RESULT.md](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/RESULT.md) | 交付范围 / 本地验证 / 服务器最小验收；历史本地记录，未在本次重跑 |
| C05 | [dtb/reasoning/generation_states.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/reasoning/generation_states.py) | _ThinkingState / _ConfidenceProbeState / _FinalAnswerState 三状态路径 |
| C06 | [dtb/reasoning/deferred_observation.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/reasoning/deferred_observation.py) | _DeferredInterventionState；原生观察和按条件接管 |
| C07 | [dtb/config/budget_config.yaml](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/config/budget_config.yaml) | RUNTIME.enable_history_budget=false、enable_difficulty_estimator=false |
| C08 | [dtb/reasoning/confidence_gates.py](https://github.com/F00L42/TB/blob/f5512aa35152a13a9ee164a64928674badc26ceb/dtb/reasoning/confidence_gates.py) | confidence 计算；average_fallback 不是正确概率 |
| O01 | [thinking_observer/README.md](https://github.com/F00L42/TB/blob/7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d/thinking_observer/README.md) | 独立 SDK 能力、接口、限制、部署入口 |
| O02 | [thinking_observer/docs/validation.md](https://github.com/F00L42/TB/blob/7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d/thinking_observer/docs/validation.md) | 2026-09-22 本地验证记录 / 尚未验证 |
| R01 | [docs/report/closeout/reference/动态思考预算控制方案.pptx](reference/%E5%8A%A8%E6%80%81%E6%80%9D%E8%80%83%E9%A2%84%E7%AE%97%E6%8E%A7%E5%88%B6%E6%96%B9%E6%A1%88.pptx) | slide 1；只继承方案结构，不把目标当实测 |
| S19 | [docs/report/dtb_closeout/evidence/phase.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/phase.json) | selected：399/a2，parent chatcmpl-dtbtool-l399-a2-dtb-activate_20260922T031321Z |

## 取数与证据边界

- 未 checkout 历史分支、未复制历史大目录、未调用模型。
- 第 4 页主效果使用全部 50 对长请求回放。178/a1 为备注中的输出示例，178/a3 与 399/a10 为审阅对照；319 不作为质量成功例。
- 第 2 页的故障计数引用已提交工程复盘；本次未重新执行这些 parser/服务器回放。
- 第 4 页的请求耗时与总账由脚本复算；业务质量沿用带边界的历史规则核对和审核。
- 复算命令与检查范围见 [精简证据说明](evidence/README.md)。

## 第 4 页 Noemate 回放集合与实际触发边界（2026-10-08）

[演示稿 r9](presentation/DTB_closeout_20260930_r9.pptx) 按用户确认，以**“Noemate 长请求回放（50 对）”**展示全部 5 个冻结输入 × 10 对回放的平均 E2E：24.8744579→18.51114568 秒，下降 25.5817%；178/a1 留作备注示例。该集合有同轮耗时配对，没有逐请求的实际触发证明。以下历史来源继续固定在 `debug_agent` 提交 `32ab2268335e00d79e0aeda4395c6ecdc06d81eb`；没有重新执行服务器实验。

| ID | 固定来源 | 本次核对字段 / 用途 |
| --- | --- | --- |
| S24 | [Noemate off 主实验 RESULT](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/RESULT.md) | §2 身份、off 与交替配对；§3 明确无逐请求 phase；§4 五个输入各 10 对；§5 质量反例 |
| S25 | [实际 phase 轮 manifest](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_activation_audit_20260922T031321Z/direct750/manifest.json) | `selected_lines` 为 6 个输入、`repetitions=2`、`variants=["dtb"]`；没有 Vanilla 对照 |
| S26 | [实际 phase 轮 paired_summary](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_activation_audit_20260922T031321Z/direct750/paired_summary.json) | `request_count=12`、`all_pairs.pairs=0`、`latency_ranking_valid=false`；零值加速字段不是测得 0% 收益 |
| S27 | [实际 phase 轮 audit_summary](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_activation_audit_20260922T031321Z/audit_summary.json) | `branches.budget_final=7`、`native_complete=5`、`hidden_probe_tokens=0`；服务事件与客户端身份闭合范围 |
| S28 | [实际 phase 轮 request_audit](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_activation_audit_20260922T031321Z/request_audit.json) | 逐 `parent_request_id` 的 `branch`、`phase_counts`、`intervention`；关联 serverlog 原行号 |
| S29 | [实际 phase 轮 RESULT](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_activation_audit_20260922T031321Z/RESULT.md) | §2 只有 DTB、无独立预热；§4 七次 `budget_final`、`final_answer_reason=budget_exhausted`；§5–6 账本与质量边界 |
| S30 | [更早 direct750 off 轮 RESULT](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_direct750_off_20260921T064359Z/RESULT.md) | §2 用户确认实际日志未开启；名含 phase 的目录不能当实际 phase 证据；§4 长输入探索选样规则 |
| S31 | [off 主实验 request_metrics](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/request_metrics.json) | 628 条中按 `group=long`、`stage=measured` 取 100 条；以 `client_request_id` 对账 `reasoning_tokens`、`elapsed_ms`、终止与异常 |
| S32 | [off 主实验 semantic_checks](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_guardrail_main_20260922T070000Z/semantic_checks.json) | `scope` 仅字面告警覆盖与显式算术；`requests` 为逐次有限检查，不能等同完整业务质量通过 |
| S33 | [冻结候选 selection](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/log/perf/noemate_activation_audit_20260922T031321Z/selection.json) | `long_candidates=[178,259,319,353]`、`semantic_boundary=[399]`；此前探索选样、后续轮冻结，非总体分布或独立 holdout |

复算入口仍为 **S04** 的 `pairs[group="long"]`，50 对分别由既有 **N178S/N178M、N259S/N259M、N319S/N319M、N353S/N353M、N399S/N399M** 的原 summary/manifest 核实。本次逐 ID 核对 `v_ms/d_ms` 与 summary 的 `elapsed_ms`，再对照 S31 的 `reasoning_tokens`；未混入预热、smoke、短请求，也未过滤慢请求、截断或动作变化。

| 数据集合 | 原字段 / 过滤 | 复算结果 | 使用边界 |
| --- | --- | --- | --- |
| 冻结长请求全 50 对 | S04 `group=long` | E2E 总量 1,243.722895→925.557284 s；均值 24.8744579→18.51114568 s；`(1−ΣD/ΣV)×100%=25.5817%`；43/50 更快 | r9 正文采用的回放时延效果；未逐请求确认实际早退 |
| 48 对长度代理 | 上述集合再取 `750<=d_reasoning<=753` | 均值 25.045980→18.655950 s；下降 25.5132% | 仅备查，正文不使用；事后长度筛选不能确认触发 |
| 7 次实际预算接管 | S19 `all_requests` / S28 中 `intervention_status=triggered`、`abort_count=1`、`post_drain_branch=budget_final`，且有 `final_answer` | 7 次，0 probe，Vanilla 配对数为 0 | 机制证据；不能跨 run 或跨 off/phase 补对计算加速率 |

早退定义的源码边界沿用 **C06 272–295 行**与 **C05 665–668 行**：自然完成、模型边界、进入 probe、预算耗尽 final 是不同结果；probe 还须核对接受并转 final。只有 capture 思考大于 750 或输出约为 750，均不能代替实际状态证据。

本节是补充来源索引，未改写既有 `evidence/source_manifest.json` 或提取脚本。2026-10-08 离线核对的完整数值、配对 ID 及所读数据 blob SHA-256 留在本地审计产物 `artifacts/closeout-review/20261008/noemate-trigger-audit.json`；该 ignored 文件不作为交付依赖，以上固定 Git 来源与字段可独立复算。质量问题和未闭合项只记录在大纲审阅、讲者备注中，不新增演示页脚声明。

## 本轮固定初始输入端到端测试（2026-10-13）

用户确认新旧模型权重相同、所有初始业务输入固定、压缩功能已先验证，正式完整E2E已切回off。
本轮采用“固定初始业务输入下长请求加速、短请求性能非劣”的模型HTTP E2E整体观测口径。
它与历史50对长请求回放分别取数；演示r10只补结论与博客入口，详细定义和归因边界见博客。

| ID | 固定来源或交付路径 | 核对字段 / 用途 |
| --- | --- | --- |
| S34 | [DTB capture manifest](https://github.com/F00L42/TB/blob/03d9ec6c3d7b48dc7925fc024b63462bd9fe7855/proxy/results/noemate/agent_20261012T030114Z_daf6d49b/manifest.json) | 网关append_body.enable_think_budget=true，无fixed；模型别名与部署源码身份 |
| S35 | [DTB requests.jsonl](https://github.com/F00L42/TB/blob/03d9ec6c3d7b48dc7925fc024b63462bd9fe7855/proxy/results/noemate/agent_20261012T030114Z_daf6d49b/derived/0dbb9a2fe91155631b1d/requests.jsonl) | 11,441全部请求及同目录details；E2E、token、结束、异常 |
| S36 | [Vanilla requests.jsonl](https://github.com/F00L42/TB/blob/60f2d7c01faa3268874893dc42f850ad86086c7c/proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3/requests.jsonl) | 12,214全部请求及同目录details；Git blob与旧32ab2268来源相同 |
| S37 | [详细 Markdown 博客](noemate_e2e_20261013.md) | 完整实验叙述、采纳口径、公式、边界、异常与复现 |
| S38 | [comparison.json](evidence/noemate_e2e_20261013/comparison.json)、[acceptance.json](evidence/noemate_e2e_20261013/acceptance.json) | 复算分母/区间；原分析快照与本轮观测验收分开记录 |
| S39 | [初始问题表](evidence/noemate_e2e_20261013/initial_root_cases.csv) | Vanilla定层的共同问题，重复数、耗时、prompt、finish |
| S40 | [原生同输入负对照](evidence/noemate_e2e_20261013/native_same_input_cases.csv) | 114case、126→128调用；耗时与输出背景变化 |
| S41 | [源文件清单](evidence/noemate_e2e_20261013/source_files.json)、[证据使用说明](evidence/noemate_e2e_20261013/README.md) | Git源、投影gzip、hash与不依赖ignored产物的重算命令 |

本轮主平均E2E14.971→10.685秒，P9541.774→28.415秒；正常可观察main>800思考316/8277→0/8018，最大1841→755。
初始短层1475共同问题case等权E2E9.361→6.752秒，D/V=.721、95%CI[.700,.743]。
本轮非劣指该固定业务初始输入实验的性能观测，不声称事前注册的5%界限、业务质量非劣或纯DTB独立因果效应。
上下文/轨迹差异、同输入原生负对照35.4%改善以及所有异常保留于S37–S41，不从成功快速子集删去失败来构造结论。
