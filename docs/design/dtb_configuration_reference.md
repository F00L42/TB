# DTB 配置与置信度参考

本文说明当前交付实现的配置语义，便于部署者判断参数是否生效、接手开发者定位修改入口。
源码核对基线为 `codex/dtb-static-refactor@2fca7bec756c60522f891983fb2cdf8617823b38`，日期为 2026-11-02；
目标为固定 vLLM 0.23.0 / vLLM-Ascend 0.23.0rc1。本次只补文档，不改变算法、默认 YAML 或 patch。
以下是源码确定的行为，不能代替服务器实际配置、协议、质量或净 E2E 验收。

默认置信度是**本轮隐藏试答中实际采样 token 概率的算术平均**；Value-Min 默认关闭。
置信度阈值与思考预算共同决定能否提前结束思考。`native` 路径不运行置信度计算。

## 1. 配置入口与生效范围

| 层级 | 入口 | 生效范围 |
| --- | --- | --- |
| 服务 YAML | [dtb/config/budget_config.yaml](../../dtb/config/budget_config.yaml) | controller 构造时加载为不可变快照；修改后完整重启，不检查 mtime、不热加载 |
| 请求覆盖 | `enable_think_budget` 和下文六个 `fixed_*` 字段 | 仅当前请求；正值 fixed 覆盖服务值，不改变服务快照 |
| 代码策略 | `DtbAlgorithmPolicy`、试答 cue、token 过滤规则 | 修改源码才能改变；不能作为任意 YAML 或请求参数注入 |
| vLLM 参数 | 原生请求采样参数、服务 `logprobs_mode`、模型容量 | 决定试答采样及返回数值的含义，DTB 复用原生接口 |

服务开关 `RUNTIME.BUDGET_ENABLED` 默认为 `true`（schema 名称为 `budget_enabled`）；请求
`enable_think_budget` 默认为 `false`。两者开启后，仍须经过路由才能进入 DTB。关闭任一开关或命中
`native` 动作时回到原生单次生成。有效 `fixed_budget_tokens` 可在开关开启时强制 DTB；
单独设置 `fixed_prob_th`、`fixed_check_length` 等不改变 native 动作。

候选 YAML 不会按模型名自动选取。部署选择、备份与回滚按[部署手册](dtb_system_routing_deployment.md)执行，
记录实际加载的规范 YAML、源码、patch、模型和启动参数。当前没有读取环境变量来覆盖 DTB 置信度参数的实现。
loader 拒绝未知顶层和 `RUNTIME` 字段；请求中的旧 `tb_*` 字段也不能恢复成配置入口。

## 2. 置信度计算与接受流程

### 2.1 输入来自隐藏试答

有预算和调用容量时，`_ConfidenceProbeState` 在当前思考前缀的独立 child prompt 上追加
`TRIAL_PROMPT`，请求模型生成一段隐藏试答。试答上限为 **50 token**，还受剩余上下文和请求
`max_completion_tokens` / `max_tokens` 限制。probe 强制 `params.logprobs = 1`，
按**实际采样 token ID**提取对应 logprob，不拿每个位置概率最大的候选代替。

每轮重新收集完整试答；DELTA 累加，CUMULATIVE/FINAL_ONLY 使用完整快照，缺失概率位置补 `None`。
评分范围是 `current_trial`，不跨 probe 累积，也不计算整个思维链的平均概率。
隐藏试答不发送给客户端、不追加到公开思考或复用成最终答案；接受后仍单独生成 final。
其 token 进入 `hidden_probe_tokens` 工作量账本，不进入响应的可见 completion usage。

### 2.2 默认平均概率

```text
score = Σ exp(logprob_i) / N
```

`N` 为本轮对齐后的概率位置数。部分缺失位置不贡献分子，但仍保留在分母。
例如两个位置分别为 `log(0.9)` 和 `None`，平均分数为 `0.45`。
默认方法在 telemetry 中叫 `average_fallback`；这个名称也用于正常关闭 Value-Min 的情况，不能据此断言发生故障。

