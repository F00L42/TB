# 思维链预算与压缩工程指南

## 项目目标

本仓库比较两套思维链压缩/预算控制实现。工程优化的首要目标不是单纯减少响应中的 token，而是在质量不退化的前提下降低端到端时延：

```text
净时延收益
= 减少的可见生成 token × 基线 TPOT
- 控制器额外成本
```

控制器额外成本必须包括隐藏的 trial-answer token、重复 prefill、子请求排队、置信度计算、预算决策、序列化和状态切换。响应 usage 中的 completion token 通常不包含隐藏 trial answer，不能直接代表模型总工作量。

## 仓库地图

- `deer/`：简化版 DEER，侵入 vLLM Server，通过 `CoTController` 驱动“思考—试答—置信度—回答”状态机。先读 `deer/AGENTS.md`。
- `dtb/`：生产 DTB（Dynamic Think-Budget Control）实现。入口是
  `request_controller.py`；预算解析、三状态推理、history、telemetry 和 vLLM 适配按子包分离。
- `think_budget/`：通过 API 转发运行的离线参考实现。范围和与生产 adapter 的边界见
  `think_budget/README.md`；已删除源码镜像的历史说明归档见
  `docs/archive/think_budget_refactored_final/README.md`。
- `patches/vllm/v0.23.0/`：唯一可部署的 vLLM patch 制品及应用说明；依次应用
  `dtb_serving.patch`、`qwen3_coder_parser.patch`。
- `docs/log/`：端到端汇总、vLLM/vLLM-Ascend 服务日志和人工 profiling 结果。
- `docs/debug/deer_performance_analysis.md`：当前性能诊断、优化优先级和服务器 profiling runbook。
- `docs/design/think_budget_generalization_plan.md`：13 场景实验、数据提取、自动路由审计、
  候选选择和阶段验收的权威流程。
- `tools/extract_dtb_phase_metrics.py`：按结果 CSV 中的 `request_id` 从共享
  `server.log` 隔离请求和 phase；接口只接收一个 run 目录。
- `docs/report/chart/`：可选的请求级配对复算、表格和图表生成层，不替代 telemetry
  裁决审计。
- `docs/design/`：迁移与模块设计文档。

## 推荐阅读顺序

1. 本文件；分析 DTB 产品实验时继续读
   `docs/design/think_budget_generalization_plan.md`，分析 DEER 内部实现时再读
   `deer/AGENTS.md`。
2. 目标 `docs/log/perf/<run_id>/` 的 `manifest.json`、`extraction_manifest.json`、
   `_SUMMARY.csv`、具体结果 CSV 和已有 `RESULT.md`；当前 13 场景自动模式闭环参考
   `docs/log/perf/run_07/RESULT.md`。
3. 需要重新提取共享日志时读 `tools/extract_dtb_phase_metrics.py`；需要生成请求级配对表
   或图时读 `docs/report/chart/report_chart_workflow.md`。
4. 需要解释服务内算法时读 `dtb/request_controller.py`、
   `dtb/budgeting/budget_resolver.py`、`dtb/reasoning/state_machine.py`、
   `dtb/reasoning/generation_states.py` 和版本化 serving patch。
5. `deer/cot_controller.py`、`deer/state.py`、`deer/context.py` 以及
   `think_budget/CLAUDE.md` 只用于核对历史实现或迁移语义。

`docs/log/test_results/_SUMMARY.csv` 和 `docs/log/deer_confidence.txt` 是早期证据，不再是
新性能 run 的默认入口。

## 新性能结果的固定处理顺序

收到一个新的 `docs/log/perf/<run_id>/` 后，按以下顺序处理；不要从 `_SUMMARY.csv` 的
平均值直接跳到算法结论：

1. **确定实验身份和基线。** 从用户说明和 `manifest.json` 确认模型/量化或后训练版本、
   服务与 DTB commit、policy/YAML/patch hash、observability mode、experiment role、
   请求参数、sampling/seed、并发、到达节奏和 cache 状态。
   优先使用同一实验身份、同一数据集的 Vanilla；模型或训练状态变化时，不得沿用旧
   run 的 Vanilla。文件名前缀（如 `vanilla_`、`qp_vanilla_`、`dtb_`）是 variant，
   不是 scene。
2. **提取并验证归属。** 仅对 `observability.mode=phase` 的账本 run，若目录内有根目录
   `server.log`、唯一 `_SUMMARY.csv` 和其引用的具体结果 CSV，执行：

   ```bash
   python tools/extract_dtb_phase_metrics.py docs/log/perf/<run_id>
   ```

   提取器以具体结果 CSV 的非空 `request_id` 为白名单，生成
   `extraction_manifest.json`、`request_index.csv` 和每个结果独立的 `extracted/`
   子目录。若仓库只上传了已提取产物而缺少原始大日志，不要假装可在本地重新提取。
