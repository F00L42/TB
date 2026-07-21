# Think Budget：vLLM 思维链预算控制

当请求设置 `enable_think_budget=true` 时，vLLM 会进入 Think Budget 控制器。它把一次推理拆成“思考、试探答案、继续思考、最终回答”等阶段，并在每个阶段检查预算和置信度。

这套机制会增加推理质量控制能力，但每增加一次迭代，都会新增 vLLM 子请求。因此服务端需要控制同时运行的父请求数；算法本身仍按原有动态预算、轮数和试探答案长度运行。目标集成版本为 vLLM `v0.23.0`，详细成本模型见[性能诊断报告](../docs/debug/deer_performance_analysis.md)。

## 使用方式

生产请求只允许 Think Budget 总开关和六个固定输入。动态预算、任务分类、难度/历史策略、会话 TTL 与并发保护均由服务端静态配置控制，客户端不能覆盖。

```json
{
  "model": "served-model",
  "stream": true,
  "enable_think_budget": true,
  "fixed_check_length": 256,
  "fixed_num_thinking": 4
}
```

允许的固定输入如下；数值为 `0` 时表示“不固定，交给动态算法计算”。

| 字段 | 固定的值 |
| --- | --- |
| `fixed_budget_tokens` | 总思考 token 预算 |
| `fixed_num_thinking` | 思考轮数 |
| `fixed_think_stride` | 每轮思考步长 |
| `fixed_check_length` | 初始思考长度 |
| `fixed_prob_th` | 置信度阈值 |
| `fixed_min_think_tokens` | 最小思考 token 数 |

`think_budget_config`、所有 `tb_*` 与 `budget_*` 字段、任务/场景覆盖、会话 ID、续接和 TTL 字段均会被协议校验拒绝。vLLM 的请求模型必须保持 `extra=forbid`，使未知字段在进入控制器前返回 4xx。

## 参数优先级

请求只可逐项固定六个参数。正值固定后不会被计算模块覆盖；其他值仍按服务端默认值及任务类型、难度、历史模块的原有顺序计算。例如只传入 `fixed_check_length: 256` 时，只跳过初始长度计算，轮数、步长和预算仍会照常计算。

服务端只读取 `config/budget_config.yaml` 的 `RUNTIME` 段，不再读取第二份 `settings.py`。新任务无历史时使用 general 的 cold_start（3000 token、8 轮）。任务分类命中非 `general` 类型时运行难度估计；未命中时才运行历史预算，因此两套动态模块不会写入同一请求并互相覆盖。

`RUNTIME.BUDGET_ENABLED: false` 是服务端总关闭开关：即使客户端发送 `enable_think_budget: true`，补丁也会跳过 Think Budget 状态机并继续原生 vLLM。客户端传入除总开关、六个 `fixed_*` 和正常模型调用字段外的 Think Budget 参数会返回 4xx。

## 本地实现的决策边界

本地 Think Budget 只负责“在进入推理前算出参数”，实际的思考、试探答案、继续思考和最终回答仍完全由 DEER 状态机执行。因此预算模块不会把一次请求改写成参考项目中的另一套循环，也不会改变 DEER 的流式续接语义。

计算顺序为：固定预算（如有）→ 任务类型 → 难度 → 历史。难度估计开启时，历史模块只能调整 `think_stride`；初始思考长度、轮数和总 token 预算保持难度模块结果，避免同类请求因历史采样波动而频繁改变子请求数量。没有开启难度估计时，历史模块仍按原算法补全全部未由外部传入的预算字段。

`task_type` 与 `scene_type` 不再是外部字段，只能在服务端配置或内部决策链中使用。配置文件中的 `TASK_TYPE_INDICATORS` 可维护检索、分类和推理的规则词；其中 `statistics_aggregation` 用于带有多行 `*_count` 统计数据且要求计数/求和的请求，避免其被误路由为普通检索。

生产环境必须安装 `PyYAML`，否则系统会记录警告并退回内置默认配置，`budget_config.yaml` 的静态配置不会生效。

完整的决策边界、字段映射、冗余审查和服务器验证边界见[适配器说明](../docs/think_budget_适配器说明.md)。

## 置信度检查与 trial 请求

每次未自然结束的思考都会生成一个**不写回主上下文**的 trial answer。它是一次独立的 vLLM 子请求，原因是置信度门控要评估“若现在结束，模型会怎样回答”的答案区 token；思考区 token 不能替代答案区 token。trial 请求固定 `logprobs=1`，vLLM 会返回实际采样 token 及少量候选 token 的分数，控制器只按 `token_ids` 精确读取实际采样 token 的 logprob，绝不依赖候选字典顺序。

默认只对 trial answer 请求 logprob，使用 Value-Min 评分：过滤结构 token 后取答案 value token 中最差 K 个 logprob 的均值。启用服务端的 `enable_dual_channel` 或 `enable_full_chain_confidence` 时，初始思考和每轮继续思考也会请求 `logprobs=1`；控制器累计完整思维链后，以“推理区最差 Min-%”与“答案区 Bottom-K”取严格 AND 分数。严格模式无法取得完整 sampled-token 数据时，评分为 0 并继续思考，不会悄悄退回较弱模式。

## 运行日志

Think Budget 不依赖 vLLM/Python 的日志等级；运行期诊断直接写入 API Server 标准输出，但不会记录模型生成的完整文本。默认每个模型 phase 只 flush 一条 `TB_PHASE` JSON，包含 parent/child request ID、phase、轮次、queue/prefill/decode/wall、prompt/cache/generated token、可见性、停止原因和 probe 判定。缺失的 vLLM stats 写为 `null`，不会伪装成 0 ms。`TB_PROFILE` 还会输出整条链路、各决策/引擎模块耗时以及 visible/hidden/final token 账本。

`RUNTIME.verbose_stage_logging: true` 可临时恢复 `TB_STAGE`/`TB_CONFIDENCE` 的详细 enter/complete 日志；生产性能实验保持默认 `false`。用以下命令把服务日志转成标准产物：

```bash
python tools/extract_tb_phase_metrics.py \
  docs/log/perf/<run_id>/server.log docs/log/perf/<run_id>/
```

可据此检索 `request_id`，定位请求是否停在初始思考、某次 rethink、trial 置信度检查或最终回答阶段；日志不包含 prompt、trial answer 或最终答案正文。

## 流式处理

Think Budget 与 DEER 一样使用请求绑定的无界输出队列：不设置父请求并发闸门、不维护会话注册表、不保留 TTL 后台任务。并发、排队、KV cache 和抢占完全交由 vLLM 调度；SSE 客户端断开时会立即取消该父请求的状态机及其子请求链。`think_budget_session_id`、续接和 TTL 请求字段仍不属于公网接口。

## 观测与排障

每个请求会记录总耗时、预算决策模块耗时以及 `initial_think`、`iterative_think`、`confidence_probe`、`final_answer` 的阶段耗时。服务端还应采集 vLLM `/metrics`：

- `vllm:num_preemptions`：持续增长通常表示 KV cache 抖动；
- `vllm:kv_cache_usage_perc`：接近 1 时不要继续提高预算请求并发；
- `vllm:prefix_cache_hits/queries`：确认扩展 prompt 是否真正复用了前缀；
- `vllm:request_prefill_time_seconds` 和 `vllm:request_queue_time_seconds`：区分重复 prefill 与排队问题。

如果最终答案吞吐很低，先在服务端配置调整动态策略，或只通过允许的 `fixed_*` 字段做隔离实验，再检查 preemption；不要只看客户端最终 tokens/s。