分数表示模型对已采样 token 的概率偏好，不是答案正确率、整段概率乘积或困惑度。
`cal_perplexity()` 是诊断计算，不参与接受判断。

### 2.3 可选 Value-Min

`RUNTIME.enable_value_min_confidence: true` 时，先按 decoded token 文本过滤结构符号、空白、
部分特殊 token 和预置业务字段名，再取有效 token 中最小的 `k` 个 logprob：

```text
k = value_min_bottom_k
score = min(1, exp(mean(最小 k 个有效 Value token logprob)))
```

这是最低 `k` 个概率的几何平均值。只有至少 `k` 个有效 Value token 时才使用该分数；
数量不足、只有结构 token 或无法提取足够 decoded token 时退回默认平均算法。
Value 过滤是 token 文本规则，不是完整 JSON 语义解析或学习得到的分类器。

具体集合在 [token_filter.py](../../dtb/reasoning/token_filter.py)：默认排除 JSON/代码符号，
并在模块导入时注入 `_NAIE_KEY_NAMES`，例如 `faultNumber`、`answer`、`name`；`</think>` 被显式保留。
`set_task_key_names()` / `set_extra_struct_tokens()` 是进程级兼容 setter，不是 YAML 配置项，
不得按并发请求动态调用。调整过滤规则属于算法行为变化，应单独回归。
同文件的 `cal_reasoning_zone_confidence()` 不在当前生产评分调用链中。
`cal_value_min_confidence()` 的 helper 默认 k=3 也不是生产默认；生产 gate 使用服务配置的 k=5。
若已有至少 k 个有效 Value token，其他位置缺失不会额外强制回退。

### 2.4 接受顺序和缺失值

| 判断顺序 | 条件 | 当前行为 |
| --- | --- | --- |
| 1 | 没有任何可用的采样 token/logprob 对 | `score=None`，`confidence_unavailable_fail_open`，直接转 final |
| 2 | 有可用分数，但思考量小于最终 `min_think_budget` | 拒绝本轮试答，理由为 `min_think_budget` |
| 3 | 达到最低思考量，`score >= 最终 confidence_threshold` | 接受，转 final |
| 4 | 其余情况 | `below_threshold`，继续思考；后续仍受轮数/预算/容量约束 |

fail-open 在最低思考量检查之前，所以它不受该最低量限制；这是避免持续依赖失效评分的既有策略，
不是高置信接受。引擎异常、没有任何 vLLM output、取消或 parser 错误不会被此分支包装成成功。

## 3. 直接影响置信度的字段

本表默认值来自规范 YAML / `DtbAlgorithmConfig`，不代表最终场景值或服务器实配。

| 字段 | 默认值与范围 | 效果 |
| --- | --- | --- |
| `RUNTIME.enable_value_min_confidence` | `false`；布尔值 | 选择平均概率或 Value-Min；服务级，不能逐请求覆盖 |
| `RUNTIME.value_min_bottom_k` | `5`；正整数 | Value-Min 取最低 k 个有效 token；不足 k 个则退回平均算法 |
| `RUNTIME.confidence_threshold` | `0.95`；`[0,1]` | 仅值不等于 0.95 时作为全局显式覆盖，详见下方优先级 |
| `RUNTIME.fixed_prob_th` / 请求 `fixed_prob_th` | `0`；`[0,1]` | 正值最高优先覆盖；0 表示不覆盖，不是阈值为零 |
| `SCENES.<scene>.prob_th` | 场景自定义；概率范围 `[0,1]` | 场景接受阈值；规范 YAML 含 0.90、0.92、0.93、0.95 |
| 场景 `confidence_threshold` | legacy 兼容别名 | legacy 模式同节点优先于 `prob_th`；`system_prefix` 拒绝此别名，必须使用 `prob_th` |

最终阈值按以下顺序选取：

1. 合并请求覆盖后，`fixed_prob_th > 0`：使用该值。
2. 否则，`RUNTIME.confidence_threshold != 0.95`：使用该值。
3. 否则，使用当前预算分支返回的场景/历史阈值；没有时使用 `0.95`。

