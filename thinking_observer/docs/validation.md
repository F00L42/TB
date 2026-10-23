# 可选检查与历史验证记录

## 当前使用方式（2026-10-23）

日常集成按[根 README](../../README.md)直接安装 observer 并向 vLLM 0.23 应用三份 patch。
不要求本地完整验收，不初始化或检查 `third_party/`，不要求先构建发行包。下列工具按修改范围选择：

| 修改内容 | 可选命令 |
| --- | --- |
| observer 算法/生命周期 | `python -m unittest discover -s thinking_observer/tests -v` |
| 观察接口示例 | `python thinking_observer/examples/observe_window.py` |
| patch 应用与语法 | `python thinking_observer/tools/verify_patch.py /path/to/vllm-0.23-source` |
| 原生 parser/serving 源码场景 | `python thinking_observer/tools/verify_native.py /path/to/vllm-0.23-source` |
| 发布发行包 | `uv build thinking_observer --out-dir artifacts/dist`，再按需运行 `verify_distribution.py` |

两个上游源码检查工具只读取显式提供的路径并在临时目录应用补丁，不查 `.git`、HEAD 或 `third_party/`。
`verify_patch.py` 不再调用 `build_patch.py` 或要求 patch 与生成器输出相同；应用失败与 Python 编译错误仍会报错。
`build_patch.py` 仅是从原始固定基线重建 serving patch 的可选维护工具，其 SHA 约束不属于部署检查。
以下历史结果保留原证据范围，不构成每次开发或集成都要重跑的清单。

## 2026-10-21 裁剪后的完整复核

平台：Windows / CPython 3.12.13。裁剪基线为 `7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d`；
SDK 源码、协议测试、三份 patch 与验证工具没有改变。删除旧实验树后重新执行：

| 验证 | 本次结果与范围 |
| --- | --- |
| `unittest` | 34 项通过，1.711 秒；真实 asyncio 与 fake engine |
| `examples/observe_window.py` | PASS：有界 token/text 窗口、text-only/缺失文本、token 偏移、请求隔离和清理 |
| `tools/verify_patch.py` | PASS：3 patch 组合应用、5 个修改文件编译；固定 vLLM 源码保持只读 |
| `tools/verify_native.py` | PASS：7 组原生 parser 场景、8 条 patched serving 路由；数据/engine/HTTP 为 stub |
| wheel + sdist | PASS：从源码分发包构建 wheel；sdist 包含 AGENTS、完整 docs、两例子、tests、patches、tools |
| wheel 独立安装 | PASS：新虚拟环境、无依赖安装；复制测试至仓库外再跑 34 项通过，1.725 秒 |

本机 SDK worktree 的 `third_party/` 是未初始化占位，本次两个验证工具显式使用另一份已初始化的
只读 checkout：`C:/Users/F-O-O/Projects/TB/third_party/vllm`，HEAD 为
`0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665`。这是当时的验证来源记录，日常接手无需初始化子模块。
当时没有修改子模块或服务实例。

Windows 非交互子进程显式 `stdin=subprocess.DEVNULL`；验证在正常主机权限下执行。
新构建输出使用仓库根 `artifacts/dist/`；源代码树与 Git 历史的体积不包含这些忽略的本地制品。
真实模型、HTTP/SSE、Ascend、真实并发、质量及净 E2E 收益仍为 `pending_remote`。

## 2026-09-22 历史验证结果与重跑

SDK 安装后运行（也可令 PYTHONPATH 指向本目录 `src/`）：

```bash
python -m unittest discover -s thinking_observer/tests -v
python thinking_observer/tools/verify_patch.py /path/to/pristine/vllm
python thinking_observer/tools/verify_native.py /path/to/pristine/vllm
uv build --python 3.12 thinking_observer --out-dir thinking_observer/dist
python thinking_observer/tools/verify_distribution.py \
  thinking_observer/dist/thinking_observer-0.1.0-py3-none-any.whl
```

| 验证 | 结果 | 证据边界 |
| --- | --- | --- |
| `unittest` 34 项 | PASS | fake engine + 真实 asyncio；包括 20 请求状态隔离、取消、abort/drain、错误、额度和 telemetry |
| 原生 parser 场景 7 组 | PASS | 执行固定版本 Qwen/DelegatingParser 源码；数据类型和字符 tokenizer 为 stub |
| patched serving 路由 8 条 | PASS | 执行补丁后的 `_create_chat_completion`；stream/full × enabled/disabled/request off/rejected；engine/render/HTTP 为 stub |
| 三份补丁组合 | PASS | 在临时目录应用并编译 5 个被修改 Python 文件；未修改上游 checkout |
| 独立 wheel / sdist 构建 | PASS | wheel 仅包含 SDK，源码包含文档/示例/测试/补丁/工具 |
| wheel 隔离安装与 34 项测试 | PASS | 全新环境，`--no-deps` 安装；复制测试到仓库外执行；屏蔽 dtb/deer/vllm/torch/yaml 导入仍能导入 SDK |

`verify_native.py` 的 7 组：原生工具、强制 plain final、强制工具 final、drain 已进入工具、同 batch 工具边界优先、drain 自然终态优先、text-only abort 尾部。每组将 SDK 产出的 raw deltas 分别送入真实增量 parser 和完整 parser，比对 reasoning/content/工具名/JSON 参数，并核对唯一终态。

两个 parser 补丁与 SDK 基线提交 `7ccd6b99` 中 `patches/vllm/v0.23.0/` 的 Git blob 一致（旧根目录已裁剪，运行不依赖它），制品固定 LF 换行；`tools/parser_fixtures.py` 只提取旧工具的测试数据/import stub，不包含或替代原生 parser 算法。来源写在文件头；没有运行时 DTB 依赖。

SHA-256（本地文件字节）：

| 补丁 | SHA-256 |
| --- | --- |
| observer_serving.patch | `13113a80253bdc27a1451eef57c9e5e2205f13a49523389de21e519e46f7e04c` |
| qwen3_coder_parser.patch | `b81e468d49fb3d3b7473a843e387b60d934a2540a6fca693d05d04e997a7e25d` |
| qwen3_reasoning_parser.patch | `713e8d79b5113cf5afc9190ae987ed6752529458edcffa1521ec52e2a865cf4b` |

## 尚未验证

**pending_remote**：真实 tokenizer/模型的 text-token 连续性、EngineCore abort 调度与取消回收、真实 HTTP/SSE full response、Ascend 构建/设备行为、多 DP/LoRA/trace 实际路由、Noop 开销、外部算法的质量与端到端净收益。具体执行和停止条件在 [服务器手册](server_runbook.md)。

已知首版限制：仅 Qwen3 协议；多模态/PD/KV transfer/状态相关采样等显式回退；同步检测无硬抢占；不支持撤回/holdback；abort 尾部耗尽容量将明确失败。没有重复检测准确率证据，因为没有实现此算法。
