# DTB 代码清理与 Telemetry 重构计划

## 1. 设计目的

本计划面向 Dynamic Think-Budget Control（下文统一简称 **DTB**）的生产实现，解决当前
`think_budget_vllm_adapter` 在多轮迁移和工具调用排障后形成的命名混杂、职责耦合、默认热路径
观测开销和实验资产残留问题。

设计起点：

- 基线 revision：`bc142570103f86ca53ed656c75afd999bd13cbbd`；
- 设计日期：2026-08-20；
- 目标运行环境：vLLM `v0.23.0`、vLLM-Ascend `v0.23.0rc1`；
- 算法和实验口径：[`think_budget_generalization_plan.md`](think_budget_generalization_plan.md)；
- 性能账本和服务器验证：
  [`deer_performance_analysis.md`](../debug/deer_performance_analysis.md)；
- 历史三状态语义：
  [`think_budget_vllm_adapter_refactor_plan.md`](../debug/think_budget_vllm_adapter_refactor_plan.md)；
- 工具调用 parser-facing 契约：
  [`tool_call_streaming_debug_plan.md`](../debug/tool_call_streaming_debug_plan.md)。

本次工作的目标是重构和清理，不改变已经由 `run_07` 与工具调用 replay 固化的算法、路由、
parser-facing 输出和取消语义：

1. 产品、代码、配置和结构化事件统一使用 DTB 术语；DEER 只作为历史集成来源，不再出现在
   生产算法包边界内。
2. telemetry 默认关闭。关闭态不计时、不构造事件、不序列化、不 hash、不复制 token、不启动
   writer、不输出成功路径日志；只能留下算法本来就需要的状态更新和异常日志。
3. 保留 `bc1425` 中有复用价值的 phase 账本和工具流审计能力，但把低基数性能 telemetry、
   高基数 stream audit、异常日志分成三个边界清晰的通道。
4. 删除已确认无调用的模块、兼容别名、重复配置和重复账本；拆分三个过大的生产文件。
5. 从主分支移除不可维护或含原始模型正文的实验产物，只保留最小回归 fixture、可复现工具、
   聚合结论和部署 patch。

### 1.1 非目标

- 不重新选择 13 场景预算，不改变 fixed、scene、difficulty、history 的优先级；
- 不改变 Thinking → ConfidenceProbe → FinalAnswer 三状态行为；
- 不借重构实现阶段二 native bypass；
- 不把 vLLM `v0.23.0` 或 vLLM-Ascend 源码复制进本仓库；
- 不在首轮内部重构中修改公网请求字段 `enable_think_budget`。是否迁移为 `enable_dtb` 必须作为
  单独的协议版本变更处理；不能为了命名整洁同时保留两套永久别名。

### 1.2 “关闭态无性能影响”的定义

“无性能影响”不能只解释为“不打印日志”，也不宣称增加一个条件分支在物理上绝对零成本。
本计划采用两层判据：

1. **源码判据：** telemetry 关闭时，不执行任何 telemetry 专属的时钟读取、metrics 反射、
   字典/dataclass 构造、JSON 序列化、文本/token hash、列表复制、队列写入、后台任务创建或
   stdout/logger I/O。phase 边界最多保留一次对请求级不可变开关的判断。
2. **实测判据：** 相同 revision、相同 DTB 算法配置和相同负载下，重构后 `off` 相对重构前
   无 telemetry 代码基线的 paired E2E、P50、P95 和吞吐均不得出现超出重复实验噪声的退化。
   目标 NPU 实验完成前，只能称为“源码关闭”，不能称为“性能已验证无影响”。

## 2. 当前实现检视

以下结论来自基线 revision 的源码和 Git 资产；“源码确定”不等于已经在目标 NPU 实测。

