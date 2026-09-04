# Terminal reasoning prefix 最小修复与离线验证

日期：2026-09-04。输入仍为 `915bec9` 上传的旧服务器记录，本文件不是新服务器 run 的结果。
历史 [RESULT.md](RESULT.md) 和 [AUDIT.json](AUDIT.json) 保持原裁决。

## 修复范围

新增 `patches/vllm/v0.23.0/qwen3_reasoning_parser.patch`，生产修改仅在
`Qwen3ReasoningParser.extract_reasoning_streaming()` 的 end ID 存在、marker text 缺失分支：

```python
if delta_token_ids and delta_token_ids[-1] == self.end_token_id:
    return DeltaMessage(reasoning=delta_text) if delta_text else None
```

include-stop=True 保留为 workaround/对照，不修改默认采样、stop 位置、ID、usage 或
stop_reason；非流式、implicit tool-end、unified merge 和已有 tool parser patch 均不改。
详细取舍见设计文档 3.3.1。

## 实测结果（仅本地、无模型）

| 检查 | 未修改 reasoner | 修复 reasoner |
| --- | ---: | ---: |
| patch 中的原生 regression 函数，以字符 tokenizer 执行 | 6/10 | 10/10 |
| 正常 marker、implicit tool、speculative、普通引用等原路径对照 | 10/10 | 10/10 |
| 原生 detokenizer base → reasoner → unified，include False/True × 混合/独立 boundary | 3/4 | 4/4 |
| 原始 60 条真实 text/IDs → reasoner → unified → tool | 13/60 | 60/60 |
| 合计 | 32/84，52 个失败 | 84/84，0 个失败 |

负对照的 47 个真实失败恰好是原先丢字的请求。新版本恢复全部 268 字符、117 个非边界 token；
60 个请求的 tool name、arguments 字符串及 content 与原服务器公共结果不变。
DTB `l5-a30` 的原始缺必填参数仍存在：该修复不猜测或补写模型没有生成的参数。

数据：[reasoning_tail_before.json](reasoning_tail_before.json)、
[reasoning_tail_after.json](reasoning_tail_after.json)。

新的上游 test patch 使用真实 tokenizer fixture，并且显式构造“不含 marker 的 text +
普通 prefix IDs + end ID”，不依赖 tokenize 带 marker 的字符串来间接覆盖。
本地 gate 执行同一批测试函数及未修改原生类，但依赖与 tokenizer 用 stub 隔离；因此不能把
本地结果写成上游完整 pytest 或目标 NPU/HTTP 通过。

## 复算命令

从 TB 根目录执行：

```bash
# 负对照：预期退出 1，52 个失败，包括 47 个真实请求。
python tools/dtb/reasoning_tail_fidelity.py \
  --replay-dir docs/log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/replay

# 新补丁：预期退出 0，84 个检查全部通过。
python tools/dtb/reasoning_tail_fidelity.py --apply-repo-patch \
  --replay-dir docs/log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/replay

python -m unittest discover -s tests -p test_qwen_reasoning_tail.py -v
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/qwen3_reasoning_parser.patch
```

固定上游 checkout 未修改；补丁在内存应用。三个 unittest（正门、负门、真实记录）均通过。
另外 96 个 DTB 单测、31 个现有 tool-call 审计单测通过；合计 130 个 unittest。
`git apply --check --whitespace=error-all` 与 `git diff --check` 通过。

## 服务器验证

1. 按 `patches/vllm/v0.23.0/README.md` 对现有部署增量应用新 reasoning patch并重启服务，
   保留 production launch、单并发、默认 stream_interval=1、include-stop=False。
2. 记录实际服务环境 import 的 reasoning parser 路径与 SHA256。补丁后 LF 源码预期 SHA256：
   `cdb64a46e7077ff6f2600ce02916cfbd4cdb5bb6f62937673013a68e6addea88`。
3. 在已准备的上游 `.venv` 中跑完整 `tests/reasoning/test_qwen3_reasoning_parser.py`；其
   既有 fixture 是三种 Qwen tokenizer，不等于只验证目标 Qwen3.6，也不需要模型推理权重。
4. 用新 run ID/目录重放原 line 5/10，更新 DTB whitelist，保存完整 server.log 或 audit 分片。
   对 include=False 的 terminal-ID tail 逐请求检查公共 reasoning 与 source prefix 一致。
5. 可以复用 gate 核对实际部署源码（TB 中需有未修改的固定 `third_party/vllm`，用于基类与
   测试对照；它不导入目标推理设备）：

```bash
python tools/dtb/reasoning_tail_fidelity.py \
  --reasoning-parser-source /vllm-workspace/vllm/vllm/reasoning/qwen3_reasoning_parser.py \
  --tool-parser-source /vllm-workspace/vllm/vllm/tool_parsers/qwen3coder_tool_parser.py \
  --replay-dir docs/log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/replay
```

此命令仍使用旧录制输入验证新解析源码，不是重新生成。分析新 run 的 default-include-stop、
自然 explicit-end 样例时，可替换 replay-dir，并传 `--server-logs /path/to/new/server.log`
或有序 audit 分片；保留相同 `../input/cases.jsonl` 目录契约。新 run 必须额外指定
`--require-public-fidelity`，这样既检查离线解析，也检查**记录的实际公共 reasoning**与
source prefix 一致。对当前旧 run 开启此开关会正确报出 47 个失败，不能用新 parser 重放
成功冒充历史服务器已修复。该定向 gate 不适用于 forced-close/probe 等其他状态机轨迹。

例如将新 run 命名为 `run_server_05_reasoning_tail`，完成新请求回放后执行：

```bash
NEXT_RUN=docs/log/perf/agent_tool_call_run04_paired_replay/run_server_05_reasoning_tail
python tools/dtb/reasoning_tail_fidelity.py \
  --reasoning-parser-source /vllm-workspace/vllm/vllm/reasoning/qwen3_reasoning_parser.py \
  --tool-parser-source /vllm-workspace/vllm/vllm/tool_parsers/qwen3coder_tool_parser.py \
  --replay-dir "$NEXT_RUN/replay" --server-logs "$NEXT_RUN/server.log" \
  --require-public-fidelity --output "$NEXT_RUN/reasoning_fidelity.json"
```

最终门：新 HTTP 回放不丢、不重、不泄漏 marker，工具内容/terminal/usage 契约不变。
本地证据不足以关闭尚未覆盖的 `[248058]` 自然边界或 Vanilla 缺参问题。
