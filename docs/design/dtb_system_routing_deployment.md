# Session A：system 场景路由交付与部署

2026-09-22。代码基线 `f342e127`；本次实现、无模型测试和 Noemate 捕获离线审计已完成。
目标 vLLM `v0.23.0` / vLLM-Ascend `v0.23.0rc1` 的自动模式 HTTP、NPU 与任务 E2E **pending_remote**。
本次不改早退算法、cue、parser patch 或默认 `budget_config.yaml`。

## 1. 接口与优先级

服务启动时加载 `ROUTING.mode`：省略为 `legacy_text`，保留最新 user / serving prompt 的原评分规则；
`system_prefix` 使用原始 `request.messages`，不使用渲染或反解后的 chat template。

```yaml
ROUTING:
  mode: system_prefix
  on_unmatched: native  # budget 表示回退 RUNTIME/history，执行动作仍为 dtb
SCENES:
  example:
    action: dtb
    match:
      system_prefix: "You are a title generator."
      first_user_prefix: "Generate a title:"  # 可选，首条 user，不是最新 user
      tools_empty: true                     # 可选
    check_length: 750
    think_stride: 256
    num_thinking: 4
    min_think_tokens: 1
    prob_th: 0.95
    max_think_tokens: 750
  general:
    description: "拒识标签，动作由 on_unmatched 决定"
```

上例仅说明 schema，预算不是标题场景推荐值；部署需完整服务配置。`action: native` 节点只保留
`match` 和描述，不能同时填写预算。旧 `legacy_text` 节点也可声明 `action: native`。

身份读取上限：4096 条消息的角色元数据、首条 system 的 512 字符、需要 guard 时首条 user 的
256 字符。只允许唯一 system 位于首条，额外 system/developer、缺少 system、非字符串 system、
超限或 guard 失败均拒识；首 user 的非字符串内容只在使用该 guard 时拒识。tools guard 只检查
工具声明是否为空，不扫描 schema。大小写、空白、BOM 均精确匹配，不 strip 或模糊匹配。
assistant/tool 正文不读取；每请求一次，不每 chunk 分类，不 tokenize、不调用模型。
身份只用于选择性能策略，不授予工具权限；前缀后是否出现语义矛盾不由此规则判断。

相同或互为前缀的规则在启动时拒绝，包括试图靠 user guard 区分的重叠规则。
`SCENE_PRIORITY` 在 system 模式仅作展示顺序；没有运行时抢占或得分排序。
system 模式要求 scene budget 开启、history/difficulty 关闭、scene_override 为空；DTB 场景显式
提供六个预算字段且 `check_length <= max_think_tokens`。未知配置字段启动报错。

执行优先级：

1. 请求 `enable_think_budget=false` 或服务 `BUDGET_ENABLED=false`：跳过路由，沿既有 serving 开关行为。
2. 开启时先校验 fixed 参数；非法参数返回错误，不能被 native 掩盖。
3. 有效 `fixed_budget_tokens>0`：显式 fixed 实验优先；system 模式不读取身份，强制 DTB。
4. 仅 `fixed_check_length` 等部分覆盖：先选动作；native 不被单字段覆盖改成 DTB。
5. 命中读取 scene.action，缺省 dtb；拒识读取 on_unmatched，旧配置缺省 budget。

`prepare_request_route()` 返回内部 `PreparedDtbRequest(config, route, threshold, text_views)`。
serving 在构造 DTB 专属额度/parser/context 前调用它，native 直接落回已有 vLLM 原生代码，
不调用 `handle_request`、预算 resolver、状态机、队列、观察器或请求级 recorder。
两个 enable 开关都为 true 时，native 不会误转 DEER。
DTB 的 scene 快照传给阈值和完整预算；流式短请求依然不执行完整 resolve。
直接调用 handle 的旧入口会内部 prepare 一次；native 必须由更新后的 serving patch 分流。

## 2. Noemate 候选与证据边界

| 文件 | 主 Agent | 推荐问题、对话压缩、未知模板 |
| --- | --- | --- |
| `dtb/config/budget_config_noemate_750.yaml` | T=750，硬思考上限=750，stride=256，num=4，min=1，prob=.95 | 原生 |
| `dtb/config/budget_config_noemate_native.yaml` | 原生，保留身份审计 | 原生 |

750 参数对齐 [884a00 冻结小集 fixed 实验](../log/perf/noemate_guardrail_main_20260922T070000Z/RESULT.md)，
只是该 Noemate/Qwen3.6-27B 场景的已测候选。系统身份不能判断同一模板下“此题 750 是否足够”。
它不覆盖未知数据集，不证明总体质量非劣。保守文件全部原生，供先验收路由或暂停压缩使用。
两文件所有服务 fixed 字段为 0，辅助任务不施加 DTB 额度。