| ID | 发现 | 证据 | 影响 | 处置 |
| --- | --- | --- | --- | --- |
| F-01 | DTB、Think Budget、DEER、`TB_*` 四套名称并存 | `think_budget_vllm_adapter/__init__.py`、`deer_think_budget/`、日志前缀 | 模块归属和维护边界不直观 | 统一内部名称为 DTB，原子更新 import 和 patch |
| F-02 | phase telemetry 实际默认开启 | `unified_config.py:61`、`budget_config.yaml:42`、`context.py:219`、`telemetry.py:81` 均为 `True` | 生产默认执行结构化日志热路径 | 默认改为 `off`，观测配置从算法配置拆出 |
| F-03 | `emit_phase_metrics=false` 只跳过最后的 `print` | `TimingCollector.record_phase()` 在开关判断前读取 metrics、构造 record、更新 ledger | “关闭”仍有 CPU、分配和反射成本 | 关闭态不创建 recorder，调用点在计时/构造前短路 |
| F-04 | `TB_PROFILE` 每个请求无条件 `snapshot()`、打印并 `flush` | `state.py:976-982` | 即使 phase 输出关闭仍有请求末尾 I/O | request summary 归入启用态 sink |
| F-05 | `TB_ROUTE` 和 `TB_POLICY` 不受统一观测开关控制 | `dtb.patch`、`patches/serving_patch.py`、`context.py:81-91` | 无法通过一个开关得到安静的成功路径 | 成功事件归 telemetry；异常日志始终保留 |
| F-06 | 工具流审计关闭态设计正确，但实现嵌入核心请求上下文 | `deer_think_budget/context.py:198-346` | context 引入 `os/json/hashlib` 和大段高基数诊断职责 | 迁入独立 `stream_audit`；保留构造前短路 |
| F-07 | telemetry `BudgetLedger` 与算法状态重复计数 | `BudgetLedger` 和 `RequestAlgorithmState` 同时维护 thinking/probe/final/calls | 双写可能漂移；history 反向依赖 telemetry | history 读取算法状态，删除 telemetry ledger |
| F-08 | history 默认关闭但 recorder 仍在 controller 构造时加载文件，并在每个请求结束后写入 | `BudgetController.__init__()`、`_record_history()`、`AsyncHistoryRecorder.__init__()` | 默认路径有磁盘检查、内存索引、任务和写入副作用 | 仅 `enable_history_budget=true` 时构造和记录 |
| F-09 | 配置并非真正单一来源，且每次查询检查文件 mtime | YAML 与 `_RUNTIME_DEFAULTS`/`_SCENE_DEFAULTS` 重复；`load_budget_config()` 被请求路径多次调用 | 漂移风险和无意义系统调用 | 启动时一次性加载并校验 YAML；失败即 fail-fast |
| F-10 | 三个核心文件职责过载 | `budget_controller.py` 约 980 行、runtime context 约 485 行、state 约 991 行 | 决策、vLLM 接入、parser 边界和 telemetry 难以独立验证 | 按 integration/decision/runtime/observability 拆分 |
| F-11 | 存在仅供旧导入或旧测试的兼容层 | `BudgetConfig`、`ThinkBudgetConfig`、`BudgetRequestContext`、四个旧 state alias、`_build_budget_config` 等 | 保留已删除概念并扩大 API 面 | 仓库内原子迁移后删除，不建立第二代别名 |
| F-12 | 存在已确认无生产调用的迁移残留 | `optimizations/complexity_analyzer.py` 只由包 `__init__` 重导出；若干 difficulty helper 仅定义未使用 | 无效代码和测试负担 | 完成外部消费者审计后删除 |
| F-13 | vLLM 集成有两份表达 | 根目录 `dtb.patch` 与 `patches/serving_patch.py`/`protocol_patch.py` | 容易发生 patch 与说明代码漂移 | 选择 deployable patch 为唯一制品，说明进入文档/测试 |
| F-14 | 自动化测试反向依赖 `docs/debug` 脚本和原始结果 | `tests/test_tool_call_debug.py` import `docs.debug.tool_call.*` | 文档目录变成代码依赖，无法清理实验资产 | 可复用逻辑迁入 `tools/dtb/`，fixture 迁入 `tests/fixtures/` |
| F-15 | 主分支仍有约 7 MB 单个原始 replay 文件 | `docs/debug/tool_call/task29-pass1-26.jsonl` 为 7,065,753 bytes | 仓库体积、敏感正文和长期维护成本 | 从主分支删除，保留归档 commit 和聚合结论 |

