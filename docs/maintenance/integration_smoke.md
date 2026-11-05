# 集成或升级后的 DTB 功能冒烟

本手册用 **8-token 思考预算 + 原生对照 + phase 账本**，直观确认当前服务确实执行了 DTB
干预并收束思考。8 是诊断参数，不是生产候选，也不保证答案正确；这里的“截断”指停止继续
思考并转入 final，不是截断 HTTP 响应或把最终答案限制为 8 token。

先完成[成套部署](../design/dtb_system_routing_deployment.md)；迁移接口时先读
[vLLM 升级手册](vllm_upgrade.md)。以下命令在服务器的 TB 根目录用 **Bash** 执行，
填写实际模型名、URL 和本轮新目录。本文是可执行流程，尚未代表该服务器已通过验证；
未实际运行的模型、版本、设备结果保持 `pending_remote`。

## 1. 打开已有的诊断记录

在服务**实际加载**的 `dtb/config/budget_config.yaml` 中合并以下字段，保留其余配置：

```yaml
RUNTIME:
  BUDGET_ENABLED: true
OBSERVABILITY:
  mode: phase
```

停服、备份规范 YAML、修改、记录 SHA，再完整重启相关进程。确认运行环境中的 DTB 包、
已安装 vLLM patch、模型/tokenizer 和 reasoning/tool parser 均与本轮部署一致；只修改仓库
文件不会更新正在运行的服务。配置生命周期和字段说明见[配置参考](../design/dtb_configuration_reference.md)。

`phase` 会向 **stdout** 写 `DTB_ROUTE`、`DTB_PHASE`、`DTB_REQUEST_SUMMARY`。
现有配置没有 `log_level` 字段，这些结构化事件也不由普通 logger 的 INFO/DEBUG 等级控制。
保存包含 stdout 的服务日志，等异步 writer 写完再检查；缺事件或队列丢事件不能判为通过。
`off` 默认没有成功事件；`stream_audit` 是另一种模式，仅用于白名单正文/token 排障，本轮不需要。

## 2. 发出一个原生请求和一个 8-token 请求

复用[细粒度 replay](../../tools/dtb/replay_tool_calls.py)，选择现有数据第 2 行
`grid_min_cost`。这是有已知答案的网格问题，适合作为起点，但不保证所有模型都长思考。
工具串行发送相同输入的 Vanilla/DTB 各一次；原生侧关闭 DTB 并删除 fixed 字段。

```bash
python tools/dtb/replay_tool_calls.py \
  --file validation/replay/cases.jsonl --lines 2 \
  --mode both --repetitions 1 --stream \
  --url http://127.0.0.1:9001/v1/chat/completions --model YOUR_SERVED_MODEL \
  --max-tokens 4096 --temperature 0 --top-p 1 --seed 20260923 \
  --fixed-budget-tokens 8 --fixed-check-length 8 \
  --fixed-think-stride 8 --fixed-num-thinking 1 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.95 \
  --output-dir artifacts/replay/integration-cap8-001
```

输出目录必须不存在，重跑换新目录。API key 从 `OPENAI_API_KEY` 读取。
显式给出全部 fixed 参数，避免沿用工具原有的 20000/850 等诊断默认值。
两侧保持模型、输入、采样和 completion 额度一致；**不要把 `max_tokens` 改为 8**。
同时确认模型上下文和服务端上限足够完成请求；最终额度还受服务端配置约束。

若直接使用 HTTP 客户端，保留原请求的 model/messages/采样字段，DTB 侧添加：

```json
{
  "enable_think_budget": true,
  "fixed_budget_tokens": 8,
  "fixed_check_length": 8,
  "fixed_think_stride": 8,
  "fixed_num_thinking": 1,
  "fixed_min_think_tokens": 1,
  "fixed_prob_th": 0.95
}
```

原生侧使用 `enable_think_budget: false` 并移除全部 fixed 字段。使用 OpenAI Python SDK 时，
这些扩展字段放在 `extra_body`；不得以一个很大的阈值代替原生对照。

请求级正值 `fixed_budget_tokens` 在服务和请求开关开启时强制选择 DTB，适合隔离路由问题。
仅改 `general` 场景未必影响实际请求：可能命中其他场景或 native 动作，部分未匹配预算分支
使用 `RUNTIME.default_budget`。本轮保持业务 YAML 中的预算和路由不变。

## 3. 看输出，也按同一个请求 ID 看账本