历史分支的 `prob_th` 当前取自选中场景或 general 的配置，不是在线学习到的正确率。
固定总预算分支不读取场景阈值。legacy 场景/历史数值会由 resolver 裁到 `[0,1]`；
system 场景启动校验要求合法概率。部署时应始终提供范围内的有限数值，不依赖隐式裁剪。

| 场景 `prob_th` | RUNTIME `confidence_threshold` | 有效 `fixed_prob_th` | 最终阈值 |
| --- | --- | --- | --- |
| 0.93 | 0.95 | 0 | 0.93 |
| 0.93 | 0.92 | 0 | 0.92 |
| 0.93 | 0.95 | 0.95 | 0.95 |

因此，强制所有进入 DTB 的请求使用 0.95 应设置 `fixed_prob_th: 0.95`；只写
`confidence_threshold: 0.95` 达不到覆盖场景的效果。请求传 `fixed_prob_th: 0` 或省略字段，
都保留服务配置；不能清除服务侧已有正值。要取消服务强制值，修改规范 YAML 并重启。

## 4. 检查时机、最低思考量与停止条件

| RUNTIME 字段 | 默认值/单位 | 对 probe 的影响 |
| --- | --- | --- |
| `check_length` | `200` token，正整数 | 首段思考/流式观察介入参考长度 |
| `min_think_budget` | `100` token，正整数 | 正常置信度接受前的最低思考量；场景对应 `min_think_tokens` |
| `think_stride` | `100` token，正整数 | 拒绝试答后继续思考的参考步长 |
| `num_thinking` | `10` 次，正整数 | 最大 probe 轮数；最后一轮拒绝后，资源允许时仍追加一次 stride，再进入 final |
| `fixed_budget_tokens` | `0`；非负整数 | 正值选择固定总思考预算分支，同时限制思考总量 |
| `fixed_check_length`、`fixed_min_think_tokens`、`fixed_think_stride`、`fixed_num_thinking` | 各为 `0`；非负整数 | 正值覆盖相应预算字段，0 不覆盖；连同 `fixed_budget_tokens`、`fixed_prob_th` 构成六个允许的请求覆盖字段 |
| `max_thinking_tokens` | `65536` token，正整数 | 服务思考硬上限；与场景 `max_think_tokens` 或 fixed 总预算、上下文/请求限制共同收紧 |
| `max_internal_calls` | `20`；0 或至少 2 | 0 表示不按次数限额；probe 前必须为 probe+final 留出两次内部调用名额 |
| `enable_safety_gate` | `false` | 开启后在状态检查点执行耗时门，不改变评分公式 |
| `baseline_time_limit` | `90.0` 秒，非负 | 开门时，耗时超过该值只记录并继续；超过 1.5 倍转 final，不是硬抢占正在执行的 generate |

预算向量最终会被硬上限收紧；同时提供正 `fixed_check_length` 和 `fixed_budget_tokens` 时，
前者不得大于后者。未单设 `fixed_min_think_tokens` 时，固定总预算分支默认以总预算作为最低思考量，
不能假定只设置总预算就允许更早置信度退出。

流式请求先观察原生生成，未达到介入条件、自然结束、工具/思考边界或无法安全接管时，可能不运行 probe。
预算耗尽、轮数/调用额度不足、没有上下文余量、安全门触发，也会跳过后续 probe。
Noemate 750 候选的 `check_length=max_think_tokens=750`：到阈值并交接后已耗尽思考预算，
走 `budget_final`，不能描述为“置信度达到 0.95 后退出”。通用字段存在不等于本分支会用到。

## 5. 场景、难度和历史预算选择

这些字段改变预算来源或试答前缀，不替换平均概率/Value-Min 公式。