### 2.1 当前 telemetry 实际数据流

```mermaid
flowchart LR
    R["DTB 请求"] --> C["TimingCollector 总是创建"]
    C --> M["measure 总是 perf_counter + 字典更新"]
    M --> P["record_phase 总是读取 metrics/构造 record"]
    P --> E{"emit_phase_metrics?"}
    E -- "true" --> O["TB_PHASE JSON + flush"]
    E -- "false" --> N["只跳过输出"]
    P --> L["BudgetLedger 重复记账"]
    L --> H["history 每请求持久化"]
    R --> F["finally: TB_PROFILE 总是 snapshot + flush"]
```

关闭 `emit_phase_metrics` 后，图中只有 `TB_PHASE` 输出节点被跳过；这不符合默认关闭目标。

### 2.2 `bc1425` 中应保留与应收敛的内容

应保留：

- reasoning boundary 的 text/token ID 对齐审计；
- raw/forwarded digest、丢弃 token 和 terminal origin；
- final child terminal 原样透传的回归测试；
- native parser 分片等价验证和固定版本 parser patch；
- phase 级 queue/prefill/decode、visible/hidden/final token 账本。

应收敛：

- `TB_TOOL_STREAM_DEBUG` 从每请求读取的环境变量改为启动时解析的 DTB 观测配置；
- raw stream audit 与低基数 phase telemetry 分开命名、分开 schema、分开开关；
- per-delta 记录器从核心 context 移出，仅启用时 lazy import/构造；
- 删除单次 replay 大文件、人工脚本和迭代过程文档，保留最小 fixture、工具和最终结论。

## 3. 目标概念与命名

### 3.1 统一概念

| 概念 | 定义 |
| --- | --- |
| DTB | Dynamic Think-Budget Control，产品算法和所有生产代码的唯一名称 |
| state | DTB 控制状态，只允许 Thinking、ConfidenceProbe、FinalAnswer |
| phase | 一次 decision 或一次 vLLM child generate 的可计量工作单元 |
| probe | 不对客户端可见、但消耗模型算力的 trial answer |
| phase telemetry | 默认关闭、低基数、用于性能账本的结构化事件 |
| stream audit | 默认关闭、高基数、可能含模型正文/token ID 的 parser 取证事件 |
| operational log | warning/error/cancel 等运维日志，不用于性能统计 |
| history store | DTB 算法的可选在线历史输入，不属于 telemetry |

“telemetry” 不再泛指所有打印；尤其不能把包含 raw text 的 stream audit 描述为低开销性能
telemetry。

### 3.2 包和文件命名

目标生产结构：

```text
dtb/
├── controller.py                 # 稳定门面；组装一次请求
├── model.py                      # DtbPlan、ThinkingBudget、DtbRequestState
├── config/
│   ├── loader.py                 # 启动时一次性加载和校验
│   ├── schema.py                 # DtbRuntimeConfig、DtbObservabilityConfig
│   └── budget_config.yaml        # 唯一部署配置源
├── decision/
│   ├── planner.py                # fixed/scene/difficulty/history 决策
│   ├── scene_router.py
│   └── difficulty.py
├── runtime/
│   ├── executor.py               # 三状态驱动器
│   ├── request_context.py        # vLLM identity、prompt 和输出队列
│   ├── phases.py                 # 三个 state/phase 的生成逻辑
│   ├── output_boundary.py        # parser-facing text/token/terminal 契约
│   └── confidence.py
├── history/
│   ├── policy.py
│   └── store.py
├── observability/
│   ├── config.py
│   ├── phase.py
│   ├── stream_audit.py
│   └── writer.py
└── integration/vllm/
    └── README.md                 # patch 契约，不复制 patch 实现
```