客户端生成 `manifest.json`、`summary.jsonl`、`chunks.jsonl`、`paired_summary.json`、`RESULT.md`。
summary 中的 `server_request_id` 用于关联服务端 `parent_request_id`；不要混入其他请求或旧日志。
下面只展示结果，不自动判定通过：

```bash
python - <<'PY'
import json
from pathlib import Path
run = Path("artifacts/replay/integration-cap8-001")
for line in (run / "summary.jsonl").read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    print(json.dumps({
        "variant": row["variant"], "server_request_id": row["server_request_id"],
        "reasoning_chars": len(row["reasoning"]), "reasoning": row["reasoning"],
        "content": row["content"], "usage": row["usage"],
        "finish_reasons": row["finish_reasons"], "terminal_count": row["terminal_count"],
        "anomaly_reasons": row["anomaly_reasons"], "error": row["error"],
    }, ensure_ascii=False, indent=2))
PY
# 将下面的占位值替换成 summary 中 DTB 行的完整 server_request_id。
rg -F '实际_server_request_id' /path/to/phase-server.log
```

按以下项目分别记录结果，不用一个 HTTP 200 或脚本 PASS 代替整体验证：

| 项目 | 本轮应看到的证据 |
| --- | --- |
| 接入 DTB | 对应 ID 恰有一条 `DTB_ROUTE`，`action=dtb`；summary 的 `intervention.minimum_intervention_tokens=8`，`threshold_source` 指向 fixed 覆盖 |
| 预算干预成功 | `intervention_status=triggered`、`post_drain_branch=budget_final`；`abort_count=1`、`abort_terminal_seen=true`、`abort_lost_to_native_terminal=false`（均在 `intervention` 内） |
| 干预后完整返回 | summary 的 `completion_status=success`、`error` 为空、`final_terminal_count=1`；各有一个 `native_passthrough` 和 `final_answer` phase，`hidden_probe_tokens=0` |
| 客户端协议正常 | HTTP 200、无 error/协议异常、`terminal_count=1`；这个普通问答用例的 `finish_reasons` 为 `["stop"]`，不能是 `length`，不能出现 XML 泄漏、role 尾流或重复终态 |
| 可见思考缩短 | DTB 的 reasoning 比同输入原生侧明显更短；记录双方文本及 `reasoning_chars`，没有缩短就不能声称本对样本展示了压缩 |
| 样本答案 | 网格题应为 `{"cost":278}`，单独记录正确/错误；8-token 强干预可能答错，功能介入证据仍可保留，但不能写成质量通过 |

8 是触发/预算目标，不承诺客户端只收到恰好 8 个思考 token。引擎增量批次及 abort/drain
可能带来尾部输出；核对 `trigger_prefix_tokens`、`threshold_overshoot_tokens`、
`drained_tail_tokens`、`drain_overshoot_tokens`，不要为凑 8 而删除已生成的文本或 token。

`reasoning_chars` 是字符数。`usage.completion_tokens` 包含可见思考、答案和相关结构 token，
不包含隐藏 probe，不能当作思考 token 数。DTB 的 `thinking_tokens` 是自身账本，原生侧没有
同类 DTB 账本。若需要精确比较思考 token，另外采集实际返回的 token IDs 并按已验证的
reasoning 边界划分；重新 tokenize 文本不能冒充原生成 token。单次耗时及本工具自动生成的
耗时摘要不用于性能结论。

以下情况写“未覆盖”，保留原因后换长思考样本或修正集成再跑：模型自然提前结束；
`native_complete`；`disarmed_boundary`；`disarmed_no_capacity`；abort 输给自然终态；
或未出现 `budget_final`。其中 `model_boundary_final` 可以证明发生过交接，但不是预算强制
收束的目标分支。协议/请求错误、重复或缺少账本则是失败/证据不完整，不能写成通过。

这个 replay 工具主要检查协议和工具形状，**不执行数据中的 `answer_check` 精确答案门**，
也不自动确认干预或文本缩短；普通回答的 `finish_reason=length` 不一定触发工具失败，
退出码 0 仍需按上表人工确认 `["stop"]`。其平铺目录不能直接交给 `audit_release_phase.py`；后者读取
`validate_release.py` 的矩阵产物。当前正式矩阵只有 `auto/cap750/probe128`，没有 `cap8`
profile；8-token 演示使用上面的 fixed CLI 与人工账本核对即可。

## 4. 单独确认置信度链路

8-token 预算与首次检查长度相等，预算耗尽后直接 final，正常情况下不执行 probe，不能用它
证明置信度算法正常。复用同一工具，以现有 `probe128` 的参数组合另跑一对请求：

