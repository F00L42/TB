# Session D 本地验证记录

日期：2026-09-22。平台：Windows，CPython 3.12.13。
提取基线：TB `f342e127`。本次只提交 `thinking_observer/` 与设计入口文档，不修改生产 DTB 或只读上游源码。

## 验证结果与重跑

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

两个 parser 补丁与基线 `patches/vllm/v0.23.0/` 的 Git blob 一致，制品固定 LF 换行；`tools/parser_fixtures.py` 只提取旧工具的测试数据/import stub，不包含或替代原生 parser 算法。来源写在文件头；没有运行时 DTB 依赖。

SHA-256（本地文件字节）：

| 补丁 | SHA-256 |
| --- | --- |
| observer_serving.patch | `13113a80253bdc27a1451eef57c9e5e2205f13a49523389de21e519e46f7e04c` |
| qwen3_coder_parser.patch | `b81e468d49fb3d3b7473a843e387b60d934a2540a6fca693d05d04e997a7e25d` |
| qwen3_reasoning_parser.patch | `713e8d79b5113cf5afc9190ae987ed6752529458edcffa1521ec52e2a865cf4b` |

## 尚未验证

**pending_remote**：真实 tokenizer/模型的 text-token 连续性、EngineCore abort 调度与取消回收、真实 HTTP/SSE full response、Ascend 构建/设备行为、多 DP/LoRA/trace 实际路由、Noop 开销、外部算法的质量与端到端净收益。具体执行和停止条件在 [服务器手册](server_runbook.md)。

已知首版限制：仅 Qwen3 协议；多模态/PD/KV transfer/状态相关采样等显式回退；同步检测无硬抢占；不支持撤回/holdback；abort 尾部耗尽容量将明确失败。没有重复检测准确率证据，因为没有实现此算法。