仓库根部继续保留 `dtb.patch` 作为生产应用制品，并通过固定 checkout 的
`git apply --check` 防漂移。第一轮不同时移动 patch，以免部署脚本和 Python 包迁移难以归因。

### 3.3 类型和事件迁移表

| 当前名称 | 目标名称 | 说明 |
| --- | --- | --- |
| `BudgetController` | `DtbController` | 请求级稳定门面 |
| `AlgorithmConfig` | `DtbRuntimeConfig` | 只含算法和执行配置 |
| `PerformancePolicy` | `DtbExecutionPolicy` | 仅保留真正的不变量和 policy version |
| `BudgetVector` | `ThinkingBudget` | 明确是思考预算，不是通用向量 |
| `BudgetPlan` | `DtbPlan` | 决策结果 |
| `RequestAlgorithmState` | `DtbRequestState` | 算法必需状态和模型工作量唯一账本 |
| `RequestStateContext` | `DtbRequestContext` | vLLM 请求身份和可变 prompt |
| `AsyncReasoningOrchestrator` | `DtbExecutor` | 驱动三状态，不使用泛化的 reasoning 名称 |
| `TimingCollector` | 删除并拆分 | 变为可选 `DtbPhaseRecorder` 和异步 sink |
| `TB_PHASE` | `DTB_PHASE` schema 3 | 提取器过渡期双读，生产只写新前缀 |
| `TB_TOOL_STREAM` | `DTB_STREAM_AUDIT` schema 1 | 明确高成本和敏感性 |
| `TB_PROFILE` | `DTB_REQUEST_SUMMARY` | 仅 phase telemetry 启用时输出 |

内部 import 和 `dtb.patch` 在同一提交原子切换，不新增 `think_budget_vllm_adapter → dtb` 的长期
shim。外部 HTTP 字段保持原名，直到有独立的客户端迁移计划。

## 4. Telemetry 设计

### 4.1 配置模型

观测配置不再属于 `DtbRuntimeConfig`：

```python
class DtbTelemetryMode(str, Enum):
    OFF = "off"
    PHASE = "phase"
    STREAM_AUDIT = "stream_audit"


@dataclass(frozen=True)
class DtbObservabilityConfig:
    mode: DtbTelemetryMode = DtbTelemetryMode.OFF
    queue_capacity: int = 4096
    stream_request_ids: frozenset[str] = frozenset()
```

规则：

1. 配置只在服务启动时解析一次；请求不能覆盖。
2. `OFF` 是 YAML、dataclass 和无 YAML 测试构造的唯一默认值。
3. `PHASE` 记录 decision 和各 child phase，不访问模型正文。
4. `STREAM_AUDIT` 包含 phase telemetry，并允许记录 raw/forwarded 内容；生产启用时必须显式
   配置 request ID allowlist。空 allowlist 不审计任何请求，防止误采全量正文。
5. `TB_STAGE`/`TB_CONFIDENCE` 不再作为第四套模式保留；有价值的字段进入 phase 或 stream
   schema，其余删除。

### 4.2 关闭态路径

```mermaid
flowchart TD
    S["服务启动"] --> C{"mode == off?"}
    C -- "是" --> N["controller.telemetry_factory = None"]
    C -- "否" --> F["构造 recorder factory + bounded writer"]
    R["请求进入"] --> Q{"factory is None?"}
    Q -- "是" --> A["直接执行 DTB；不创建观测对象"]
    Q -- "否" --> T["创建 request recorder"]
    A --> X["算法状态记账 / 可选 history"]
    T --> X
```

具体约束：

- `decision_trace` 只在 recorder 存在时创建；
- `DtbPlan.to_event_dict()` 只在 telemetry/history 确有消费者时调用；
- phase start/end 的 `perf_counter_ns()` 只在 recorder 存在时读取；
- `_interval_ms()`、vLLM metrics 反射和 stop reason 规范化只在 `finish_phase()` 内发生；
- `stream_output()` 的普通路径只做 parser-facing 处理和 `result_queue.put()`；stream audit 参数、
  token list 和 digest 在启用分支内部构造；
