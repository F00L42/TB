# vLLM 升级与 DTB 接口迁移

先沿原生 chat serving → DTB controller → engine child → 原生 response parser 的调用链核对契约，再生成目标版本 patch。patch 能应用仅说明文本上下文匹配，不代表 token、取消或 parser 行为兼容。

本文依据特性分支 `codex/dtb-static-refactor` 的 `c716f588` 源码整理，核对日期为 2026-10-21；未选择或验证新的目标版本。SDK 分支的观察器是另一套集成，不能把这里的 DTB patch 直接当作 SDK patch。

## 当前基线与阅读顺序

| 对象 | 固定身份 / 唯一入口 |
| --- | --- |
| vLLM | `v0.23.0`，gitlink `0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665` |
| vLLM-Ascend | `v0.23.0rc1`，gitlink `f4a08bddd0cc65a0bd8c3d377b158ae5ca7527db` |
| DTB 行为 | [`dtb/AGENTS.md`](../../dtb/AGENTS.md)、[`DtbRequestController`](../../dtb/request_controller.py) |
| 上游改动 | [`patches/vllm/v0.23.0/`](../../patches/vllm/v0.23.0/README.md)：serving、tool parser、reasoning parser 三个 patch |
| 配置与部署 | [`dtb/README.md`](../../dtb/README.md)、[部署与回滚](../design/dtb_system_routing_deployment.md) |
| 本地 / 服务器门禁 | [`check_static.py`](../../tools/dtb/check_static.py)、[replay 手册](../../validation/replay/README.md) |

`third_party/` 是固定、只读的分析基线。改上游实现、应用 patch 和部署使用独立 checkout；不要在子模块中直接开发。vLLM 0.20.2 的历史说明不能替代这份基线。

## 一次请求从哪里进入、从哪里返回

1. 上游 `OpenAIServingChat._create_chat_completion()` 完成原生请求预处理，取得 conversation、engine inputs、tokenizer、LoRA 和 DP rank。
2. serving patch 检查请求开关与服务总开关，再调用 `prepare_request_route()` 一次。`native` 返回原生单次生成；`dtb` 把 `PreparedDtbRequest` 快照传给 `handle_request()`。
3. patch 计算原生 completion 额度，并通过请求私有属性 `_dtb_route_identity` 传递模型、parser、chat template、trace、LoRA、DP 等上下文。它是进程内接口，不是公开 HTTP 字段。
4. controller 创建单请求 `DtbRequestContext` 和状态机。流式先观察原生输出，再按既有条件介入；非流式走状态机。每个 child 顺序调用 engine，并使用 `engine_request_kwargs()` 传递同一外部请求身份。
5. [`response_adapter.py::native_chat_response()`](../../dtb/integration/vllm/response_adapter.py) 把流式 DELTA 送回原生 `chat_completion_stream_generator()`；非流式先聚合，再调用原生 `chat_completion_full_generator()`。不要改成自行拼装 HTTP/SSE 响应。

## 按上游变化定位本仓库接口

表中上游路径相对目标 vLLM checkout。当前路径若被移动，先用符号搜索找到新位置，再比较调用者和实现。