[离线审计](../log/perf/noemate_system_routing_offline/RESULT.md) 对 12,214 份捕获 detail 逐条执行
生产 prepare：主请求 8,306 / 推荐 3,897 / 压缩 11，全部命中；最大 393 条消息。
这批数据参与模板设计，不能称作 holdout 泛化测试。原始 archive 未在本机重新解析；审计基于
detail 中保存的请求正文，source hash 和代表 ID 见 audit.json。已有非流式响应解析缺陷不影响
此次读取输入身份，也不能据此把这些响应判为成功。

## 3. 日志与归属

`phase` 每个开启路由的请求输出一次 `DTB_ROUTE` schema 1：parent ID、mode、scene、matched、
action、reason、wall_ms、threshold、threshold_source；无 system 正文或 token IDs。
scene 本身是稳定 rule ID。事件为路由意图，生成成功仍由原生输出/客户端裁决。
native 不伪造 `DTB_PHASE` 或 `DTB_REQUEST_SUMMARY`。off 不构造路由事件、不读遥测时钟；
stream_audit 不输出路由事件，也不为 native 添加逐 chunk 观察。

文本选择/初始匹配移到 route.wall_ms；不再记旧 `decision.select_text`，也不叠加到延迟
`decision.total`。legacy fixed 快路径可能标 `fixed_budget` 或 `legacy_deferred`，完整预算阶段
仍沿旧语义确定 scene；system 自动路由始终有明确 scene/reject 快照。

CSV 型 run 继续用 `python tools/extract_dtb_phase_metrics.py <run_dir>`。新增每结果的
`routes.jsonl`、request_index 的 route_* 列及 manifest 中 route 覆盖统计：

- `native_no_phase_expected`：有 native route，没有 phase，合法。
- `dtb_phase_missing`：有 dtb route 却没账本，需核验失败、能力回退或日志丢失，不能当成功。
- `route_missing`：未知，可能旧版本/off/开关关闭/日志缺失，不得推断 native。
- `duplicate_route` / `native_with_unexpected_phase`：归属异常，停止自动验收。

writer 丢事件/写失败时保留缺失状态；排查日志警告，不能填充假 route。
replay JSON 型结果按保存的 request_id 和 server parent 前缀关联，不为运行提取器伪造 CSV。

## 4. 部署和回滚（服务器执行，pending_remote）

先记录实际部署 TB、vLLM、vLLM-Ascend commit/dirty、Python 包版本、PyTorch/torch-npu/CANN、
模型权重、启动命令、APC、采样/seed、并发/到达节奏。保留同身份 Vanilla。
使用部署方现有停服/启动方式；本仓库没有可通用于所有服务器的服务名，不猜 systemd unit。
下列操作均在服务停止后执行；用实际路径设置变量，保存原启动命令供原样重启。

```bash
set -euo pipefail
export TB_ROOT=/path/to/TB
export VLLM_ROOT=/path/to/deployed/vllm
export BACKUP=/path/to/dtb-backup-session-a
mkdir -p "$BACKUP"
cp "$TB_ROOT/dtb/config/budget_config.yaml" "$BACKUP/budget_config.old.yaml"
git -C "$TB_ROOT" rev-parse HEAD > "$BACKUP/tb.commit"
git -C "$VLLM_ROOT" rev-parse HEAD > "$BACKUP/vllm.commit"
git -C "$VLLM_ROOT" diff > "$BACKUP/vllm.diff"
```

全新固定上游 checkout：按 [patch 手册](../../patches/vllm/v0.23.0/README.md) 检查并依序应用三份 patch。
已有 DTB 部署只替换 serving patch，保留两个 parser patch；`OLD_TB_COMMIT` 必须来自实际部署
manifest，不根据版本号猜测，也不使用 git reset 清理其他改动。

```bash
: "${OLD_TB_COMMIT:?Set the actual deployed TB commit}"
git -C "$TB_ROOT" show "$OLD_TB_COMMIT:patches/vllm/v0.23.0/dtb_serving.patch" > "$BACKUP/dtb_serving.old.patch"
git -C "$VLLM_ROOT" apply --reverse --check "$BACKUP/dtb_serving.old.patch"
git -C "$VLLM_ROOT" apply --reverse "$BACKUP/dtb_serving.old.patch"
git -C "$VLLM_ROOT" apply --check "$TB_ROOT/patches/vllm/v0.23.0/dtb_serving.patch"
git -C "$VLLM_ROOT" apply "$TB_ROOT/patches/vllm/v0.23.0/dtb_serving.patch"
cp "$TB_ROOT/dtb/config/budget_config_noemate_750.yaml" "$TB_ROOT/dtb/config/budget_config.yaml"
cd "$TB_ROOT"
python -c "from dtb.config.loader import load_dtb_service_config; c=load_dtb_service_config(); print(c.routing, c.scene_rules)"
sha256sum dtb/config/budget_config.yaml patches/vllm/v0.23.0/*.patch
```