- `DTB_REQUEST_SUMMARY`、route、policy 事件只由 recorder 发出；
- warning/error 使用标准 logger，禁止正常完成路径 `print(..., flush=True)`。

### 4.3 算法账本与 telemetry 解耦

`DtbRequestState` 是模型工作量的唯一运行时账本：

```text
thinking_tokens
hidden_probe_tokens
final_answer_tokens
child_generate_calls
completion_status
```

这些字段本来就参与预算、调用上限和 history，因此不算 telemetry 开销。删除
`telemetry.BudgetLedger`，避免同一 token 在状态机与 telemetry 双写。

- history 开启时，从 `DtbRequestState` 构造 `HistoryOutcome`；
- phase telemetry 开启时，从同一状态生成 request summary；
- 两者都关闭时，不构造任何 dict 或持久化对象。

### 4.4 启用态写入

phase 结束后，request recorder 只提取 primitive 字段并用 `put_nowait()` 提交给有界 writer；
JSON 序列化和日志写入不在请求 coroutine 中执行。队列满时：

- 不阻塞请求，不丢最终答案；
- 增加 `dropped_events` 计数并限频输出一次 warning；
- 本轮性能 run 因 telemetry 不完整而不可用于裁决，不能静默继续排名。

服务退出和测试可显式 `flush(timeout)`；正常请求不等待 flush。事件仍以
`parent_request_id + child_request_id + phase + round` 关联，不依赖日志物理顺序。

### 4.5 Schema 和兼容策略

`DTB_PHASE` schema 3 保留当前 schema 2 的有效字段，并统一：

- `variant: "dtb"`；
- `policy_version`；
- decision 的 route、effective plan、`source_by_field` 和模块耗时；
- child phase 的 queue/prefill/decode、prompt/cache/generated token；
- probe assessment 和 final answer reason。

`tools/extract_tb_phase_metrics.py` 在一个过渡周期内同时读取：

- 历史 `TB_PHASE` schema 1/2；
- 新 `DTB_PHASE` schema 3。

写端不双写，避免性能 run 重复计数。完成现存历史 run 回放测试后再删除旧 schema 解析；旧日志
文件本身不需要重写。

`DTB_STREAM_AUDIT` 是独立 schema，不进入 phase 提取器。它必须记录安全告警：事件可能含模型
正文、token ID 和工具参数，只能用于定向取证，不得作为常驻生产日志。

## 5. 模块清理计划

### 5.1 直接删除项

以下项目在仓库内无生产调用；完成一次外部部署 import 审计后删除：

- `optimizations/complexity_analyzer.py` 及其 `__init__` 重导出；
- `difficulty_estimator.py` 中仅定义未使用的 `has_math_content`、`has_code_content`、
  `has_logic_content`；
- `BudgetConfig`、`ThinkBudgetConfig`、`BudgetRequestContext`；
- `_build_budget_config`、`_emit_think_end_once`；
- `_InitialThinkState`、`_IterativeThinkState`、`_ConfidenceCheckState`、`_AnswerState`；
- request context 中仅服务旧日志命名的 `trial_count`、`api_call_count`、`thinking_rounds` proxy；
- 不可配置却重复写入 YAML 的 `PERFORMANCE_POLICY` flags 和
  `get_performance_policy_config()`；policy version 与固定 50-token probe 改为源码不变量；
- `patches/serving_patch.py`、`patches/protocol_patch.py` 说明性副本；部署实现只保留
  `dtb.patch`。

删除前要求 `rg` 在生产包、测试、`dtb.patch` 和服务器启动脚本中均无剩余消费者。不能因为
名称像旧代码就直接删除。

### 5.2 配置收敛

当前 Python `_SCENE_DEFAULTS` 与约 500 行 YAML 重复。目标为：