```bash
python tools/dtb/replay_tool_calls.py \
  --file validation/replay/cases.jsonl --lines 2 \
  --mode both --repetitions 1 --stream \
  --url http://127.0.0.1:9001/v1/chat/completions --model YOUR_SERVED_MODEL \
  --max-tokens 4096 --temperature 0 --top-p 1 --seed 20260923 \
  --fixed-budget-tokens 2048 --fixed-check-length 128 \
  --fixed-think-stride 256 --fixed-num-thinking 4 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.95 \
  --output-dir artifacts/replay/integration-probe128-001
python tools/dtb/check_probe.py artifacts/replay/integration-probe128-001 /path/to/phase-server.log
```

`check_probe.py` 返回 0/3/1 分别表示 probe 交接链覆盖/未覆盖/协议或账本阻塞；
**0 不保证置信值可用或因置信达标而早退**。继续按同一请求 ID 检查 `confidence_probe`：

- `generated_tokens > 0`，probe token 合计与 summary 的 `hidden_probe_tokens` 一致。
- `pair_count > 0`，`confidence` 是有限的 0～1 数值；`confidence_method` 和 `accept_reason`
  不能是 `confidence_unavailable_fail_open`。只有 fail-open 时，生成链可通，但评分链尚未验证。
- 如只验证计算链，`accepted=false` / `below_threshold` 可以正常成立；如声称“置信接受早退”，
  还需有效评分的 `accepted=true` 和随后 final 的 `final_answer_reason=confidence_accepted`。
  不能只看 accepted 或 final reason，因为缺失概率的 fail-open 也会转 final。

默认算法是隐藏试答中实际采样 token 概率的算术平均；Value-Min 为可选配置。
公式、有效配对、缺失值回退、probe 采样和上游 logprobs 语义见
[配置与置信度参考](../design/dtb_configuration_reference.md)。集成升级需同时核对实际 sampler，
日志中“有一个数字”不能代替归一化概率语义验证。

## 5. 恢复实际配置，再做升级验收

移除请求的六个 fixed 覆盖字段，确认服务 YAML 没有遗留诊断 fixed 覆盖，以真实业务提示
复验自动路由。预期 native 的请求应原生完成；预期 DTB 的请求再核对实际阈值和介入。
固定预算成功只说明强制路径可用，不证明自动路由正确。

随后按[正式 replay 手册](../../validation/replay/README.md)运行流式、非流式、工具调用及
多轮验收，启用精确答案门。默认 `auto` 的预期绑定 Noemate 750 YAML，不能直接用来裁决
其他部署策略；使用实际策略对应的路由预期。正式 phase 审计的 `--require-intervention`
要求同一 run 同时覆盖 direct 和 probe，只跑 direct 不满足该门。

本轮至少保留客户端目录、所用请求数据及 hash、包含 stdout 的 `server.log`，并另存填写完整
的[服务器身份清单](../../validation/replay/server-manifest.example.json)；基础 replay 的 manifest
仅记录客户端输入和参数，不自动核验服务环境。记录实际 DTB/vLLM/Ascend commit、模型修订、
torch/torch-npu/CANN、YAML/patch hash、启动命令和重启时间。
在生成的 RESULT 后追加人工结论，分别写“路径接入、预算干预、可见思考缩短、置信度链路、
样本答案、协议”的通过/失败/未覆盖及对应 request ID，保留错误结果。

诊断结束后恢复诊断前服务配置，正式质量/性能运行改为 `OBSERVABILITY.mode: off` 并重启，
以实际预算重新做配对验收。不得把 8-token 演示、phase 耗时或 completion token 减少
当作质量不退化或净 E2E 提升；服务器验收范围见[runbook](../debug/dtb_performance_analysis.md)。

## 源码定位

- [配置加载](../../dtb/config/loader.py)、[事件写出](../../dtb/observability/event_writer.py)：配置键、启动加载、stdout 与异步队列。
- [请求控制](../../dtb/request_controller.py)、[预算解析](../../dtb/budgeting/budget_resolver.py)：开关、fixed 优先级、最终预算来源。
- [流式观察与交接](../../dtb/reasoning/deferred_observation.py)：阈值、abort/drain、分支与 overshoot 字段。
- [生成状态](../../dtb/reasoning/generation_states.py)、[置信裁决](../../dtb/reasoning/confidence_gates.py)：probe、可用性、接受与 final。

变更记录：2026-11-05，补充基于现有接口的 8-token 直观验证与独立置信度检查；不改变算法、
生产默认参数、日志接口或验收工具，服务器效果仍需实测。
