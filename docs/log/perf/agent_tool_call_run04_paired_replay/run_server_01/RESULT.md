# run04 tool-call paired replay 裁决

## 结论

本轮 600 个响应（300 个 DTB、300 个 Vanilla）中有 196 个响应至少命中一个异常标签。它复现了
端到端 case36 的 DTB 工具参数截断，但主因不是 parser：133 个 DTB invalid-JSON 中，132 个在
parser 前已经没有完整 XML；其中 131 个 final-answer 子请求明确被字面 `</think>` stop 截断。
源码同时确认 final-answer 阶段错误地追加了该思考 stop。剩余 1 个样本的 parser 前文本包含完整
tool XML，而 SSE arguments 缺 `}`，是独立的 multi-call burst parser 缺陷。

196 不能直接解释为 196 个传输故障。53 个 `expected_tool_name_missing` 是固定单工具 oracle 与模型
选择不同，例如报告场景先调用 `bash` 而非 `write`；它们需要语义裁决，不能和 malformed JSON、
XML 泄漏或 terminal 缺失放在同一个协议失败分母中。可用性门未通过，因此本轮时延不参与排名。

## 输入与证据边界

- 实测输入：`summary.jsonl`、`chunks.jsonl`、`analysis.json`、Prometheus 前后快照和
  `paired_summary.json`；600/600 客户端结果、300/300 pair 均存在。
- 本目录没有原始 `server.log`，不能本地重新提取；根因复核使用已上传 `analysis.json` 中按请求保存的
  parser 前 raw、child phase、source finish/stop 和 terminal 契约。
- `manifest.json` 明确记录 10 个 case、每个 30 次、Vanilla/DTB 交替、DTB 固定参数；其余未声明项
  按实验说明继承模板。本目录未固化的源码/patch hash 不能从该声明反推出，下一轮必须实际记录。

## 异常统计

| 客户端异常标签 | DTB | Vanilla | 两边均出现该类型 | 性质 |
| --- | ---: | ---: | --- | --- |
| `invalid_tool_arguments_json` | 133 | 0 | 否 | 协议 hard failure |
| `expected_tool_name_missing` | 24 | 29 | 是 | 工具选择/oracle 偏差，不自动等于协议故障 |
| `expected_tool_call_missing` | 9 | 1 | 是 | 模型、控制器或终止行为；需 raw 定责 |
| `missing_terminal` | 1 | 0 | 否 | completion lifecycle hard failure |

共有 4 种标签，2 种在 Vanilla 和 DTB 中都出现。标签计数为 197，而异常响应为 196，是因为一个 DTB
响应同时命中 `expected_tool_call_missing` 与 `missing_terminal`。

按 pair 统计：128/300 两边都 clean；172/300 至少一边异常；24/300 两边都有异常；只有 12/300
两边异常标签完全相同，均为 `expected_tool_name_missing`。因此“共同错误”若按类型是 2 种，若按
paired attempt 是 24 对，若要求同一标签则是 12 对。

## 对端到端故障的复现

| 原故障族 | 回放观察 | 裁决 |
| --- | --- | --- |
| case36 `task` arguments 截断 | line 1～6 的 180 个 DTB 请求中 125 个 invalid JSON；180 个 Vanilla 为 0 | **已稳定复现**，且明显为 DTB 路径特异 |
| case35 checkout action 缺失 | line 7～8 没有 `expected_tool_call_missing`；只有 1 个 DTB、2 个 Vanilla 选择 `todowrite` 而非固定预期 `bash` | **未复现原缺调用**；这是 alternate action，不是 parser 证据 |
| case36 report write 缺失 | line 9：DTB/Vanilla 各 1 次缺调用；line 10：DTB 8 次缺调用，Vanilla 0 次，但 Vanilla 有 19 次先选 `bash` | **部分复现**；两边均可出现模型/流程选择偏差，DTB 更高，需修复后重跑 |

## 根因链