1. `budget_config.yaml` 是 scene、runtime 和 history 参数唯一来源；
2. 启动时加载为不可变 `DtbServiceConfig` 并完成全部 unknown-key、类型、范围和 scene 引用校验；
3. 缺失 PyYAML、配置缺失或配置非法时服务启动失败，不能静默回退到另一套预算；
4. 请求路径只读取已解析对象，不做 `exists/getmtime/open/yaml.safe_load`；
5. 若未来确需 reload，提供显式、原子替换且有配置签名的管理动作，不在 getter 中隐式 reload。

这既是清理，也是实验身份约束：静默 fallback 会使 manifest 中记录的配置与实际预算不一致。

### 5.3 Controller 和 runtime 拆分

`DtbController` 只保留：

- 检查 server enable；
- 取得 vLLM 原生 route identity；
- 调用 planner；
- 组装 request context 与 executor；
- 把 executor 输出交回原生流式/非流式 formatter；
- 在取消/异常/finally 中收敛可选 history 与 telemetry 生命周期。

下列逻辑移出 controller：

- 最新用户消息提取 → `integration/vllm/request_input.py`；
- fixed/scene/difficulty/history 合并与 clamp → `decision/planner.py`；
- 输出队列聚合 → `runtime/output_stream.py`；
- response formatter 参数适配 → `integration/vllm/response.py`。

`runtime/request_context.py` 不再包含 JSON/hash/env/logging；`runtime/output_boundary.py` 独立维护
text/token/logprobs/terminal 的 parser-facing 不变量，防止普通状态重构再次破坏工具调用。

### 5.4 History 边界

history 是可选算法模块，不是“顺便采集”的 telemetry：

- `enable_history_budget=false` 时不创建 recorder、不读历史文件、不维护索引、不写结果；
- history store 路径、容量和保留期进入显式服务配置；
- `record_inference()` 这类离线入口移到工具层，生产 store 只提供 `lookup()`、`record()`、
  `flush()`；
- error/cancel 仍可记录诊断状态，但不进入有效 token 分位数；
- native first observation 未实现前继续按泛化计划标记为阶段二缺口。

### 5.5 实验资产清理

| 当前资产 | 处理 | 保留的长期价值 |
| --- | --- | --- |
| `docs/debug/tool_call/task29-pass1-26.jsonl`（约 7.1 MB） | 从主分支删除；用归档 commit 标识原始证据 | 聚合结论和 commit ID |
| `docs/debug/tool_call/chunks-4-fail.jsonl` | 裁剪、脱敏为 `tests/fixtures/dtb/tool_stream_failure.jsonl` | 最小失败回归 |
| `docs/debug/tool_call/test.py`、`start.sh` | 删除；有效命令写入 runbook | 可执行说明 |
| `analyze_tool_call_run.py`、`replay_tool_calls.py` | 去掉现场路径和重复逻辑后迁入 `tools/dtb/` | 可复现 paired replay/分析 |
| `test_parser_partition_equivalence.py` | 迁入 `tools/dtb/verify_parser_partition.py`，最小断言进入 `tests/` | 固定上游 parser 等价验证 |
| `tests/test_tool_call_debug.py` | 改为 import `tools/dtb` 并使用最小 fixture | 自动化回归 |
| 约 100 KB 的工具调用排障长文 | 提炼为 parser 契约、最终根因、补丁身份和 run 汇总；迭代日记留在历史 commit | 可维护设计依据 |
| `think_budget_refactored_final/` 源码镜像 | 把仍有效的历史裁决迁入 `docs/archive/` 后删除镜像 | 历史说明，不保留第二套可执行实现 |

`think_budget/` 继续作为 AGENTS.md 定义的离线参考实现；`deer/` 继续作为历史集成语义参考；
结构化 `docs/log/perf/<run_id>/` 不属于本轮清理对象，仍按 manifest 和 RESULT 规则保存。

## 6. 核心接口