| 上游检查点 | 本仓库修改点 | 必须保留的契约 / 首选回归 |
| --- | --- | --- |
| `entrypoints/openai/chat_completion/protocol.py`：`ChatCompletionRequest`、`to_sampling_params()` | [`dtb_serving.patch`](../../patches/vllm/v0.23.0/dtb_serving.patch)、controller 请求覆盖 | 字段默认值、校验、原生 sampling 转换；`test_dtb_config.py`、`test_dtb_system_routing.py` |
| `entrypoints/openai/chat_completion/serving.py`：`_create_chat_completion()`、prompt 提取、parser 构造；`entrypoints/serve/utils/api_utils.py::get_max_tokens()` | 同一 serving patch、[`request_controller.py`](../../dtb/request_controller.py) | 路由一次、native 单次 generate、completion 总额度、模板与 parser 上下文；`test_dtb_system_routing.py`、`test_dtb_token_prompt.py` |
| 同一 serving 模块：`chat_completion_stream_generator()` / `chat_completion_full_generator()` 的签名 | [`response_adapter.py::native_chat_response()`](../../dtb/integration/vllm/response_adapter.py) | 流式 reasoning parser / 非流式完整 parser、metadata、取消与异常；`test_dtb_prompt_copy.py`、`test_dtb_token_prompt.py` |
| `engine/protocol.py::EngineClient.generate/abort`、`v1/engine/async_llm.py::AsyncLLM.generate/abort` | [`request_context.py::engine_request_kwargs()`](../../dtb/reasoning/request_context.py)、[`deferred_observation.py`](../../dtb/reasoning/deferred_observation.py)、[`generation_states.py`](../../dtb/reasoning/generation_states.py) | 每个 child 的 priority、LoRA、trace、DP 亲和性、reasoning 上下文；abort 后 drain、自然终态竞争；`test_dtb_intervention.py` |
| `inputs/` 的引擎 prompt 类型、`outputs.py::RequestOutput/CompletionOutput`、DELTA / CUMULATIVE 行为 | [`request_context.py`](../../dtb/reasoning/request_context.py)、[`request_state.py`](../../dtb/reasoning/request_state.py)、response adapter | prompt 所有权和 child 快照、text/token_ids 连续性、finish/stop_reason；`test_dtb_prompt_copy.py`、`test_dtb_token_prompt.py`、`test_dtb_final_answer.py` |
| `sampling_params.py`：stop token 的初始化及内部缓存 | [`request_adapter.py::merge_stop_token_ids()`](../../dtb/integration/vllm/request_adapter.py)、[`parser_boundary.py::configure_thinking_stop()`](../../dtb/reasoning/parser_boundary.py) | 当前版本需同步公开 stop IDs 与 `_all_stop_token_ids`；目标版本应重新确认；`test_dtb_reasoning_protocol.py` |
| `reasoning/qwen3_reasoning_parser.py`、`tool_parsers/qwen3coder_tool_parser.py`、`parser/abstract_parser.py`、`v1/engine/detokenizer.py` | 两个 parser patch、[`parser_boundary.py::bind_reasoning_boundary_protocol()`](../../dtb/reasoning/parser_boundary.py)、配置中的 reasoning protocol | token-only 边界、尾部文本、tool JSON、统一 parser 增量顺序；partition / tail gates 与 `test_dtb_reasoning_protocol.py` |

测试文件均在 [`tests/`](../../tests/)。当前绑定明确要求 Qwen3 reasoning、Qwen3Coder tool parser 及配置 token ID 与实际 tokenizer 相符。换 parser 名称、模型或 tokenizer 时，不能仅放宽类型检查：需要新的协议适配和完整边界回归。