### 1. DTB final-answer stop 碰撞：主因

133 个 DTB invalid-JSON 响应中：

- 132 个 parser 前 raw 有 `<tool_call>`，但缺完整 `</function></tool_call>`；parser 不可能恢复模型未生成
  的 XML，也不应人工补齐。
- 这 132 个中，131 个 final-answer child 的 `source_finish_reason=stop` 且
  `source_stop_reason=</think>`；另 1 个 stop reason 为空。
- raw 尾部恰好停在工具参数正文中的字面 `</think>` 前：task 描述引用
  `THINK_BLOCK = "</think>"`，report/write 内容也会讨论该 marker。
- 源码确定：旧 `configure_final_answer_stop()` 对 final-answer sampling params 调用
  `_append_stops(params, "</think>")`。因此已关闭 reasoning 的最终答案仍把思考边界当 stop，导致 XML
  参数正文在合法字面 marker 处终止。

修复：final-answer 使用从原请求重新构造的 sampling params，不再追加任何 DTB 内部思考 stop；思考
阶段仍保留 `</think>` 与 `<tool_call>` boundary stop。新增状态机回归断言，确保 initial-think 含该
stop、final-answer 不含该 stop。

### 2. Qwen3-Coder streaming parser 分片敏感：独立次因

`dtbtool-l10-a5-dtb-run04-p0` 的 parser 前 raw 至少包含完整 tool XML，但客户端首个 `write`
arguments 缺最终 `}`；同一 final child 后续还有相邻调用/正文 burst。这命中 legacy parser 在
tool-start、header、JSON-open、parameter-close 与 call-advance 各处分支提前 return 的缺陷族。

新的 v0.23.0 patch 不再逐 parameter 猜测可增长 JSON 前缀，而是在累计文本中：header 完整即发工具
名称，`</function>` 完整后用同一非流式 XML parser 生成 canonical JSON arguments 并一次发送；单个
callback 可聚合多个连续 index 的 `DeltaToolCall`。它覆盖社区 #39598/#43074/#45256/#45439/#45440
的 parser burst 形状，且不会为 incomplete XML 合成 close。patch SHA256：
`f8405cdfa04d69e942ac77fdb21f93a5d94a0c749d471ed03f9c921b2336dad2`。

### 3. 其他可用性项

- `dtbtool-l10-a28-dtb-run04-p0` 是唯一 completion lifecycle failure，同时命中缺调用与缺 terminal；
  它不由 parser patch 解释，下一轮必须继续保持 terminal/passthrough 审计。
- 53 个工具名偏差应从协议门移到语义门。尤其 line 10 的 `bash` 可能是生成报告前的合法检查步骤；
  下一轮使用允许工具集合/状态转换 oracle，并另报具体选择分布。
- `analysis.json` 的历史 diagnosis 把 raw-incomplete、parser failure 和工具选择混为
  `downstream_parser_or_finish_failure`。分析器已增加 stop-collision、complete-XML parser failure、
  truncated XML 与 semantic tool choice 的独立分类；因本归档没有原始 server.log，未覆盖写回旧
  `analysis.json`，上述复核来自其已保存的请求级 raw 证据。

## 时延

原始 300 对中 DTB 较快 177 对、配对中位数表面 speedup 15.541%；但只剩 128 对双方协议 clean。
clean 子集上 DTB 仅快 43/128，中位 `DTB - Vanilla = +546.507 ms`，即 DTB 中位慢 15.773%。这两个
数字都不能用于 DTB 时延结论：失败请求提前被 `</think>` 截断会制造虚假加速，clean-only 又是由故障
机制选择出的偏差子集。本轮 `latency_ranking_valid=false` 的裁决保持不变。

## 下一轮完整 paired replay

### A. 离线与部署身份门

1. 对固定 `third_party/vllm` 执行两个 patch 的 `git apply --check`；保存 vLLM/vLLM-Ascend commit、
   import path、实际 parser 源码 SHA、DTB commit、两个 patch SHA、配置 hash 和启动加载类。
