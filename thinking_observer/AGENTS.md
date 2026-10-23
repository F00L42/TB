# Thinking Observer SDK 修改导航

先读本目录 [README](README.md) 与 [API](docs/api.md)。本 SDK 可独立安装；不需要阅读或安装
已退出当前树的 `dtb/`、`deer/`、`think_budget/`；历史资料见 [来源索引](docs/history.md)。新 Agent 开发检测器先运行
[观察窗口示例](docs/detector_development.md)，不要从 DTB 观察状态复制一部分代码重新拼接。

| 修改目标 | 首查符号或文件 | 验证 |
| --- | --- | --- |
| 自己的增量检测算法 | `api.py::Detector/DetectorState/ReasoningDelta`、`examples/observe_window.py` | 合成 delta、空文本/text-only/边界、跨请求隔离；再接真实服务 |
| 返回停止与答案引导 | `api.py::StopAndAnswer`、`controller.py::ThinkingObserver.stream` | `tests/test_observer.py` 的接管、自然竞态、drain、额度测试 |
| 服务开关、工厂与成本限制 | `integration.py::VllmObserver.from_env`、`config.py::ObserverConfig` | 关闭态不导入 factory、错误/超时软检查 |
| 参数能力与原生回退 | `integration.py::capability_rejection`、`VllmObserver.prepare` | 能力拒绝必须发生在 start/engine 提交前 |
| tokenizer/parser 边界绑定 | `integration.py::bind_qwen3`、`api.py::BoundaryProtocol` | 活动 tokenizer ID、原生 parser 场景 |
| 非流式/取消与资源回收 | `integration.py::aggregate_deltas/close_with_response` | 原生完整 parser、一个终态、close 一次 |
| vLLM serving 接线 | `patches/vllm-v0.23.0/observer_serving.patch`、`tools/build_patch.py` | `tools/verify_patch.py`、`tools/verify_native.py` |
| SDK 安装与发行范围 | `pyproject.toml`、`MANIFEST.in` | `tools/verify_distribution.py` |

路径未注明目录时，SDK Python 文件在 `src/thinking_observer/`。

## vLLM 版本变化时

当前工具固定到 vLLM `0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665`，不是通用新版本 patch 生成器。
不要只改 `VLLM_SHA` 或放宽检查后声称适配完成。先在独立部署 checkout 比较下列接口，再提出新版本目录的 patch 方案。

| 上游变化点 | 本 SDK 受影响位置 | 必须保持的行为 |
| --- | --- | --- |
| `ChatCompletionRequest` / `_create_chat_completion` / generate 参数 | `tools/build_patch.py`、serving patch、`VllmObserver.prepare/generate` | 双开关；不支持请求走原生；priority、LoRA、trace、显式 DP rank、parser kwargs 逐 child 透传 |
| `RequestOutputKind.DELTA`、RequestOutput/CompletionOutput、终态语义 | `controller.py::validate_result/copy_output/ThinkingObserver.stream` | 新增 text/ID 顺序、完整 batch、单一终态、abort/drain 的所有权与自然竞态 |
| token prompt / sampling / context/completion 限额 | `capability_rejection`、controller 的 prefix/cue/final 计算 | 原 prompt + 已接受 ID 前缀为权威，容量不足不伪造答案 |
| Qwen reasoning/tool parser、DelegatingParser | `bind_qwen3`、两份 Qwen parser patch、`tools/parser_fixtures.py` | 流式 text/ID 连续；非流式仍由原生完整 parser 解析 |
| response 生成器的关闭/异常生命周期 | `close_with_response`、serving patch | 消费者取消传播，child 回收，detector close 一次 |

先检查新上游是否已包含两份 parser 修复；已上游化的修复不重复打补丁，但保留原有回归场景。
`build_patch.py`、`verify_patch.py`、`verify_native.py` 及 `parser_fixtures.py` 含固定版本假设，升级时一起审查。
每个新版本记录准确 commit、patch hash、模型/tokenizer/parser 身份与能力矩阵；旧版本制品仍须可复现。

本地顺序：SDK 单测 → 新版本 patch 应用/编译 → 原生 parser 与 patched serving 路由 → 独立安装检查。
随后按 [服务器手册](docs/server_runbook.md) 做真实 tokenizer、SSE/JSON、取消/并发/额度验收。
本地 PASS 不能替代 `pending_remote`，也不能证明重复检测准确率或净收益。

## 扩展边界

每个请求由 `start(context)` 创建新状态；同请求 `observe(delta)` 有序同步调用，结束 `close(outcome)` 一次。
回调只做有界增量 CPU 工作，不写文件、不联网、不调用模型、不持有 engine/parser。
`callback_limit_ms` 是返回后的软检查，不会抢占卡死回调。不要在共享 factory 上存请求文本或窗口。

`delta.text` 与 `delta.token_ids` 是两个观察通道；不要重新分词文本来制造“原始 token”，也不要假定逐 token 字符对齐。
自然边界、终态与 abort drain 的规则以 API 为准；SDK 的 `repeat_start` 仅用于诊断，不能删除已输出内容。
SDK serving patch 与 DTB serving patch 不叠加。新运行输出进入根 `artifacts/`，保持源码和无模型 fixtures 可追溯。
