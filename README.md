# Thinking Observer：vLLM 0.23 集成与二次开发

本分支 `codex/thinking-observer` 提供思考流观察、停止思考和引导答案续写的实现。
向 vLLM `v0.23.0` 应用三份 patch，并安装 `thinking_observer` 即可接入；Ascend 环境沿用 `v0.23.0rc1`。
压缩算法直接在 observer 源码上修改即可，当前尚未实现重复检测算法。

## 直接集成

在目标 vLLM 0.23 部署源码上执行，路径换成实际值：

```bash
export SDK=/absolute/path/to/TB/thinking_observer
export VLLM_DEPLOY=/absolute/path/to/vllm
python -m pip install -e "$SDK"
git -C "$VLLM_DEPLOY" apply --check \
  "$SDK/patches/vllm-v0.23.0/observer_serving.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_coder_parser.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_reasoning_parser.patch"
git -C "$VLLM_DEPLOY" apply \
  "$SDK/patches/vllm-v0.23.0/observer_serving.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_coder_parser.patch" \
  "$SDK/patches/vllm-v0.23.0/qwen3_reasoning_parser.patch"
export THINKING_OBSERVER_ENABLED=1
export THINKING_OBSERVER_FACTORY=thinking_observer.detectors:NoopDetector
export THINKING_OBSERVER_TELEMETRY=off
# 按目标环境安装已打 patch 的 vLLM 并重启服务。
# 请求 JSON 增加："enable_thinking_observer": true
```

SDK 需要 Python 3.11+，自身无第三方运行时依赖。默认 `NoopDetector` 只观察、不压缩；
启动与请求示例见[服务器手册](thinking_observer/docs/server_runbook.md)。
集成不要求本地完整验收，不要求初始化或检查 `third_party/`，也不需要先构建 wheel/sdist。
三份 patch 的唯一入口是 `thinking_observer/patches/vllm-v0.23.0/`；不能与 DTB serving patch 叠加。

## 直接修改压缩算法

修改 `thinking_observer/src/thinking_observer/`：

| 修改内容 | 入口 |
| --- | --- |
| 增量判断何时结束思考、选择 cue | [detectors.py](thinking_observer/src/thinking_observer/detectors.py) |
| 接管、abort/drain、续写和预算处理 | [controller.py](thinking_observer/src/thinking_observer/controller.py) |
| 默认工厂、服务配置及 vLLM 接线 | [integration.py](thinking_observer/src/thinking_observer/integration.py)、[config.py](thinking_observer/src/thinking_observer/config.py) |

可以直接侵入式修改上述实现，不要求另建外部 detector 包。`THINKING_OBSERVER_FACTORY` 指向包内自己的类或工厂即可；
外部 factory 只是可选接入方式。使用上面的 editable 安装后，源码修改完成重启服务即可生效。
保留请求隔离、已输出 text/token 连续性、原生 parser、取消与 completion 额度语义。

## 按需检查与资料

从 TB 仓库根目录按需运行与修改相关的检查，不作为部署前置：

```bash
python -m unittest discover -s thinking_observer/tests -v
python thinking_observer/examples/observe_window.py
```

patch/原生 parser/发行包工具的可选用法见[验证说明](thinking_observer/docs/validation.md)。
它们不检查 `third_party/`；上游源码检查使用调用者显式提供的路径。

- [交付状态](RESULT.md)、[SDK 导航](thinking_observer/AGENTS.md)、[API](thinking_observer/docs/api.md)
- [算法开发入口](thinking_observer/docs/detector_development.md)、[设计](thinking_observer/docs/design.md)
- [历史来源](thinking_observer/docs/history.md)：旧 DTB/DEER 与实验材料保留在 Git 历史。

本分支交付 observer；`codex/dtb-static-refactor` 交付 DTB，两套 serving patch 是独立集成方案。
`third_party/` 的 gitlink 仅保留上游来源信息，不参与上述集成流程。
新运行产物写入 Git 忽略的 `artifacts/perf/<run_id>/` 或 `artifacts/replay/<run_id>/`。
真实设备、HTTP/SSE、质量与性能仍为 `pending_remote`；历史本地结果不代表服务器效果。