2. 对应用新 parser patch 的源码运行 `parser_partition_equivalence.py --require-equivalence`。必须同时
   通过历史 parameter-close、whole body、whole call、last-param+close 和 first-close+second-call，
   且 streaming 重组结果与非流式 parser oracle 一致。
3. 状态机测试必须证明 final-answer stop 不含 DTB 注入的 `</think>`；用户原生 sampling stop 不得被
   清空或改写。

### B. 定向服务器矩阵

使用本轮同一 10 个真实多轮输入，单并发、每 case 每 variant 30 次、Vanilla/DTB 交替；补一个明确在
tool argument 中包含字面 `</think>` 的最小 canary，以及完整 single-call/two-call burst canary。
服务器保持生产默认 `stream_interval=1`，不把 interval 作为实验变量，分别执行：

- `stream=true` 定向回放；
- `stream=false` 协议 smoke，验证原生完整响应 parser；
- DTB 固定参数保持本轮值，Vanilla 保持原生路径；请求采样、seed、到达节奏和 cache 策略相同。

仅对这些 request ID 开 `stream_audit`，每个失败样本保存 parser 前 raw、forwarded text/token IDs、
source/forwarded finish/stop、completion epoch 和 terminal origin。不要把 telemetry run 用作性能排名。

### C. 双层裁决与准入

协议层对两个 variant 分别要求：request error、invalid JSON、schema failure、XML leak、index 不连续、
tool 后 content、terminal 缺失/重复、terminal 后 chunk、raw-complete 但 parser 丢调用全部为 0；DTB
还要求 final-answer `source_stop_reason=</think>` 为 0，单 completion、单 source terminal、无 synthetic
terminal 和 passthrough violation 为 100%。

语义层不再要求每个上下文只能调用一个固定工具。每个 case 配置 `allowed_next_tools` 或可审计状态转换；
`bash→write`、`todowrite→bash` 等合法前置步骤单列，不计协议失败。报告每对的 tool-call presence、首个
工具、完整名称序列和 schema-valid arguments；确定性配置要求 paired outcome 完全一致，生产采样配置
则报告每类转移和 McNemar/置信区间，不以一次随机不同判 parser 回归。

只有上述可用性门通过，才关闭 `stream_audit`，在同服务身份下另跑 `observability=off` 的 9 场景
Vanilla/DTB 单并发性能实验。该性能 run 再做 E2E、completion token、质量与 DTB 隐藏工作量配对比较。

## 下一轮 paired replay 完整命令

以下命令以 Linux 服务器、仓库 `/opt/p00906181/TB-main`、同一服务端口 9000 为例。`VLLM_ROOT`
必须指向实际运行服务加载的固定 vLLM `v0.23.0` 源码。唯一无法由仓库给出的命令是服务器模板的完整
启停命令；它必须从上一轮 manifest 原样复用并保持生产默认 `stream_interval=1`。不能用一个新写的
简化 `vllm serve` 命令替代生产模板，也不需要增加 `--stream-interval` 参数。

### 1. 定义路径并验证代码和 patch

```bash
set -euo pipefail

TB_ROOT=/opt/p00906181/TB-main
VLLM_ROOT=/path/to/the/runtime/vllm-v0.23.0
INPUT="$TB_ROOT/docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl"
RUN_ROOT="$TB_ROOT/docs/log/perf/agent_tool_call_run04_paired_replay/run_server_02"
DTB_CONFIG="$TB_ROOT/dtb/config/budget_config.yaml"
SERVER_URL=http://127.0.0.1:9000/v1/chat/completions
METRICS_URL=http://127.0.0.1:9000/metrics
ALLOWLIST="$RUN_ROOT/stream_request_ids.txt"

mkdir -p "$RUN_ROOT"
cd "$TB_ROOT"

git rev-parse HEAD
git -C "$VLLM_ROOT" describe --tags --always --dirty
git -C "$VLLM_ROOT" status --short
git -C "$VLLM_ROOT" apply --check \
  "$TB_ROOT/patches/vllm/v0.23.0/dtb_serving.patch"
git -C "$VLLM_ROOT" apply --check \
  "$TB_ROOT/patches/vllm/v0.23.0/qwen3_coder_parser.patch"
sha256sum \
  patches/vllm/v0.23.0/dtb_serving.patch \
  patches/vllm/v0.23.0/qwen3_coder_parser.patch \
  dtb/config/budget_config.yaml
```