每条命令检查退出码，失败即停；patch 部分替换时先恢复旧 serving 再启动。
YAML 不是热加载；完成下节 phase 配置后才按保存命令启动。记录实际安装的包路径以避免 Python
仍导入另一份 dtb/vllm。回滚配置：停服、复制 `budget_config.old.yaml`、重启。
只暂停压缩可改 `BUDGET_ENABLED=false` 后重启；保守候选仍需保证没有有效 fixed 注入。
回滚代码：停服，反向检查/移除新 serving patch，检查/应用保存的旧 patch，恢复旧 TB 部署
版本与备份配置，再按原命令重启。新旧 controller 与 serving patch 必须成对使用。

## 5. 自动模式最小验收（服务器执行，pending_remote）

1. 停服后将规范 YAML 的 OBSERVABILITY.mode 改为 phase，所有 fixed_* 保持 0，保存 SHA、重启。
   检查代理 append_body 和客户端真实发送字段，不能保留 fixed=750 再称自动配置。
2. 从当前离线审计生成 5 个 smoke 请求：真实主请求、推荐、压缩、长例 319、合成未知身份。
   这只是路由/协议测试，不是最终 E2E 数据集。命令在 TB 根目录执行：

```bash
python -m tools.dtb.prepare_system_route_smoke \
  --audit docs/log/perf/noemate_system_routing_offline/audit.json \
  --pool docs/log/perf/noemate_analysis_60f2d7/replay_dataset/pool.jsonl \
  --output /path/to/run-session-a-input/smoke.jsonl

python tools/dtb/replay_tool_calls.py \
  --file /path/to/run-session-a-input/smoke.jsonl --repetitions 1 \
  --mode both --order alternating --request-id-run session-a-phase-stream \
  --url http://127.0.0.1:9000/v1/chat/completions \
  --fixed-budget-tokens 0 --fixed-check-length 0 --fixed-think-stride 0 \
  --fixed-num-thinking 0 --fixed-min-think-tokens 0 --fixed-prob-th 0 \
  --output-dir docs/log/perf/session_a_phase_stream
```

3. 用相同六个零参数再运行 `--no-stream`，换 request-id-run/output-dir，保存原生完整 JSON；
   不复用流式路径的成功结论。主请求 DTB、两类辅助和未知 native 应与输入 route_expectation
   一致。native 无 DTB phase/context；长例如达到安全介入点，再核对 scene 来源、T=750、
   硬额度=750、实际 budget_final/隐藏 probe。短请求自然结束不构造完整预算是合法状态。
4. 检查工具 JSON/XML、finish_reason、非空 final/截断、外部 completion 额度、priority/LoRA/trace/DP；
   加短思考+长工具正文、并发取消和错误样例。身份匹配不代表压缩已介入，HTTP 200 不代表任务成功。
   replay 的工具期待和历史响应只作协议诊断，不能替代人工语义判断。
5. 路由/协议通过后停服切回 off、记录新 YAML SHA、重启。用 Session C 的实际任务数据集运行
   同身份 Vanilla/自动 DTB，保存完整任务结果；已有 fixed run 和本次 phase/off 分开归档。
   不强制新增超参扫描，不把旧 fixed 成功直接当作新自动模式的实测验收。

## 6. 本地可复算入口

```bash
python -m pytest tests -q -p no:cacheprovider --basetemp /path/to/new-unique-test-temp
python -m tools.dtb.audit_system_routes \
  --config dtb/config/budget_config_noemate_750.yaml \
  --details proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3/details \
  --output docs/log/perf/noemate_system_routing_offline/audit.json
python -m tools.dtb.benchmark_system_routes \
  --config dtb/config/budget_config_noemate_750.yaml \
  --output docs/log/perf/noemate_system_routing_offline/local_benchmark.json
```

新测试包含真实 patch 在临时上游副本的应用/AST 编译、原生生成与关闭态的上下文/异常/取消对照，
以及 DTB 流式短请求不 resolve、非流式使用同一 scene 快照。现有 parser、生命周期、额度、
历史和 633 产品场景回归一并运行。本地纯 CPU 微基准不替代目标服务 CPU 或 NPU E2E。
