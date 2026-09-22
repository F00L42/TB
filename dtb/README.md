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

默认部署入口固定为 `config/budget_config.yaml`。`config/budget_config_quant.yaml` 是量化增训
权重的 13 场景 **TP4 待验收候选**，不是运行时自动选择的第二套策略。服务器应在停止服务后
备份规范文件，把候选复制为 `budget_config.yaml`，记录候选文件名与 SHA，再重启服务；不要
重命名或移走仓库中的候选文件，也不要在运行中覆盖。正式 E2E 使用 `OBSERVABILITY.mode=off`。
这样部署选择保持显式、启动时生效且可由 manifest 审计，无需增加环境变量或权重专属算法分支。

```bash
cp dtb/config/budget_config_quant.yaml dtb/config/budget_config.yaml
sha256sum dtb/config/budget_config_quant.yaml dtb/config/budget_config.yaml
```

两行应在服务停止后执行；随后重启，并把相同 SHA 写入本轮 `manifest.json`。

量化候选包含 5 个 fixed_v5 和 8 个 fixed_v4 复验点。现在支持 scene `action: native` 和
`ROUTING.on_unmatched: native`，在 serving 中直接回原生生成；原有配置省略时保持旧行为。
更换场景动作仍需同身份协议/质量/性能验收，现有量化候选没有自动改为 native。

Agent 可选择 `ROUTING.mode: system_prefix`，启动编译有界 system/首 user 前缀规则；只看
原始消息身份，不扫描历史正文。新增 Noemate `budget_config_noemate_750.yaml`（已测参数的
自动路由候选）与 `budget_config_noemate_native.yaml`（保守全原生），均不替换默认文件。
有效 fixed_budget_tokens 仍优先，单字段 fixed 覆盖不改变 native 动作。
接口、离线覆盖率、部署升级/回滚和 pending_remote 验收见
[Session A 部署手册](../docs/design/dtb_system_routing_deployment.md)。

请求仍可使用既有 fixed override 字段。公网开关 `enable_think_budget` 为外部协议兼容字段，
内部 Python 包和事件统一使用 DTB 命名。

## Telemetry

`OBSERVABILITY.mode` 有三种服务级模式，请求不能覆盖：

- `off`（默认）：不创建 event writer、phase recorder 或 stream audit。phase/delta 边界只检查
  可选引用，不读取 telemetry 时钟/metrics，不构造事件，不序列化、hash、复制 token 或写队列。
- `phase`：异步写出一次 `DTB_ROUTE` schema 1，以及 DTB 路径的 `DTB_PHASE` schema 3 和
  `DTB_REQUEST_SUMMARY`；native route 不构造 phase/summary。该模式会在请求协程中
  计时、读取 metrics 并组装低基数 record；写出只做有界队列 `put_nowait`，后台线程负责 JSON
  和 stdout。队列满时丢事件并由后台标准 logger 限频告警，绝不阻塞或修改模型输出。
- `stream_audit`：不创建 phase recorder，只对 `stream_request_ids` 白名单请求写
  `DTB_STREAM_AUDIT` schema 1。事件可能含模型正文、工具参数和 token ID，必须限定排障窗口。

请求级账本只由 `DtbRequestState` 维护：模型工作量使用 `thinking_tokens`、
`hidden_probe_tokens`、`final_answer_tokens`、`child_generate_calls`；原生 completion 契约使用
`completion_token_limit`、`emitted_completion_tokens`。后者只累计送入 parser 的可见 token，
不包含 hidden probe、answer cue prompt 或 stop 后丢弃的 token ID。history 和 request summary
都读取该状态；telemetry 不再维护第二本 ledger。

提取共享日志：

```bash
python tools/extract_dtb_phase_metrics.py docs/log/perf/<run_id>
```

提取器读取历史 `TB_PHASE` schema 1/2 与新 `DTB_PHASE` schema 3；生产写端不双写。
`routes.jsonl` 与 request_index 的 route_* 列区分 native 合法无 phase、DTB 缺账本、重复路由及
未知缺失。路由事件只表示意图，不能替代客户端完成/质量证据。

## vLLM patch

唯一部署制品位于 `patches/vllm/v0.23.0/`。应用顺序和 `git apply --check` 命令见该目录
`README.md`。Serving patch 复用 vLLM 原生 `get_max_tokens()` 解析一次整个 assistant completion
的有效额度，并通过私有 route identity 交给 DTB；包内不保存 serving/protocol patch 的 Python
副本。
