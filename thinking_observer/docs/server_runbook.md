# Session D 服务器交付与验收

状态：**pending_remote**。本地没有目标模型/设备；下面命令尚未在 Ascend 服务执行。
不要把 SDK 的 CPU PASS、历史 DTB 成功率或 token 阈值 fixture 写成重复检测准确率或性能收益。

## 1. 固定部署身份

使用独立 TB 分支及独立 vLLM 部署 checkout；不要在其他 session 的服务上叠加补丁。

- TB：`codex/thinking-observer`，记录实际交付 SHA（`git rev-parse HEAD`）。
- vLLM：`v0.23.0`，SHA `0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665`。
- vLLM-Ascend：`v0.23.0rc1`，SHA `f4a08bddd0cc65a0bd8c3d377b158ae5ca7527db`。
- 同时记录权重路径/版本/hash、chat template、tokenizer、PyTorch、torch-npu、CANN、设备、启动命令、APC/并发/到达方式、采样/seed、数据集 hash、factory/cue 配置。

两个上游源码以仓库记录的 SHA 为准。下述 VLLM_DEPLOY 是服务器可写的部署 checkout；TB 的 `third_party/` 仍只读。

```bash
set -euo pipefail
export SDK=/absolute/path/to/TB/thinking_observer
export VLLM_DEPLOY=/absolute/path/to/separate/vllm
export MODEL_PATH=/absolute/path/to/qwen/model
test "$(git -C "$VLLM_DEPLOY" rev-parse HEAD)" = 0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665
test -z "$(git -C "$VLLM_DEPLOY" status --porcelain)"
python -m pip install "$SDK"
python -m unittest discover -s "$SDK/tests" -v
python "$SDK/tools/verify_patch.py" "$VLLM_DEPLOY"
python "$SDK/tools/verify_native.py" "$VLLM_DEPLOY"
```

任何步骤失败都停止，先核对固定版本。`verify_native.py` 用数据 stub 执行原生 parser/serving 源码，不导入设备运行时；即使在服务器通过也不等同于模型服务通过。

## 2. 应用独立补丁

`observer_serving.patch` 只添加 `enable_thinking_observer` 和薄 SDK 接入，不含 DEER、DTB 字段。**不能同时应用 `dtb_serving.patch`**。迁移现有 DTB 服务时新建干净的独立部署 checkout；不要尝试手工拼接两份 serving patch。

两份 Qwen parser patch 与基线逐字节相同，包含本 SDK 所需的结构分块与 reasoning tail 修复。

```bash
git -C "$VLLM_DEPLOY" apply --check \
  "$SDK/patches/vllm-v0.23.0/observer_serving.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_coder_parser.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_reasoning_parser.patch"
git -C "$VLLM_DEPLOY" apply \
  "$SDK/patches/vllm-v0.23.0/observer_serving.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_coder_parser.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_reasoning_parser.patch"
sha256sum "$SDK"/patches/vllm-v0.23.0/*.patch
```

随后按已有、已验证的 vLLM-Ascend 环境构建/安装该 checkout；本 SDK 不规定通用 CANN 编译命令。确认 `python -c 'import vllm; print(vllm.__file__)'` 指向实际已应用补丁的代码，记录安装包版本和 dirty diff。

## 3. 最小启动与外部算法替换

先使用 NoopDetector 验证同 child 路径，再使用确定阈值接管 fixture。下面是最小单 DP、无 KV transfer/PD 的 Qwen 服务启动例；模型路径、设备/TP 等沿用目标环境的已验证值。

```bash
export THINKING_OBSERVER_ENABLED=1
export THINKING_OBSERVER_FACTORY=thinking_observer.detectors:NoopDetector
export THINKING_OBSERVER_TELEMETRY=summary
vllm serve "$MODEL_PATH" --served-model-name observer-model \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder
```