关键内部接口控制在以下范围：

```python
class DtbPlanner:
    def plan(self, request: DtbDecisionInput) -> DtbPlan: ...


class DtbExecutor:
    async def run(self, context: DtbRequestContext) -> None: ...


class DtbPhaseRecorder:
    def begin(self, phase: DtbPhaseKey) -> DtbPhaseHandle: ...
    def finish(self, handle: DtbPhaseHandle, result: object) -> None: ...
    def finish_request(self, state: DtbRequestState) -> None: ...


class DtbHistoryStore:
    def lookup(self, scene: str) -> DtbHistoryStats: ...
    def record(self, outcome: DtbHistoryOutcome) -> bool: ...
    async def flush(self, timeout_s: float) -> bool: ...
```

关系为：controller 调用 planner 得到不可变 plan，executor 只消费 plan 和 request context；
history 只通过 planner lookup 和请求完成后的 record 参与；telemetry recorder 是可选旁观者，任何
返回值都不能改变状态迁移、输出或错误处理。

## 7. 实施顺序

### P0：冻结语义和基线

1. 固定基线 revision、`dtb.patch` hash、YAML hash 和当前测试清单；
2. 为三状态黄金轨迹、stream/non-stream、取消、工具调用 terminal、fixed/scene/history 决策保存
   测试基线；
3. 记录当前默认配置下实际输出的 `TB_*` 事件，作为“需要消失”的反向基线；
4. 在目标服务器准备 telemetry-off A/B 清单，但本地不宣称性能通过。

### P1：统一术语和目录，不改行为

1. 先建立本文件第 3 节术语；
2. 机械迁移 `think_budget_vllm_adapter` → `dtb`、`deer_think_budget` → `runtime`；
3. 原子更新 tests、`dtb.patch`、工具和文档 import；
4. 保持 HTTP 字段、日志 schema 和算法输出不变；
5. 全仓 `rg` 确认生产代码不再出现旧包名和 DEER 算法命名。

P1 单独提交，便于通过 diff 确认只有 move/rename/import，没有行为变化。

### P2：Telemetry 真正默认关闭

1. 引入独立 observability config，默认 `off`；
2. 将 phase recorder 和 stream audit 从算法 config/context 拆出；
3. 先建立关闭态测试，再移动计时、record 构造和 summary 输出；
4. 将 `TB_ROUTE`、`TB_POLICY`、`TB_PROFILE`、stage/confidence 成功日志纳入统一模式或删除；
5. 将 stream audit 改为 allowlist、lazy construction；
6. 提取器双读新旧 phase schema。

### P3：去重和模块拆分

1. 先让 history 读取 `DtbRequestState`，再删除 `BudgetLedger`；
2. 拆 planner/controller/runtime output boundary；
3. 删除兼容 alias、死模块和固定 policy 配置；
4. 配置改为启动时单次、强校验、无 fallback；
5. history disabled 路径不实例化 store。

每个小步都保持黄金轨迹不变，禁止一次提交同时改变预算算法和文件布局。

### P4：实验资产和文档收口

1. 先生成最小 fixture 并让测试通过；
2. 再移动可复现工具，解除 tests → docs/debug 依赖；
3. 提炼长文和历史裁决；
4. 最后删除原始 JSONL、一次性脚本和历史源码镜像；
5. 更新 `AGENTS.md`、adapter README、两份背景文档和变更记录。

### P5：服务器验收

按 [`deer_performance_analysis.md`](../debug/deer_performance_analysis.md) runbook 在目标服务器执行：

1. 同一进程配置、同一数据集、同一顺序/并发/seed/cache，至少三次旧基线与新 `off`；
2. 分别运行 `phase` 和定向 `stream_audit`，确认字段完整并单列启用成本；
3. telemetry off 的正式性能结果中不得出现 `DTB_PHASE`、`DTB_REQUEST_SUMMARY`、
   `DTB_STREAM_AUDIT` 或成功路径 `TB_*`；
