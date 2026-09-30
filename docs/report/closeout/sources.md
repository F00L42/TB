# 汇报引用源索引

## 第 3、4 页分布图的分工与风格（2026-09-28）

第 3 页采用辅助研发 Vanilla-06 的 391 条分布，第 4 页采用 Noemate 的 8,277 条主 Agent 分布。
下列两张历史图已实际查看；本轮只更新构图要求和数据入口，没有修改图片或 PPTX。

| ID | 固定图源 | 本稿使用方式 |
| --- | --- | --- |
| V01 | [研发思考长度长尾与 850 阈值](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/sum0911/统计图/思考长度长尾与850阈值.png) | 第 3 页数据与旧图参照。同目录有同名 PPTX；移除宽分段隔带及右侧放大图，默认连续线性轴，只留细 850 阈值线 |
| V02 | [Noemate Vanilla 思考长度分布](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/figures/vanilla_distribution.png) | 第 4 页图源与两图统一风格参照。同目录有同名 PPTX；连续线性轴、浅色普通点、金色长点、细 750 阈值线 |

V01/V02 的 PNG Git blob 哈希已纳入本轮更新的 `evidence/source_manifest.json`，未把大图复制进交付证据目录。
研发点数据在 [development_distribution.csv](evidence/development_distribution.csv)，来自 S10 的 `vanilla_chronological_requests`，逐 ID 对照 S11。
Noemate 点数据仍为 [noemate_distribution.csv](evidence/noemate_distribution.csv)。统一外观不代表两组实验可以直接比较性能。

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
| S06 | [docs/report/dtb_closeout/evidence/cards/l178_a1.json](https://github.com/F00L42/TB/blob/32ab2268335e00d79e0aeda4395c6ecdc06d81eb/docs/report/dtb_closeout/evidence/cards/l178_a1.json) | 178/a1 的 pair、vanilla、dtb、parameters、semantic_verdict；主成功例 |
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
- 主成功样例是 178/a1；178/a3 与 399/a10 是审阅对照。319 不作为质量成功例。
- 第 2 页的故障计数引用已提交工程复盘；本次未重新执行这些 parser/服务器回放。
- 第 4 页的请求耗时与总账由脚本复算；业务质量沿用带边界的历史规则核对和审核。
- 复算命令与检查范围见 [精简证据说明](evidence/README.md)。
