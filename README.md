# Think Budget：DTB / DEER

DTB 在 vLLM 服务内控制思考预算，目标是在保持答案质量和原生 parser 协议的前提下降低端到端时延。
生产入口是 `dtb/` 与 `patches/vllm/v0.23.0/`。本次交付状态、清理范围、服务器验收命令见 [RESULT.md](RESULT.md)。

| 任务 | 从哪里开始 |
| --- | --- |
| 修改生产代码 | [DTB 导航与所有权](dtb/AGENTS.md)、[配置说明](dtb/README.md) |
| 部署、system 路由与回滚 | [路由部署手册](docs/design/dtb_system_routing_deployment.md)、[patch 说明](patches/vllm/v0.23.0/README.md) |
| 理解实现及设计依据 | [当前设计索引](docs/design/README.md) |
| 通用 vLLM 透传、记录、离线阅读 | [vllm_gateway](vllm_gateway/README.md) |
| 服务器流式/非流式、质量和配对耗时检查 | [7 条 replay 数据与矩阵](validation/replay/README.md) |
| 无模型回归与数据来源 | [fixtures](tests/fixtures/README.md)、[测试门禁](tools/dtb/check_static.py) |
| 正式性能实验 | [实验与证据契约](docs/design/think_budget_generalization_plan.md)、[服务器 runbook](docs/debug/deer_performance_analysis.md) |

## 本地验证

```bash
python -m pip install -r requirements-dev.txt
git submodule update --init --recursive
python tools/dtb/check_static.py --benchmark
```

需要 Python 3.10+。检查包括固定上游身份/只读状态、3 个 patch 可应用性、编译、文档链接、
全部无模型测试；可选微基准比较 `f342e127` 旧实现与当前实现并完整回放 633 条路由。
微基准需要 Git 中存在该历史 commit（浅克隆需补取），结果写入 `.pytest_cache/`。
本机没有目标推理设备，服务器 E2E、质量和真实并发吞吐仍为 `pending_remote`。

## 交付边界

- `dtb/`：生产 controller；`deer/`：保留的历史服务内对照，约束见 [deer/AGENTS.md](deer/AGENTS.md)。
- `third_party/`：固定只读子模块，vLLM `v0.23.0` / vLLM-Ascend `v0.23.0rc1`。
- `vllm_gateway/`：通用 HTTP 透传和采集，不执行 DTB 算法。旧 `proxy` Python 入口已移除。
- `tools/`：replay、phase 提取、parser/介入检查及静态基准；[工具索引](tools/README.md)。
- `validation/replay/`：可直接提交给模型的合成验证集；`tests/fixtures/`：独立、可溯源的本地回归输入。
- `artifacts/`：本地/服务器运行输出，Git 忽略。API 转发 `think_budget/`、LightCode 接口和历史大日志已删除。

旧实验及被移除的文档仍可从清理前提交 `cb6d67de` 查阅；不要把旧文档当作当前部署入口。