| RUNTIME 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `enable_scene_budget` | `true` | 启用场景预算；最终场景决定 `prob_th` 和预算向量 |
| `scene_override` | 空串 | legacy 场景显式选择；system_prefix 不允许设置 |
| `scene_match_min_score`、`scene_match_min_margin` | `3.0`、`1.0`，非负 | legacy 关键词场景匹配/拒识条件，不是置信度阈值 |
| `enable_difficulty_estimator` | `false` | 命中场景时允许合成难度预算，再与场景预算合并 |
| `difficulty_check_length_base`、`difficulty_check_length_max` | `400`、`6000` | 难度预算首检查长度参数 |
| `difficulty_min_think_base`、`difficulty_min_think_max` | `100`、`6000` | 难度预算最低思考量参数 |
| `difficulty_stride_base`、`difficulty_stride_max` | `400`、`6000` | 难度预算步长参数 |
| `difficulty_num_thinking_base`、`difficulty_num_thinking_max` | `10`、`20` | 难度预算轮数参数；上述四组均为正整数且 base 不超过 max |
| `enable_history_budget` | `false` | 未进入 fixed/命中场景分支时，可读取历史 token 分布生成预算 |
| `history_budget_multiplier` | `1.2`，正值 | 历史 warm-start 预算系数 |
| `history_warmup_count`、`history_warmup_strategy` | `3`、`moderate` | warm-start 样本数边界与策略；策略实现使用 conservative/moderate/aggressive |
| `new_task_unlimited` | `true` | 无历史时采用首次完整观察预算策略；仍受服务/上下文硬限制 |
| `history_stride_percentile` | `0.75`，`(0,1]` | history 步长所用 token 分布分位数 |
| `percentile_step` | `0.75`，`(0,1]` | 不等于 0.75 时覆盖上项；不是 Value-Min 的低概率分位数 |

`ROUTING.mode` 默认 `legacy_text`；`system_prefix` 采用启动编译的身份规则，并要求
scene 开启、history/difficulty 关闭、scene_override 为空。`ROUTING.on_unmatched` 默认 `budget`，
可选 `native`；`SCENES.<scene>.action` 默认 `dtb`，也可选 `native`。native 场景不得携带预算字段。
`SCENE_PRIORITY`、legacy `indicators` 与 system `match` 的完整规则见[路由设计](dtb_system_scene_native_routing.md)。

`DIFFICULTY_TIERS` 当前仅被加载保存，生产难度预算计算未读取它；但 loader 仍要求该键存在且为 mapping，
不能直接删除。`HISTORY_NEW_TASK`、`HISTORY_WARM_START`、`HISTORY_HOT_START` 可以省略。
history 的运行参数由 resolver 显式传入，优先于对应策略数据的 fallback；
`HISTORY_HOT_START.low_success_boost` / `high_failure_stride_factor` 当前没有消费者。
历史策略只使用 token 分布，不读取在线正确率来校准置信度，不能仅因 YAML 有键就认为它参与当前决策。
具体消费者见 [difficulty](../../dtb/budgeting/difficulty/budget.py)与[history](../../dtb/history/budget_policy.py)。

## 6. 试答采样、返回概率与固定策略

probe 调用原生 `request.to_sampling_params()`，复用请求/服务默认的 `temperature`、
`top_p`、`top_k`、`min_p`、`seed`、presence/frequency/repetition penalty、stop 等参数。
它们会改变试答内容、结束位置及评分输入；没有独立的 DTB probe temperature 配置。
`max_completion_tokens` 优先于 `max_tokens`，正请求上限还会缩小 50-token 试答上限。

固定上游的 [ModelConfig](../../third_party/vllm/vllm/config/model.py) 支持服务参数 `--logprobs-mode`：

| 模式 | 返回数值 | DTB 使用边界 |
| --- | --- | --- |
| `raw_logprobs`（默认） | 采样处理器、penalty、temperature 等处理前的归一化 log 概率 | 当前默认评分口径；采样参数仍会改变选中的 token 及后续前缀 |
| `processed_logprobs` | 处理后的归一化 log 概率 | 改变评分口径，旧阈值/结果不能直接迁移；需重新验证 |
| `raw_logits` / `processed_logits` | 未归一化分数 | 不满足 DTB 的 `exp(logprob)` 概率语义，不应配合当前置信度算法使用 |