上述 `apply --check` 以 clean v0.23.0 checkout 为前提。通过后才实际应用；如果目标 checkout 已经有
旧 patch，不要强行重复 apply，应先重新准备与服务器版本一致的 clean checkout：

```bash
git -C "$VLLM_ROOT" apply \
  "$TB_ROOT/patches/vllm/v0.23.0/dtb_serving.patch"
git -C "$VLLM_ROOT" apply \
  "$TB_ROOT/patches/vllm/v0.23.0/qwen3_coder_parser.patch"

python tools/dtb/parser_partition_equivalence.py \
  --qwen-parser-source \
  "$VLLM_ROOT/vllm/tool_parsers/qwen3coder_tool_parser.py" \
  --summary-only --require-equivalence \
  > "$RUN_ROOT/parser_partition_equivalence.json"

python -m unittest \
  tests.test_tool_call_debug \
  tests.test_dtb_state_machine
```

`parser_partition_equivalence.py` 必须退出 0，且输出中的 `burst_regressions.all_pass=true`。应用 patch
后还要确认服务的 Python 环境实际 import 这个文件，而不是另一份 site-packages：

```bash
python - <<'PY' | tee "$RUN_ROOT/parser_import_identity.txt"
import hashlib
import inspect
from pathlib import Path
from vllm.tool_parsers.qwen3coder_tool_parser import Qwen3CoderToolParser

path = Path(inspect.getfile(Qwen3CoderToolParser)).resolve()
print(f"parser_path={path}")
print(f"parser_sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
PY
```

### 2. 一次生成两个子 run 的 stream-audit 白名单

流式子 run 和非流式子 run 使用不同的 request-id suffix，避免日志串 run。每个 suffix 产生 600 个
Vanilla/DTB ID，共 1200 个 ID：

```bash
cd "$TB_ROOT"
for RUN_ID in \
  run04-fix-stream \
  run04-fix-nonstream
do
  python tools/dtb/replay_tool_calls.py \
    --file "$INPUT" \
    --start 1 --end 10 \
    --mode both \
    --repetitions 30 \
    --request-id-run "$RUN_ID" \
    --list-request-ids
done | sort -u > "$ALLOWLIST"

test "$(wc -l < "$ALLOWLIST")" -eq 1200
head "$ALLOWLIST"
tail "$ALLOWLIST"
```

备份配置，将白名单写入 `OBSERVABILITY`，并保存本轮实际配置。这个 YAML 在进程启动时读取，所以
写完后必须重启服务：

```bash
cp "$DTB_CONFIG" "$RUN_ROOT/budget_config.before.yaml"

python - "$DTB_CONFIG" "$ALLOWLIST" <<'PY'
import sys
from pathlib import Path
import yaml

config_path = Path(sys.argv[1])
ids_path = Path(sys.argv[2])
config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
request_ids = [line.strip() for line in ids_path.read_text(
    encoding="utf-8").splitlines() if line.strip()]
assert len(request_ids) == 1200
config["OBSERVABILITY"]["mode"] = "stream_audit"
config["OBSERVABILITY"]["stream_request_ids"] = request_ids
config_path.write_text(
    yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
PY

cp "$DTB_CONFIG" "$RUN_ROOT/budget_config.stream_audit.yaml"
sha256sum "$DTB_CONFIG" | tee "$RUN_ROOT/budget_config.sha256"
```