请求：

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"observer-model","messages":[{"role":"user","content":"解释一个需要多步推理的问题。"}],"max_tokens":1024,"stream":true,"enable_thinking_observer":true}'
```

启用接管 smoke fixture，重启服务：

```bash
export PYTHONPATH="$SDK/examples${PYTHONPATH:+:$PYTHONPATH}"
export THINKING_OBSERVER_FACTORY=custom_detector:make_detector
export OBSERVER_DEMO_LIMIT=128
export THINKING_OBSERVER_CUE=$'\n请根据已有推理直接给出完整答案。\n'
```

示例 state 传了自己的 answer_cue，优先于环境默认 cue。正式替换时复制 `examples/custom_detector.py`，移除演示阈值，接上自己的增量算法，再让 factory 返回它。factory 是服务器配置，不能由请求客户端指定任意导入路径。自定义 detector state 不可复用到别的请求。

可配置变量：`ENABLED`、`FACTORY`、`CUE`、`TELEMETRY=off|summary`、`CALLBACK_MS=10`、`ABORT_TIMEOUT_S=10`（全部加 `THINKING_OBSERVER_` 前缀）。其他有界限制见 ObserverConfig；改动上限需作为部署身份记录。

## 4. 顺序验收与停止条件

每项先保存完整请求、sampling/seed、raw engine text/token deltas、SSE/完整响应、request→child 关系、summary，再做语义核对。raw token/body 只用于隔离的合成测试数据，SDK summary 本身不记录正文。

1. **原生与 Noop 对照**：同模型/样本、双开关关闭作为原生、双开关开启 Noop。分别 stream true/false。确认一个 child、没有 abort、输出原生 parser 一致；固定种子并记录可能的批处理非确定性。Noop 不等于零开销。
2. **确定接管**：用已知能超过 128 token 的合成长思考样例、TokenLimit fixture。必须看到 detected_at、actual_handoff 和尾部超出；两个 child、无 probe、一个终态。短问题自然结束可合法不触发，不能当接管通过。
3. **parser 契约**：plain answer、Qwen XML 工具调用、跨 delta 的函数名/中文/引号/参数、同 batch 的 reasoning end+tool、空 text/非空 IDs、文本延后、停止 token。stream 与 full 的最终工具名和 JSON 参数须一致，无 XML 泄漏、重复/丢 token、tool 后 role 尾流。观察器不喂正文/tool 给算法。
4. **额度**：小 max_completion_tokens、接管前不足、drain 把剩余额度吃完、长 cue 接近 max_model_len。没有超过外部 completion/context；报错不得计成功或吞成空 final。核对可见、合成 closure、cue、两个 child 的 token 分母。
5. **自然竞态**：自然终态先到不接管；abort 已发但 drain 返回 stop/length 时不再发 final；drain 已闭合思考或进入工具则不拼 cue/第二次 closure。
6. **隔离与取消**：并发不同请求，在观察、abort、final 阶段各取消；确认 child 回收、无跨请求 detector 状态、close 一次。注入普通 detector 错误继续原 child；取消和框架/parser/engine 错误必须传播。
7. **能力回退**：关闭开关、未知 parser、n>1、beam、logprobs、非默认 penalty、多模态/PD、无显式 rank 的多 DP。必须原生一次生成且不调用 start；不把请求 flag=true 当成已经介入。多 DP 支持场景给显式 rank，核对两个 child 同 rank。

任何结构错误、错误的终态、超额度、取消泄漏或跨请求状态污染：停止性能比较并保留失败记录。质量的正确/错误/未判定分开；HTTP 200 不等于任务成功。

## 5. 性能、归档与回滚

先以 summary 单独验证真实分支，再切 telemetry=off 重启做正式比较。原生、Noop、确定阈值 fixture、外部真实算法分别记录；不能混用观测模式或旧 DTB baseline。报告逐请求 E2E/TTFT、P50/P95、更快请求比例、任务正确性、公开 completion 和两个 child 的完整工作量。SDK summary 没有 queue/prefill/decode 分解；需要服务器 profiler/engine metrics 补齐，不能用全局吞吐率归因。

归档至 `docs/log/perf/<actual_observer_run>/`：manifest、安装/源码身份、补丁和配置 hash、客户端请求/响应/任务级结果、raw 测试轨迹、诊断日志、RESULT.md。记录本 run 实际数据集和评判标准；不复用历史成功率。

功能回滚：请求置 `enable_thinking_observer=false` 即原生；服务器置 `THINKING_OBSERVER_ENABLED=0` 并重启，不导入算法 factory。保留 SDK 安装，因为 serving patch 的模块导入仍需要它。

完全卸载：先停服务，在**独立部署 checkout** 对三份补丁执行 `git apply --reverse --check`，核对无其他改动后逆序移除并按环境重装 vLLM，再卸载 SDK、重启。不要在未核对的 checkout 上 reset/clean 或覆盖其他人的补丁。