3. **先做完整性和裁决审计。** 对账 summary 行、具体结果行、唯一 request ID、日志覆盖、
   `decision_branch`、scene、reject、有效 `DtbBudgetDecision`、配置签名和 `source_by_field`。
   任何请求归属不闭合或 variant 分支断言失败时，该 run 只能用于排障，不能参与性能
   排名。Vanilla 没有 phase 是合法状态；`off` 正式 DTB run 没有 phase 也是设计状态，
   但不能用于分支或模型总工作量裁决。
4. **再做请求级配对。** 使用规范化数据集标识和 `QA_ID` 配对 Vanilla/候选；拒绝缺失、
   重复或样本集合不同的静默比较，并核对相同 ID 的问题与参考答案或 manifest 数据集
   哈希。优先报告逐场景 paired E2E、13 场景等权宏平均、请求加权结果、P50/P95、
   响应 completion token 和更快请求数。
5. **独立处理质量。** 只有字面量 `True` 计为自动 Judge 正确；`judge_invalid` 不得静默
   当成正确或错误。取得人工结论后，在 `RESULT.md` 记录样本、结论和理由；生成报表时
   通过可审计的 `judge_overrides` 应用。客户端 `错误`、Judge 错误和 hard failure 是
   三个概念，必须分开统计。
6. **闭合模型工作量。** 分别汇总 visible thinking、hidden probe、final answer token
   及各 phase wall/queue/prefill/decode；比较 phase wall 总和与客户端 E2E 总和并解释
   缺口。响应 completion token 不含 hidden probe，不得称为总生成量。
7. **写入同 run 的 `RESULT.md`。** 至少包含结论、输入与证据边界、完整性、实际分支和
   配置、逐场景与总体比较、质量转移、phase 账本、异常项、阶段验收和下一轮决策。
   所有结论标注为实测、源码确定或假设。

精确定义、公式、质量分层、目录契约和验收清单以
`docs/design/think_budget_generalization_plan.md` 第 6 节为准。

## 性能分析口径

每个外部请求用一个 `parent_request_id` 关联所有内部 phase。至少区分：

- `decision`：scene 匹配、预算、难度和历史决策的 CPU 时间。
- `initial_think` / `iterative_think`：对外可见的思考生成。
- `confidence_probe`：不对外可见、但真实消耗算力的试答分支。
- `final_answer`：最终答案生成。
- `controller_cpu`：tokenize/decode、拷贝、置信度聚合、队列和格式转换。

每个 phase 至少记录 `queue_ms`、`prefill_ms`、`decode_ms`、`prompt_tokens`、`cached_prompt_tokens`、`generated_tokens`、`visible`、`round`、`stop_reason`。不要只用 vLLM 每 10 秒打印的全局吞吐率归因单个请求。
`final_answer` 还必须记录原生 `completion_token_limit`、final child 启动前已透传 token、待透传
reasoning boundary、外部剩余额度和实际 `answer_limit`，以便审计统一 completion 额度。

DTB telemetry 由 `dtb/config/budget_config.yaml` 的 `OBSERVABILITY.mode` 在服务启动时确定：

- `off` 是生产和正式性能排名默认值，不创建 recorder/writer，日志中不得出现 DTB 成功事件；
- `phase` 写 `DTB_PHASE` schema 3 和 `DTB_REQUEST_SUMMARY`，用于单独的归属与 phase 账本 run；
- `stream_audit` 不创建 phase recorder，只对 `stream_request_ids` 显式白名单写
  `DTB_STREAM_AUDIT`。该事件可能含正文和 token ID，只能用于隔离排障。

不要把 telemetry 启用 run 与 `off` run 混为同一性能基线。提取器兼容读取历史
`TB_PHASE` schema 1/2，但重构后生产端只写新事件。

比较必须使用相同样本、采样参数、随机种子、到达节奏、并发度、prefix cache 状态和质量评分。目标版本中 MTP 保持生产默认配置，不再作为常规性能分析变量。先看配对请求差值，再看总体分位数。

## 证据规则

结论要标明证据强度：

- **实测**：日志、CSV、profiler 或请求级 metrics 直接支持。
- **源码确定**：可由当前仓库或对应 vLLM 版本源码推出，但尚未在目标服务器测量。
- **假设**：相关现象的候选解释，必须给出可以证伪的实验。

不要作以下推断：

