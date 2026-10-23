# Think Budget：DTB 特性交付

DTB 在 vLLM 服务内控制思考预算，目标是在保持答案质量和原生 parser 协议的前提下降低端到端时延。
生产入口是 `dtb/` 与 `patches/vllm/v0.23.0/`。本次交付状态、清理范围、服务器验收命令见 [RESULT.md](RESULT.md)。

## 先确定分支

| 分支 | 交付能力 | 开发入口 |
| --- | --- | --- |
| `codex/dtb-static-refactor`（本分支） | vLLM 服务内思考预算与压缩、原生 parser 适配 | [接手导航](docs/maintenance/README.md)、[DTB](dtb/README.md) |
| `codex/thinking-observer` | 独立思考流观察与停止/引导续写 SDK；不包含重复检测算法 | 切到该分支后读根 README、`thinking_observer/AGENTS.md` 与 `thinking_observer/docs/detector_development.md` |

两套 serving patch 是独立集成方案，不能直接叠加到同一个 vLLM checkout。
两个分支的交付边界、清理记录和验收范围见[交付梳理](docs/maintenance/delivery_review.md)。

| 任务 | 从哪里开始 |
| --- | --- |
| 修改生产代码 | [DTB 导航与所有权](dtb/AGENTS.md)、[配置说明](dtb/README.md) |
| 部署、system 路由与回滚 | [路由部署手册](docs/design/dtb_system_routing_deployment.md)、[patch 说明](patches/vllm/v0.23.0/README.md) |
| 理解实现及设计依据 | [当前设计索引](docs/design/README.md) |
| 升级 vLLM 或调整接口 | [升级手册：上游差异→包内接口→patch→验收](docs/maintenance/vllm_upgrade.md) |
| 通用 vLLM 透传、记录、离线阅读 | [vllm_gateway](vllm_gateway/README.md) |
| 服务器流式/非流式、质量和配对耗时检查 | [7 条 replay 数据与矩阵](validation/replay/README.md) |
| 无模型回归与数据来源 | [fixtures](tests/fixtures/README.md)、[测试门禁](tools/dtb/check_static.py) |
| 正式性能实验 | [实验与证据契约](docs/design/think_budget_generalization_plan.md)、[服务器 runbook](docs/debug/dtb_performance_analysis.md) |
| 最终汇报 | [五页演示稿](docs/report/closeout/presentation/README.md)、[内容稿与案例选择](docs/report/closeout/outlines.md)、[固定来源索引](docs/report/closeout/sources.md) |

## 本地验证

```bash
python -m pip install -r requirements-dev.txt
git submodule update --init --recursive
python tools/dtb/check_static.py --benchmark
```

需要 Python 3.10+。检查包括固定上游身份/只读状态、3 个 patch 可应用性、编译、文档链接、
全部无模型测试；可选微基准比较 `f342e127` 旧实现与当前实现并完整回放 633 条路由。
微基准需要 Git 中存在该历史 commit（浅克隆需补取），检查产物写入 `artifacts/checks/<run>/`。
本机没有目标推理设备，服务器 E2E、质量和真实并发吞吐仍为 `pending_remote`。

## 交付边界

- `dtb/`：生产 controller；DEER 调试实现及请求接口已退役，旧代码保留在 `c716f588` 的 `deer/`，当前 patch 不再依赖它。
- `third_party/`：固定只读子模块，vLLM `v0.23.0` / vLLM-Ascend `v0.23.0rc1`。
- `vllm_gateway/`：通用 HTTP 透传和采集，不执行 DTB 算法。旧 `proxy` Python 入口已移除。
- `tools/`：replay、phase 提取、parser/介入检查及静态基准；[工具索引](tools/README.md)。
- `validation/replay/`：可直接提交给模型的合成验证集；`tests/fixtures/`：独立、可溯源的本地回归输入。
- `artifacts/`：本地/服务器运行输出，Git 忽略。API 转发 `think_budget/`、LightCode 接口和历史大日志已删除。

旧实验及被移除的文档仍可从清理前提交 `cb6d67de` 查阅；不要把旧文档当作当前部署入口。
2026-10 的 Noemate 原始派生采集固定在 `03d9ec6c`；当前保留完整统计投影、哈希和复算脚本，
不再携带 `proxy/results/` 活树副本。最终演示及必要构建底稿见[演示说明](docs/report/closeout/presentation/README.md)。
