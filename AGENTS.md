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
- `think_budget_vllm_adapter/`：基于 DEER 控制接口的服务内 Think Budget 实现，加入任务分类、难度、历史预算、Value Min Confidence 等逻辑。
- `think_budget_refactored_final/`：通过 API 转发运行的离线参考实现。算法入口和配置关系见 `think_budget_refactored_final/CLAUDE.md`。
- `think_budget/`：另一份算法实现/迁移来源；除非正在核对迁移差异，优先读 `think_budget_refactored_final/`。
- `dtb.patch`：对 vLLM Chat Completion 协议和 serving 入口的侵入式修改。
- `docs/log/`：端到端汇总、vLLM/vLLM-Ascend 服务日志和人工 profiling 结果。
- `docs/debug/deer_performance_analysis.md`：当前性能诊断、优化优先级和服务器 profiling runbook。
- `docs/design/`：迁移与模块设计文档。

## 推荐阅读顺序

1. 本文件与 `deer/AGENTS.md`。
2. `docs/log/test_results/_SUMMARY.csv`、`docs/log/deer_confidence.txt`。
3. `deer/cot_controller.py`、`deer/state.py`、`deer/context.py`。
4. `dtb.patch`。
5. `think_budget_vllm_adapter/budget_controller.py`、`think_budget_vllm_adapter/deer_think_budget/state.py`、两个 `context.py`。
6. `think_budget_refactored_final/CLAUDE.md` 及其算法模块，用于核对服务内迁移是否保留语义。

## 性能分析口径

每个外部请求用一个 `parent_request_id` 关联所有内部 phase。至少区分：

- `decision`：预算、任务类型、难度和历史决策的 CPU 时间。
- `initial_think` / `iterative_think`：对外可见的思考生成。
- `confidence_probe`：不对外可见、但真实消耗算力的试答分支。
- `final_answer`：最终答案生成。
- `controller_cpu`：tokenize/decode、拷贝、置信度聚合、队列和格式转换。

每个 phase 至少记录 `queue_ms`、`prefill_ms`、`decode_ms`、`prompt_tokens`、`cached_prompt_tokens`、`generated_tokens`、`visible`、`round`、`stop_reason`。不要只用 vLLM 每 10 秒打印的全局吞吐率归因单个请求。

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
- 性能实验产物放在 `docs/log/perf/<run_id>/`，包含 `manifest.json`、请求级 JSONL、phase JSONL、metrics 前后快照和摘要 CSV/Markdown。
- 当实现、默认参数或实测结论变化时，同步更新性能分析文档末尾的变更记录。
