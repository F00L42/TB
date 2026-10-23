# Thinking Observer SDK 交付分支

本分支 `codex/thinking-observer` 交付独立的思维链观察与受控续写 SDK。开发入口是
[`thinking_observer/`](thinking_observer/README.md)，本分支已移除旧 DTB / DEER 实验树。
SDK 允许读取增量 reasoning 文本和 token ID，并返回停止思考、引导答案续写的意图；
没有实现重复检测算法。当前适配 vLLM `v0.23.0` / vLLM-Ascend `v0.23.0rc1`。

| 接手任务 | 阅读与执行入口 |
| --- | --- |
| 了解交付范围及验证边界 | [RESULT](RESULT.md)、[SDK 导航](thinking_observer/AGENTS.md) |
| 安装、配置和部署 | [SDK README](thinking_observer/README.md)、[服务器手册](thinking_observer/docs/server_runbook.md) |
| 获取观察器流过的文本/token，开发自己的检测器 | [开发示例与契约](thinking_observer/docs/detector_development.md)、[API](thinking_observer/docs/api.md) |
| 理解生命周期、接管和原生回退 | [设计](thinking_observer/docs/design.md)、[controller](thinking_observer/src/thinking_observer/controller.py) |
| vLLM 版本变化时定位接口与 patch | [SDK 导航](thinking_observer/AGENTS.md) 的版本适配表、[集成层](thinking_observer/src/thinking_observer/integration.py) |
| 重跑无模型验证 | [验证记录和命令](thinking_observer/docs/validation.md) |

```bash
python -m pip install ./thinking_observer
python -m unittest discover -s thinking_observer/tests -v
python thinking_observer/examples/observe_window.py
```

需要 Python 3.11+。最后一条命令仅构造 SDK 事件并验证有界观察窗口，不启动 vLLM、不访问模型。
设备、真实 tokenizer、HTTP/SSE、质量、性能与真实并发仍为 `pending_remote`。

## 两个分支的关系

- 特性分支 `codex/dtb-static-refactor`：生产 DTB 思考预算/压缩、场景路由和相应 vLLM patch。
- 本分支 `codex/thinking-observer`：独立 SDK 和可替换的 detector 接口，不依赖 DTB 预算算法。

两分支的 serving patch 是替代关系，不能叠加。SDK 的独立发行内容位于 `thinking_observer/`。
本分支只保留根导航、`thinking_observer/` 与固定上游子模块身份；旧 `dtb/`、`deer/`、
`think_budget/`、`proxy/`、`lightcode/`、根 `patches/`、旧测试/工具及实验文档已退出交付树。
历史设计、实验与关键演示材料的固定来源见 [历史索引](thinking_observer/docs/history.md)，
清理范围与本次验收见 [RESULT](RESULT.md)。不要为了新功能恢复旧接口或叠加另一分支的 patch。

上游固定依赖位于 `third_party/`，仅用于源码核对；应用补丁时使用独立部署 checkout。
新运行产物统一放入 Git 忽略的 `artifacts/perf/<run_id>/` 或 `artifacts/replay/<run_id>/`。

## 本地完整验收

从仓库根执行；已安装 SDK 后，以下检查只需要 Python、Git、uv 和固定上游源码：

```bash
git submodule update --init --recursive
python -m pip install ./thinking_observer
python -m unittest discover -s thinking_observer/tests -v
python thinking_observer/examples/observe_window.py
python thinking_observer/tools/verify_patch.py third_party/vllm
python thinking_observer/tools/verify_native.py third_party/vllm
uv build thinking_observer --out-dir artifacts/dist
python thinking_observer/tools/verify_distribution.py artifacts/dist/thinking_observer-0.1.0-py3-none-any.whl
```

`third_party/vllm/.git` 必须存在，不能仅用 `git -C third_party/vllm rev-parse HEAD` 判断子模块已初始化：
空目录会使 Git 向上找到 TB 仓库。也可向两个 verify 工具传入另一份已初始化、干净的固定版本源码路径。
三份 SDK patch 均来自 `thinking_observer/patches/vllm-v0.23.0/`；原根 `patches/` 不再是部署入口。
完整 clone 的 Git 历史体积不因本次裁剪减少；只需当前源码可浅克隆，需要原始档案时再补齐固定提交。