当前 DTB 没有校验引擎 `logprobs_mode`，因此不能把上述限制误写成“代码会拒绝启动”。部署应记录该模式，
保持 `raw_logprobs` 默认口径，并核对目标设备实际返回语义。
固定 Ascend 的 [model_runner_v1.py](../../third_party/vllm-ascend/vllm_ascend/worker/model_runner_v1.py)
直接构造 `AscendSampler()`，没有传入 `model_config.logprobs_mode`；
[Ascend sampler](../../third_party/vllm-ascend/vllm_ascend/sample/sampler.py) 默认仍为 `raw_logprobs`。
因此上游支持四种模式，不等于该 Ascend runner 会响应相同 CLI 切换；须核对实际 runner/MTP 路径并做设备验收。
probe 的 `logprobs=1` 是内部固定采集要求，不是“只计算最高概率候选”；外部请求不必开启 logprobs。

| 固定策略/历史字段 | 当前语义 |
| --- | --- |
| `DtbAlgorithmPolicy.trial_answer_length` | 固定 50，上限可被容量缩小；不能从 YAML 改成其他值 |
| `confidence_scope`、`confidence_missing_input` | 固定 `current_trial`、`fail_open`；未知 RUNTIME 键被 loader 拒绝 |
| `TRIAL_PROMPT` | 定义在生成状态源码，追加在 child prompt；修改属于算法变化 |
| legacy `check_answer_len` | 不决定生产试答长度；system_prefix 模式拒绝该字段 |
| `tb_prob_th`、`tb_check_answer_len`、`tb_enable_value_min_confidence`、`tb_value_min_bottom_k`、`budget_confidence_threshold` | 已退役请求字段；不能以它们配置当前算法，非默认值会被 DTB 配置构造拒绝 |

## 7. 协议、观测和历史存储

| 顶层配置 | 说明 |
| --- | --- |
| `REASONING_PROTOCOL` | `adapter: qwen3` 及部署对应的 `think_end_token_id`、`tool_call_begin_token_id`；ID 非负且不同，须验证模型/tokenizer，不能从字符串推断 |
| `OBSERVABILITY` | `mode: off` 默认；`phase` 记录决策/阶段；`stream_audit` 必须设置非空 `stream_request_ids` 白名单。`queue_capacity` 默认 2048，为正值 |
| `HISTORY_STORE` | `path` 相对 YAML 目录解析；默认 `../data/history/inference_history.jsonl`，`max_entries=5000`、`max_age_days=7`、`queue_capacity=1024`；history 关闭时不创建/读取 store |