### 3. 记录部署身份

```bash
cd "$TB_ROOT"
{
  date --iso-8601=seconds
  printf 'tb_commit=' && git rev-parse HEAD
  printf 'tb_dirty=\n' && git status --short
  printf 'vllm_commit=' && git -C "$VLLM_ROOT" rev-parse HEAD
  printf 'vllm_dirty=\n' && git -C "$VLLM_ROOT" status --short
  python -m pip show vllm vllm-ascend torch torch-npu
  sha256sum \
    patches/vllm/v0.23.0/dtb_serving.patch \
    patches/vllm/v0.23.0/qwen3_coder_parser.patch \
    "$DTB_CONFIG"
} > "$RUN_ROOT/deployment_identity.txt" 2>&1
```

### 4. 定义单并发回放函数

脚本默认只有一个 HTTP connection，按 repetition 交替 Vanilla/DTB。它在观察到异常时会返回 1，
所以命令必须保存退出码后继续提取服务日志；大于 1 才表示执行器自身失败。`--no-stream` 使用原生完整
ChatCompletion JSON，并复用同一套 tool JSON/schema/terminal 裁决。

```bash
cd "$TB_ROOT"

run_replay () {
  RUN_ID="$1"
  RESPONSE_FLAG="$2"
  SERVER_LOG="$3"
  OUT="$RUN_ROOT/$RUN_ID"
  BEFORE="$RUN_ROOT/$RUN_ID.metrics.before.prom"

  curl -fsS "$METRICS_URL" -o "$BEFORE"

  set +e
  python tools/dtb/replay_tool_calls.py \
    --file "$INPUT" \
    --start 1 --end 10 \
    --mode both \
    --repetitions 30 \
    --request-id-run "$RUN_ID" \
    --order alternating \
    "$RESPONSE_FLAG" \
    --url "$SERVER_URL" \
    --output-dir "$OUT" \
    --fixed-budget-tokens 20000 \
    --fixed-check-length 850 \
    --fixed-think-stride 500 \
    --fixed-num-thinking 5 \
    --fixed-min-think-tokens 1 \
    --fixed-prob-th 0.93
  REPLAY_RC=$?
  set -e
  test "$REPLAY_RC" -le 1

  mv "$BEFORE" "$OUT/metrics.before.prom"
  curl -fsS "$METRICS_URL" -o "$OUT/metrics.after.prom"
  cp "$RUN_ROOT/deployment_identity.txt" "$OUT/"
  cp "$RUN_ROOT/budget_config.stream_audit.yaml" "$OUT/"
  printf 'response_flag=%s\nstream_interval=1(default)\nreplay_exit=%s\n' \
    "$RESPONSE_FLAG" "$REPLAY_RC" \
    > "$OUT/server_variant.txt"

  set +e
  python tools/dtb/analyze_tool_call_run.py "$OUT" "$SERVER_LOG"
  ANALYZE_RC=$?
  set -e
  test "$ANALYZE_RC" -le 1
  printf 'analyze_exit=%s\n' "$ANALYZE_RC" \
    >> "$OUT/server_variant.txt"
}
```

### 5. 使用生产默认配置执行流式子 run

用上一轮 manifest 的**完整模板启动命令**启动一次服务，保持默认 `stream_interval=1`，不增加 interval
参数。模型、TP/DP、MTP、cache、端口和环境变量全部复用生产模板：

```bash
# 服务终端
<TEMPLATE_START_COMMAND> 2>&1 | tee "$RUN_ROOT/server.log"
```

```bash
# 回放终端
until curl -fsS http://127.0.0.1:9000/v1/models >/dev/null; do sleep 2; done
run_replay run04-fix-stream --stream "$RUN_ROOT/server.log"
```

### 6. 在同一服务上执行非流式协议 smoke

