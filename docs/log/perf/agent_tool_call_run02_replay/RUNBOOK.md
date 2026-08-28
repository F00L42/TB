# run-02 tool-call 配对流式回放

## 目标

用四个 request-body 精确样本比较同一服务上的 Vanilla 与 run-02 固定参数 DTB：

1. 先裁决工具调用流式协议是否保持原生等价；
2. 只有全部配对请求协议通过后，才比较请求 E2E、首个可见输出时延和 token；
3. 使用 DTB stream audit 将客户端异常定位到 controller、child output 或下游 parser。

这四个原始故障轮次的 reasoning 只有 31、38、89、32 token，均早于 400-token 首次 confidence check。按当前源码语义，它们应走模型原生 `tool_call` boundary，不应添加 guided answer cue。因此该组首先验证短思考工具边界，而不是 confidence-accepted final 的 cue 成本。

## 1. 生成 stream-audit 白名单

固定本次 run ID；下面示例产生 40 个 DTB parent request ID：

```bash
python tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_tool_call_run02_replay/cases.jsonl \
  --mode both \
  --repetitions 10 \
  --request-id-run agent-run02-r01 \
  --list-dtb-request-ids
```

将输出的完整 `chatcmpl-dtbtool-...` ID 写入 `OBSERVABILITY.stream_request_ids`，以 `stream_audit` 模式启动服务。记录 DTB、vLLM、vLLM-Ascend commit、两个 patch hash、YAML hash、模型 revision、启动参数和 server log 路径。

## 2. 执行配对回放

```bash
python tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_tool_call_run02_replay/cases.jsonl \
  --mode both \
  --repetitions 10 \
  --request-id-run agent-run02-r01 \
  --url http://127.0.0.1:9000/v1/chat/completions \
  --output-dir docs/log/perf/agent_tool_call_run02_replay/run_server_01 \
  --fixed-budget-tokens 20000 \
  --fixed-check-length 400 \
  --fixed-think-stride 500 \
  --fixed-num-thinking 4 \
  --fixed-min-think-tokens 1 \
  --fixed-prob-th 0.93
```

未显式传入 `max_tokens`、`temperature`、`top_p` 时，脚本保留每个归档请求的原始值。Vanilla/DTB 的先后顺序按 repetition 交替，以减小顺序和 cache 热度偏差。每个请求同时在 body 中记录 request ID，并通过 `X-Request-Id` header 透传。

脚本检测到任何协议异常时返回非零退出码，但仍完整写出结果。这是测试失败信号，不代表结果丢失。

## 3. 关联服务器审计

```bash
python tools/dtb/analyze_tool_call_run.py \
  docs/log/perf/agent_tool_call_run02_replay/run_server_01 \
  /path/to/server.log
```

主要产物：

- `chunks.jsonl`：逐 SSE 行及解析结果；
- `summary.jsonl`：逐请求协议、时延、首输出时间和 usage；
- `paired_summary.json`：全部配对与 protocol-clean 配对统计；
- `RESULT.md`：协议通过率和仅基于 clean pair 的时延摘要；
- `analysis.json`：DTB stream-audit 生命周期和 parser-facing 诊断。

## 4. 验收门禁

四个样本、十次重复应得到 40 个 Vanilla 和 40 个 DTB 请求：

- `tool_xml_leaked_to_content = 0`；
- `expected_tool_call_missing = 0`；
- 工具参数 JSON 错误、缺失名称、非连续 index、tool 后 content、重复/缺失 terminal 均为 0；
- DTB `analysis.json` 中 40/40 为 `pass`，每个请求只有一个 completion epoch 和一个原生 terminal；
- 所有工具边界都保持 `text/token_ids` 对齐，不得在 boundary 后进入 probe 或新的 thinking；
- `paired_summary.json.latency_ranking_valid = true` 后，才解释 paired E2E 和 speedup。

这组回放是隔离协议 canary，不能替代九任务 LightCode E2E。协议通过后再运行单独的 phase 样本，核对短链 `confidence_accepted` 的 answer cue、额外 prefill 和 hidden probe 成本。
