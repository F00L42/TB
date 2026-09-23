# 最小服务器 replay

`cases.jsonl` 是本次构造的 7 条合成输入，约几 KiB，不依赖已删除的历史日志或任何评测平台。
模型收到的工具仅有无副作用的 schema；replay 不执行工具，tool 返回值由样本预置。

| case | 精确答案门 | 自动路由（Noemate 750 YAML） |
| --- | --- | --- |
| arithmetic | 整数 4014 | main / dtb |
| grid_min_cost | 10×10 右/下最短路径成本 JSON，278 | main / dtb |
| tool_call | lookup_alarm(resource=AMF01)，合法终态 | main / dtb |
| tool_result | 预置工具返回后输出 [A1,A2] JSON | main / dtb |
| noemate_followup | FOLLOWUP_OK | followup / native |
| noemate_compaction | COMPACTION_OK | compaction / native |
| unknown_identity | UNKNOWN_OK | general / native |

system 前缀与候选相同，后接合成 self-test 指令；这些输入只验证接口/分支与特定答案，不代表真实 Agent 质量。
网格成本由确定性动态规划计算，不调用模型作裁判。自然提前结束可能导致介入路径未覆盖，审计会明确返回 3。
本地 parser fixtures 保留真实历史流，但不作为服务器 replay 请求。

## 准备

从 TB 根目录运行，先按 [部署手册](../../docs/design/dtb_system_routing_deployment.md) 使用成套 controller/patch，
并显式选择 `budget_config_noemate_750.yaml` 作为规范配置。默认 legacy YAML 不满足本表的自动路由预期。
模型名填写实际 served-model-name，API key 从 `OPENAI_API_KEY` 读取，不写入 manifest。

```bash
python -m pip install -r requirements-dev.txt
mkdir -p artifacts
cp validation/replay/server-manifest.example.json artifacts/server-off.json
# 编辑 server-off.json 的全部 REPLACE 字段，记录实际运行服务的身份；observability 必须与服务相同。
git rev-parse HEAD
sha256sum dtb/config/budget_config.yaml patches/vllm/v0.23.0/*.patch
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL --dry-run
```

manifest 要填写实际 vLLM/Ascend commit、torch/torch-npu/CANN、模型修订、配置和各 patch SHA、prefix cache 与启动命令。
`patches_sha256` 可填按文件名排列的 `文件:SHA` 字符串；不能用本地只读 checkout 身份代替已安装服务器身份。
脚本校验完整性并记录为操作者声明，不声称从远端自动核验这些值。
输出目录必须不存在；失败后使用新目录重跑，保留失败证据。

## 功能矩阵（默认 off）

```bash
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL \
  --url http://127.0.0.1:9001/v1/chat/completions \
  --server-manifest artifacts/server-off.json --output artifacts/smoke-off
```

默认 7 样本 × 3 参数组 × 2 HTTP 模式 × Vanilla/DTB = 84 请求，各重复 1 次，串行。
每个参数组都配自己的 Vanilla；相邻重复轮交替先后次序，保持模型、seed、temperature=0、top_p=1、max_tokens=4096。

| profile | budget / check / stride / num / min / prob | 用途 |
| --- | --- | --- |
| auto | 完全删除请求的 fixed 字段 | 检查自动 scene/native 动作 |
| cap750 | 750 / 750 / 256 / 4 / 1 / .95 | 固定预算/direct-final 候选 |
| probe128 | 2048 / 128 / 256 / 4 / 1 / .95 | 低阈值 probe 诊断组合，非推荐生产参数 |

正的 fixed_budget_tokens 优先于 native scene，因此 cap750/probe128 下全部样本应进入 DTB。
JSON/SSE 均检查 HTTP、协议异常、精确正文/JSON/工具参数、唯一终态；`length`、错误答案或工具失败立即非零退出，
已完成记录保留。工具调用的预期 finish_reason 是 tool_calls，其他答案是 stop。
只执行部分组合可加 `--profiles cap750 --protocol stream`；本脚本不接受旧 replay CLI 的隐含 fixed 默认值。

## 分支/模型工作量（另开 phase run）

停服，备份规范 YAML，将 OBSERVABILITY.mode 改为 phase，重启。复制并更新 server manifest（含新 config SHA/mode）。
在记录下来的服务日志中等待 writer 完成写出，再审计；避免读取尚未完整复制的日志。

```bash
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL \
  --server-manifest artifacts/server-phase.json --output artifacts/smoke-phase
python tools/dtb/audit_release_phase.py artifacts/smoke-phase /path/to/phase-server.log --require-intervention
```

审计按 server_request_id 对账，要求 auto 对应本表、fixed=DTB、Vanilla 不带 DTB 事件、native 无 DTB 账本，
DTB 成功 summary 与 hidden probe token 相符；流式还验证 abort/drain 交接。
返回 1 表示错误/缺账本，3 表示 direct/probe 实际介入未全部覆盖。3 不能写成通过：增加真实长思考请求，
为新增数据提供可审计的 `answer_check` 与自动路由预期，重新运行。短样本自然结束是合法响应，却不证明 probe 可用。
phase JSONL 不是 CSV 提取器的输入；归属结果写入 phase_audit.json。

## 性能候选（切回 off 并重启）

```bash
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL \
  --server-manifest artifacts/server-off.json --profiles cap750 --protocol stream \
  --repetitions 10 --warmup 1 --concurrency 1 --require-gain --output artifacts/perf-c1
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL \
  --server-manifest artifacts/server-off.json --profiles cap750 --protocol stream \
  --repetitions 10 --warmup 1 --concurrency 4 --require-gain --output artifacts/perf-c4
```

并发是同 variant 闭环批次，Vanilla/DTB 不混在同批；两侧相同并发和样本，默认 keepalive 与 server cache 配置保持一致。
warmup 记录但不参与耗时统计，warmup 质量失败仍阻止通过。并发超过数据量不会凭空增加请求，真实负载需扩充数据。

退出码：0=功能通过（启用 require-gain 时还需至少一个选择的 cell 通过样本收益门）；1=功能/数据/身份错误；
3=未观察到所要求的收益。收益门要求至少 5 次重复、全部样本质量通过、配对收益中位数与总 E2E 收益均 ≥5%
（可用 minimum-gain 调整）。这是样本筛选门，不是统计显著性检验，不能保证生产优化。
报告始终将 production_performance_accepted 标为 false，质量更广泛的验收见[实验契约](../../docs/design/think_budget_generalization_plan.md)。

脚本输出 manifest、逐 cell 的 summary/chunks、validation.json 和 RESULT.md；响应 usage 不计 hidden probe。
DTB vs Vanilla 衡量预算策略效果。证明本次 Python 重构收益还需在相同参数下分别部署优化前/后代码重放，
并核对事件循环延迟、CPU、RSS、吞吐与 P95。两类结论不可互换。
