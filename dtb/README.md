# DTB production package

DTB（Dynamic Think-Budget Control）在 vLLM Chat Completion serving 内控制可见思考长度，
同时保持原生 reasoning/tool parser 面向的文本、token ID、结构边界、terminal 和多轮语义。

## 结构

- `request_controller.py`：外部请求生命周期门面；不包含预算算法或 parser 实现。
- `config/`：从 `budget_config.yaml` 启动时加载一次并 fail-fast 校验；YAML 是部署参数唯一来源。
- `budgeting/`：scene 路由与 fixed/scene/difficulty/history 预算解析，输出 `DtbBudgetDecision`。
- `reasoning/`：`DtbRequestState`、三状态机、child generate 行为和 parser boundary 契约。
- `history/`：默认关闭的在线历史策略与持久化。
- `observability/`：默认关闭的 phase telemetry 和定向 stream audit。
- `integration/vllm/`：原生请求输入与 stream/non-stream response parser 适配。

难度估计是原始设计的一部分，完整保存在 `budgeting/difficulty/`，生产默认关闭。本轮结构重构
没有删除或改写其裁决语义；后续单独做集成一致性检视。

## 配置生命周期

`config/loader.py` 只在 controller 构造时加载并校验 YAML，进程运行期间不检查 mtime、不做
隐式 reload，也没有内嵌 fallback 表。配置错误会阻止服务启动；修改配置后必须重启。
`HISTORY_STORE` 保存可选 history 的路径、保留上限和异步队列容量；history 关闭时不会配置、
构造或读取 store。

请求仍可使用既有 fixed override 字段。公网开关 `enable_think_budget` 为外部协议兼容字段，
内部 Python 包和事件统一使用 DTB 命名。

## Telemetry

`OBSERVABILITY.mode` 有三种服务级模式，请求不能覆盖：

- `off`（默认）：不创建 event writer、phase recorder 或 stream audit。phase/delta 边界只检查
  可选引用，不读取 telemetry 时钟/metrics，不构造事件，不序列化、hash、复制 token 或写队列。
- `phase`：异步写出 `DTB_PHASE` schema 3 和 `DTB_REQUEST_SUMMARY`。该模式会在请求协程中
  计时、读取 metrics 并组装低基数 record；写出只做有界队列 `put_nowait`，后台线程负责 JSON
  和 stdout。队列满时丢事件并由后台标准 logger 限频告警，绝不阻塞或修改模型输出。
- `stream_audit`：不创建 phase recorder，只对 `stream_request_ids` 白名单请求写
  `DTB_STREAM_AUDIT` schema 1。事件可能含模型正文、工具参数和 token ID，必须限定排障窗口。

模型工作量只由 `DtbRequestState` 维护：`thinking_tokens`、`hidden_probe_tokens`、
`final_answer_tokens`、`child_generate_calls`、`completion_status`。history 和 request summary 都读取
该状态；telemetry 不再维护第二本 ledger。

提取共享日志：

```bash
python tools/extract_dtb_phase_metrics.py docs/log/perf/<run_id>
```

提取器读取历史 `TB_PHASE` schema 1/2 与新 `DTB_PHASE` schema 3；生产写端不双写。

## vLLM patch

唯一部署制品位于 `patches/vllm/v0.23.0/`。应用顺序和 `git apply --check` 命令见该目录
`README.md`。包内不保存 serving/protocol patch 的 Python 副本。