非流式请求不依赖客户端 SSE 分片，但仍经过 DTB final-answer 和 vLLM 原生完整响应 parser，无需重启：

```bash
run_replay \
  run04-fix-nonstream \
  --no-stream \
  "$RUN_ROOT/server.log"
```

### 7. 自动执行硬门检查

这个检查允许 `expected_tool_name_missing` 继续进入人工语义裁决，但不允许 missing call、schema、JSON、
XML、terminal 或 parser/controller diagnosis：

```bash
python - "$RUN_ROOT" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1])
run_ids = (
    "run04-fix-stream",
    "run04-fix-nonstream",
)
allowed_semantic = {"expected_tool_name_missing"}
allowed_diagnoses = {
    "pass",
    "semantic_tool_choice_mismatch",
}

for run_id in run_ids:
    run_dir = root / run_id
    summaries = [json.loads(line) for line in (
        run_dir / "summary.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()]
    assert len(summaries) == 600, (run_id, len(summaries))
    assert Counter(row["variant"] for row in summaries) == {
        "vanilla": 300,
        "dtb": 300,
    }
    protocol = [
        (row["client_request_id"], row["protocol_anomaly_reasons"])
        for row in summaries if row["protocol_anomaly_reasons"]
    ]
    semantic_blockers = [
        (row["client_request_id"], row["semantic_anomaly_reasons"])
        for row in summaries
        if set(row["semantic_anomaly_reasons"]) - allowed_semantic
    ]
    assert not protocol, (run_id, protocol[:10])
    assert not semantic_blockers, (run_id, semantic_blockers[:10])

    analysis = json.loads((run_dir / "analysis.json").read_text(
        encoding="utf-8"))
    dtb_rows = [
        row for row in analysis["requests"] if row["variant"] == "dtb"
    ]
    bad_diagnoses = [
        (row["client_request_id"], row["diagnosis"])
        for row in dtb_rows if row["diagnosis"] not in allowed_diagnoses
    ]
    assert len(dtb_rows) == 300, (run_id, len(dtb_rows))
    assert not bad_diagnoses, (run_id, bad_diagnoses[:10])
    assert all(row["terminal_contract_valid"] for row in dtb_rows), run_id
    assert not any(
        row["final_answer_stopped_on_think_end"] for row in dtb_rows
    ), run_id
    assert not any(
        row["controller_passthrough_violation_sequences"] for row in dtb_rows
    ), run_id
    print(f"PASS {run_id}: requests=600 dtb_audit=300")
PY
```

随后单列工具选择分布，人工确认 `bash→write`、`todowrite→bash` 等是否属于合法状态转换：

```bash
python - "$RUN_ROOT" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

for run_dir in sorted(Path(sys.argv[1]).glob("run04-fix-*")):
    path = run_dir / "summary.jsonl"
    if not path.exists():
        continue
    rows = [json.loads(line) for line in path.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    counts = Counter(
        (row["line_no"], row["variant"], tuple(
            call["name"] for call in row["tool_calls"]
        ))
        for row in rows
    )
    print(f"\n[{run_dir.name}]")
    for key, count in sorted(counts.items()):
        print(count, key)
PY
```

### 8. 恢复配置

完成审计后先停止服务，再恢复原配置；下一轮性能实验必须重新以 `OBSERVABILITY.mode=off` 启动，不能
直接复用本轮 stream-audit 进程或日志：

```bash
cp "$RUN_ROOT/budget_config.before.yaml" "$DTB_CONFIG"
sha256sum "$DTB_CONFIG"
grep -A4 '^OBSERVABILITY:' "$DTB_CONFIG"
```

若任一硬门失败，保留两个子目录和对应 server log，不执行 9 场景时延排名；先按 request ID 从
`analysis.json` 回到 parser 前 raw 和 source stop/terminal 定责。两个子 run 全部通过后，才执行同
commit、同模型、单并发、`observability=off` 的 LightCode 9/9 可用性门与独立性能 run。