- 不同内部 `request_id` 不等于 prefix KV 一定从零计算；开启 Automatic Prefix Caching 时可按 prompt 内容复用完整 block。
- 一个外部请求的多个状态是顺序 `await` 的，不能把全部状态计成同时占用 `max_num_seqs`。
- vLLM `v0.20.2` 中 MTP 的严重退化已由端到端实验确认为该版本的性能 bug；不要把这份历史日志归因到控制器算法。
- vLLM `v0.23.0` 已验证可同时开启思维控制模块与 MTP，且没有 MTP 性能退化。后续默认忽略 MTP 对控制器性能的影响；只有新版本、模型或硬件出现明确回归证据时才重新分析。
- 可见 token 变少不等于总生成 token 变少；隐藏 probe token 必须单列。

## vLLM 集成约束

- DTB 的输出契约是**原生 parser-facing 等价**：控制器只允许缩短思考内容，交给原生
  reasoning/tool parser 的逻辑 token 序列、结构标记、增量顺序、结束语义和多轮消息语义
  必须与同一模型完成一次原生生成时一致。流式响应必须同时保持 `text` 与 `token_ids`
  的可解析连续性；子请求边界、合成 `</think>`、空文本 delta 和 speculative batch 不得
  使 `<tool_call>` 等结构 token 丢失、重复或进入 `content`。非流式响应必须继续使用
  原生完整响应 parser。任何工具调用 XML 泄漏、参数 JSON 不完整、tool 后 role 尾流或
  `finish_reason` 偏离都属于 hard failure，不能用“可见思考更短”解释或接受。
- 最终目标集成版本为 vLLM `v0.23.0`。`v0.20.2` 只用于解释既有日志和历史实现，不再作为优化目标或性能基线。
- 目标运行环境使用 vLLM-Ascend。提交性能结论时记录实际 `vllm`、`vllm-ascend`、PyTorch、torch-npu、CANN、模型版本以及两个源码仓库的 commit SHA；不要仅凭版本号推断服务器代码。
- Controller 劫持原生 serving 后，所有内部 `generate()` 必须透传原生调用上下文，包括 priority、LoRA、trace headers、data-parallel rank、reasoning/parser 参数等；尤其要保持同一外部请求的 DP 亲和性。
- `budget_disabled` 一类开关必须真正回到原生单次生成路径，不能只关闭部分评分逻辑后继续运行状态机。
- 性能优化不得以吞掉取消、错误或最终答案为代价。流式与非流式模式都要验证。

## 上游源码策略

- 后续会反复分析 vLLM serving、scheduler、metrics 和 vLLM-Ascend backend，推荐在本地保留两个**只读、固定版本的 Git checkout**，而不是每次只查看 GitHub 网页，也不要把完整上游源码复制进本仓库提交。
- checkout 必须对应实际验证环境：vLLM 固定到 `v0.23.0` 的 tag；vLLM-Ascend 固定到 `v0.23.0rc1` 的 tag。
- checkout放在使用git submodule add添加的`third_party/`，下载源码只需执行`git submodule update --init --recursive`；在性能实验 `manifest.json` 中保存路径、remote URL、commit SHA、dirty 状态和安装包版本。
- 日常源码追踪优先使用本地 `rg`、`git grep`、`git blame` 和 tag 间 diff；GitHub 主要用于查看 PR 讨论、release note、issue 和本地 checkout 中不存在的后续提交。
- 上游 checkout 视为只读依赖。控制器修改继续保存在本仓库的 adapter 中，避免本地上游改动使分析结果不可复现。

## 当前环境与验证

本工作区能读取仓库源码和日志，但没有目标模型或推理设备，不能在本机验证真实 vLLM 性能。运行时结论必须按 `docs/debug/deer_performance_analysis.md` 的 runbook 由服务器执行后回填。

本地改动优先做静态检查和无模型单元测试。不要把无法运行的服务器测试描述为已通过；不要修改或清理与当前任务无关的工作区改动。

## 文档维护

- 文档和结构化日志使用 UTF-8。
- 性能实验产物放在 `docs/log/perf/<run_id>/`。完整服务器归档包含 `manifest.json`、
  `server.log`、客户端 summary/明细、请求级 JSONL、phase JSONL、metrics 前后快照和
  `RESULT.md`；因体积不能完整上传时，至少回传客户端 summary/明细、
  `extraction_manifest.json`、`request_index.csv`、每个结果的 `requests.jsonl` 与
  `phase_summary.csv`。缺失 manifest 时只能报告实测差异，不能归因跨 run 波动。
- 当实现、默认参数或实测结论变化时，同步更新性能分析文档末尾的变更记录。
