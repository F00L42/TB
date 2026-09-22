# Think Budget：DTB / DEER

本仓库研究在保持答案质量和原生输出协议的前提下，通过控制可见思考降低端到端时延。
生产集成是 `dtb/` + 版本化 vLLM patch；DEER 和 API 转发参考实现用于历史对照。
减少响应 completion token 并不等于减少模型总工作量，隐藏 probe 和控制器成本必须单列。

## 从这里开始

| 要完成的工作 | 首读入口 | 接着查 |
| --- | --- | --- |
| 理解当前实现、修改生产代码 | [DTB 说明](dtb/README.md)、[代码导航](dtb/AGENTS.md) | `dtb/request_controller.py` 与目标模块 |
| 配置或部署 Qwen/vLLM-Ascend | [DTB 配置](dtb/README.md#配置生命周期)、[patch 操作](patches/vllm/v0.23.0/README.md) | `dtb/config/budget_config.yaml`、固定上游源码 |
| 理解设计的实现状态 | [设计索引](docs/design/README.md) | 每份文档的状态和基线，再核对源码 |
| 分析新服务器实验 | [仓库约束](AGENTS.md)、[泛化实验流程](docs/design/think_budget_generalization_plan.md) | 对应 run 的 manifest、请求明细、提取清单、RESULT |
| 采集 Agent 请求 | [观测代理](proxy/README-observation.md)、[proxy 约束](proxy/AGENTS.md) | `proxy/observation/` |
| 复算表格和图 | [报表流程](docs/report/chart/report_chart_workflow.md) | 对应 run 的可审计输入 |
| 本地验证本次静态优化 | [优化与交付记录](docs/design/dtb_static_cpu_refactor.md) | 下方统一命令 |

## 仓库边界

- `dtb/`：服务内生产实现；决策、状态机、请求所有权、协议适配和 telemetry 分层。
- `patches/vllm/v0.23.0/`：唯一可部署 patch 制品；不要从历史文档复制旧 adapter。
- `third_party/`：只读固定 Git 子模块。vLLM `v0.23.0` / vLLM-Ascend `v0.23.0rc1`。
- `deer/`：历史侵入式实现；独立约束见 [deer/AGENTS.md](deer/AGENTS.md)。
- `think_budget/`：API 转发参考实现，范围见 [说明](think_budget/README.md)。
- `proxy/`、`lightcode/`、`tools/`：采集、重放、实验和离线检查，不是生产 controller 的替代入口。
- `docs/design/` 保存设计及演进；`docs/log/perf/` 保存各次实验身份和证据；
  `docs/report/` 消费这些证据；`docs/archive/` 保留历史，不能用作当前部署指南。

## 无模型本地验证

从仓库根目录执行，建议使用独立 Python 虚拟环境。生产运行依赖由目标 vLLM-Ascend 环境提供；
本地检查不安装模型、vLLM 或 NPU 运行时。

```bash
python -m pip install -r requirements-dev.txt
git submodule update --init --recursive
python tools/dtb/check_static.py
```

检查包含固定子模块身份与只读状态、patch 可应用性、Python 编译和完整 `tests/`。
部分用例读取仓库已提交的历史 CSV/JSONL，因此必须保留这些文件。
Windows 下脚本显式关闭子进程 stdin，并为 pytest 生成独立临时目录。

需要复算局部 CPU 差异与 633 条完整路由结果时执行：

```bash
python tools/dtb/check_static.py --benchmark
```

基准结果写入 `.pytest_cache/dtb-static-cpu.json`，只说明本地 Python 操作成本。
模型质量、真实并发吞吐、NPU 时间和 E2E 收益必须按[服务器 runbook](docs/debug/deer_performance_analysis.md)
单独验收，未执行项目记为 `pending_remote`。