2026-10-21 已按交付范围移除 DEER 调试接口：当前 serving patch 只集成 DTB，不导入/构造 `CoTController`，也不再声明 DEER 请求字段或路由。旧实现保留在 `c716f588` 的 `deer/` 与同提交 patch。升级时检查已安装的 serving 源码，不能把删除本仓库目录当作已移除服务器上的旧补丁；按[退役升级步骤](../design/dtb_system_routing_deployment.md#deer-调试接口退役后的升级)重新部署。

## 做出可审查的新 patch 方案

1. **冻结目标。** 记录目标 vLLM / Ascend 的完整 commit、模型修订、Python / torch / torch-npu / CANN 与启动参数。没有这些信息时，产物只能是迁移方案，不能声称兼容新版本。保留旧基线以便回滚；MTP 沿用目标环境默认。
2. **核对原生变化。** 在独立 checkout 中比较上表文件与目标符号。为每处差异记录“旧契约 → 新契约 → 本仓库修改点 → 验证用例”。先判断 parser 修复是否已由上游覆盖；已覆盖且回归通过的修复无需重复回移植。
3. **修改最小边界。** serving patch 负责原生入口、上下文和调用，DTB 包负责预算与状态机。同步更新两侧接口；不得把上游完整 serving/parser 实现复制进 `dtb/`。不要把预算、cue、采样或 child 顺序变更混入接口迁移。
4. **生成并复验。** 从干净的目标 commit 修改相关上游文件，再按职责导出差异；在另一份同 commit 的干净 checkout 逐项 `git apply --check` 并应用、编译、回归。不要通过 `--reject` 留下部分应用状态。
5. **切换唯一活动版本。** 在迁移分支完成验证后，将部署 patch 目录、gitlink、静态门禁、测试加载器和当前文档切到目标版本，旧版本由 Git 历史保留。交付树仍只有一套活动部署 patch，避免多个“当前”入口。

以下是生成 patch 的 Bash 模板，`PORT` 指独立的目标 vLLM checkout，`OUT` 指迁移工作的临时输出目录；先确认目标路径与源码差异。目标版本目录移动时需要相应修改文件列表。

```bash
# 只读确认：目标 checkout 的 HEAD 必须是记录的目标 commit。
git -C "$PORT" rev-parse HEAD
git -C "$PORT" diff --check
git -C "$PORT" diff --binary --output="$OUT/dtb_serving.patch" -- \
  vllm/entrypoints/openai/chat_completion/protocol.py \
  vllm/entrypoints/openai/chat_completion/serving.py
git -C "$PORT" diff --binary --output="$OUT/qwen3_coder_parser.patch" -- \
  vllm/tool_parsers/qwen3coder_tool_parser.py
git -C "$PORT" diff --binary --output="$OUT/qwen3_reasoning_parser.patch" -- \
  vllm/reasoning/qwen3_reasoning_parser.py \
  tests/reasoning/test_qwen3_reasoning_parser.py
```

模板导出的是工作树中未暂存的差异；若改动已暂存，使用 `git diff --cached`，不要拿空文件当 patch。`--output` 直接写 Git diff 字节，避免 Windows shell 重定向引入编码变化。每个生成文件都应核对实际包含的路径和差异；目标版本新增的相关文件需明确纳入。

## 验证顺序与通过的含义

先在当前版本跑基线，再迁移测试加载器和接口。统一本地命令是：

```bash
python tools/dtb/check_static.py
```

该门禁检查导航、fixture 哈希、行尾、gitlink/子模块干净状态、patch 可应用性、编译与无模型测试。不要为新版通过而改写真实 fixture、取消失败断言，或在缺少旧日志时 skip。Windows 新增非交互子进程必须显式传 `stdin=subprocess.DEVNULL`。

当前版本的专用 parser 检查入口如下；升级时先完成下一段所列加载器迁移，再把它们作为目标版本证据。

```bash
python tools/dtb/parser_partition_equivalence.py \
  --qwen-parser-source /path/to/patched/qwen3coder_tool_parser.py \
  --summary-only --require-equivalence
python tools/dtb/reasoning_tail_fidelity.py --apply-repo-patch \
  --replay-dir tests/fixtures/parser_tail
```

**版本绑定不能漏改。** `check_static.py` 固定使用 `patches/vllm/v0.23.0`；`tests/test_dtb_system_routing.py` 复制固定子模块的 serving 并应用该 patch；`reasoning_tail_fidelity.py` 固定 parser patch 路径。`parser_partition_equivalence.py --qwen-parser-source` 只替换一个 parser 文件，其余基类和统一 parser 仍来自固定子模块。`reasoning_tail_fidelity.py` 的单文件覆盖也不是完整引擎迁移验证。用旧基类加载新 parser 得到 PASS，不能证明目标 vLLM 兼容；迁移时要让相关源码、stub 契约和目标版本保持一致，并运行目标上游自己的相关测试。

回归至少覆盖 disabled/native 单次生成、自动与 fixed 路由、流式/非流式、短请求不介入、probe 与 direct-final、abort/drain 尾流、取消/异常、自然结束竞争、token-only 边界、工具调用、多轮、总 completion 额度与 child 身份。XML 泄漏、工具标记重复/丢失、JSON 不完整、role 尾流或 finish_reason 偏离均是 hard failure。

本地检查通过后，按 [replay 手册](../../validation/replay/README.md) 在目标服务器完成 HTTP/SSE 矩阵、真实 parser 绑定、独立 phase 归属审计，再以 `off` 模式配对比较 E2E 与质量。新模型必须重跑 Vanilla。源码检查、冻结链、HTTP 200 或少发 token 都不等于质量及净 E2E 收益；未执行项目写 `pending_remote`。

交付迁移结果时保留：目标身份与 hash 清单、接口差异表、最终 patch 与 DTB 改动、各门禁命令/结果/未覆盖范围，以及可执行的旧版本恢复和重启步骤。部署必须同步更新 DTB 包、已安装 vLLM 的 patch、规范 YAML，并完整重启相关进程；只切换此仓库分支不会改变运行中的服务。