`phase` 模式的 `confidence_probe` 记录 `confidence`、`confidence_method`、`average_confidence`、
`value_min_raw`、`pair_count`、`value_pair_count`、`accepted`、`accept_reason`。
即使 Value-Min 关闭，phase 也可能计算反事实 `value_min_raw`；判断实际算法看 `confidence_method`。
`value_min_raw` 是 logprob 域的聚合值，不能直接与概率阈值比较。off 省去不影响决策的诊断计算，
不会切换评分算法；正式性能 run 使用 off，另开 phase run 排障，不混合排名。
更多说明见 [DTB telemetry](../../dtb/README.md#telemetry)及[实验契约](think_budget_generalization_plan.md)。

## 8. 配置示例与确认方法

集成/升级后希望直观看到干预时，先按[8-token 功能冒烟](../maintenance/integration_smoke.md)
启用现有 phase 模式，使用请求级短预算与原生对照；预算直接收束和置信度评分分别检查。
无需新增日志等级或改 general 场景，诊断参数不作为生产推荐。

以下仅为**合并到现有 YAML 的 RUNTIME 片段**，不是完整配置或推荐阈值。不要用片段覆盖整个规范文件。
例如启用 Value-Min，并对进入 DTB 的请求强制使用 0.95：

```yaml
RUNTIME:
  enable_value_min_confidence: true
  value_min_bottom_k: 5
  fixed_prob_th: 0.95
```

修改后重启。该例仍可能因有效 Value token 不足回退平均算法，也可能因 native/预算路径不运行 probe。
若要使用默认平均算法且允许场景阈值，服务配置应为 `enable_value_min_confidence: false`、
`fixed_prob_th: 0.0`、`confidence_threshold: 0.95`，同时确认请求未携带正的 `fixed_prob_th`。

单次请求的扩展字段片段如下；保留原请求的 model/messages/采样等字段：

```json
{"enable_think_budget": true, "fixed_prob_th": 0.95}
```

这不会把 native 场景强制变成 DTB，也不会逐请求开启 Value-Min。
排查“配置为何没生效”时，按加载 YAML/hash → 请求覆盖 → route → 最终 budget decision 的
`source_by_field.confidence_threshold` → 是否存在 probe → `confidence_method/accept_reason` 的顺序核对。
不要仅凭配置文件中出现某个字段、HTTP 200 或 phase 中存在 `value_min_raw` 判断算法已启用。

## 9. 源码与验证索引

| 责任 | 源码 | 对应回归 |
| --- | --- | --- |
| 配置加载、范围和固定策略 | [loader](../../dtb/config/loader.py)、[schema](../../dtb/config/schema.py)、[routing](../../dtb/config/routing.py) | [config](../../tests/test_dtb_config.py)、[system routing](../../tests/test_dtb_system_routing.py) |
| 请求覆盖白名单与退役字段 | [request_controller](../../dtb/request_controller.py)、[serving patch](../../patches/vllm/v0.23.0/dtb_serving.patch) | [budget resolution](../../tests/test_dtb_budget_resolution.py) |
| 最终阈值/预算及来源 | [budget_resolver](../../dtb/budgeting/budget_resolver.py) | [budget resolution](../../tests/test_dtb_budget_resolution.py)、[scene config](../../tests/test_dtb_scene_config.py) |
| 默认公式、实际采样概率提取 | [confidence](../../dtb/reasoning/confidence.py) | [confidence and trial](../../tests/test_dtb_confidence_and_trial.py) |
| Value-Min、缺失回退和过滤 | [confidence_gates](../../dtb/reasoning/confidence_gates.py)、[token_filter](../../dtb/reasoning/token_filter.py) | [confidence and trial](../../tests/test_dtb_confidence_and_trial.py)、[collection optimization](../../tests/test_dtb_collection_optimization.py) |
| probe 采样、接受与转 final | [generation_states](../../dtb/reasoning/generation_states.py)、[safety_gate](../../dtb/reasoning/safety_gate.py)、[deferred_observation](../../dtb/reasoning/deferred_observation.py) | [state machine](../../tests/test_dtb_state_machine.py)、[intervention](../../tests/test_dtb_intervention.py) |
| 上游采样/概率语义 | [request protocol](../../third_party/vllm/vllm/entrypoints/openai/chat_completion/protocol.py)、[sampler](../../third_party/vllm/vllm/v1/sample/sampler.py) | 固定源码检查；真实 tokenizer/设备/HTTP 验收仍需服务器执行 |

本地完整门禁：`python tools/dtb/check_static.py`。改算法时先固定旧行为，再跑相应回归及完整门禁；
本地 mock/合成数据只能证明对应输入的实现契约。更换评分方式、过滤规则、采样口径或阈值后，
须按 [replay 手册](../../validation/replay/README.md)验收协议，并在同模型/版本/输入/采样/并发/cache 条件下
重新评估质量和净 E2E；未执行项目标为 `pending_remote`。

## 变更记录

| 日期 | 变更 | 验证边界 |
| --- | --- | --- |
| 2026-11-02 | 汇总配置层级、置信度公式/回退、阈值优先级、probe 采样与跳过条件、上游概率模式和源码入口 | 当前源码核对；文档补充，不新增服务器效果结论 |