4. phase wall、Prometheus 和客户端 E2E 仍按原口径闭合；
5. 工具调用 paired replay、流式/非流式、取消和 parser partition 测试全部通过。

## 8. 验收标准

### 8.1 代码与命名

- 生产包统一为 `dtb`；`deer_think_budget` 不再存在；
- 除历史文档、兼容读取和 HTTP 协议字段外，生产代码不出现 `Think Budget`/`TB_*`；
- controller、planner、runtime、history、observability、vLLM integration 依赖方向单向；
- tests 不 import `docs/debug`；
- 删除项均有全仓零引用证据。

### 8.2 Telemetry 关闭态

- 默认配置为 `off`；
- off 下 recorder/writer/history（未启用时）均不构造；
- off 下 telemetry builder、serializer、hash、stdout flush 的 spy 调用数均为 0；
- off 与 phase/stream mode 产生完全相同的 parser-facing output、token IDs、finish/stop reason 和
  状态迁移；
- error/cancel 不因 telemetry 关闭而被吞掉。

### 8.3 Telemetry 启用态

- phase schema 可按 parent/child request ID 完整提取，visible/hidden/final token 不重不漏；
- stream audit 只命中 allowlist，请求正文不意外进入普通 phase 日志；
- writer 满载不阻塞请求，丢事件有显式计数并使 run 完整性门失败；
- 旧 run 的 schema 1/2 提取回归保持通过，新写端只输出 schema 3。

### 8.4 性能和功能

- 本地无模型测试、静态检查和 `dtb.patch` apply-check 通过；
- 目标 NPU telemetry-off paired E2E、P50、P95、吞吐无可测退化；
- phase/stream 启用成本单列，不混入正式 DTB 性能排名；
- DTB 的质量、hard failure、取消、工具调用和 parser-facing 契约不退化。

## 9. 一致性校验

- **概念一致性：** DTB 是产品名；phase telemetry、stream audit、operational log、history store
  无同义异名。
- **状态一致性：** telemetry 是旁观者，不能返回下一状态或修改 prompt/token/output。
- **层次一致性：** vLLM 特有对象只进入 integration/runtime；planner 不 import vLLM；tests 不
  import 文档目录。
- **配置一致性：** YAML 是部署参数唯一来源；源码只保留真正不可调的不变量。
- **性能一致性：** 算法必需的计数与 elapsed 不伪装成 telemetry 开销；telemetry 专属工作在 off
  下为零。
- **数据一致性：** 原始实验正文不留在主分支；结构化 perf run 仍按 AGENTS.md 的证据规则处理。
- **升级一致性：** 内部 Python import 原子迁移；外部 HTTP 字段另行版本化，不叠加永久 alias。

## 10. 风险与回退

| 风险 | 控制 |
| --- | --- |
| 大规模 rename 掩盖行为 diff | P1 只做机械 move/import，使用 `--find-renames` 复核 |
| 删除 alias 破坏服务器 patch | 同提交更新并在固定 vLLM checkout 执行 apply/import smoke test |
| async telemetry 丢日志 | 有界队列、dropped counter、run 完整性门、显式 flush |
| 配置 fail-fast 降低“带错启动”的可用性 | 把配置校验放到部署预检；宁可拒绝启动，不静默换预算 |
| history 关闭后历史样本不再积累 | 这是开关语义修复；需要学习时必须显式启用并写入 manifest |
| 删除原始数据影响复盘 | 记录归档 commit/hash，保留最小 fixture 和聚合 RESULT |

任一阶段出现 parser-facing token、终止语义或质量变化时，回退该阶段提交；不能通过重新开启旧
兼容 alias 或吞掉 terminal 来“修复”。

## 11. 变更记录

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-08-20 | 基于 `bc1425` 完成当前实现、命名、telemetry 热路径、history、配置和实验资产检视，给出分阶段重构与验收计划 | 在保留工具流排障能力的同时，使 DTB 生产边界清晰、默认安静且可证明无额外观测工作 |
