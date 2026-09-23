# 交付结果

分支 `codex/dtb-static-refactor`。基于 `f342e127` 完成静态优化，已合入 system 路由 `34f39983`。
清理前可恢复提交：`cb6d67dee5a65ad50959330e4c58d246ded35226`。
推送前同步合入远端最新 `32ab2268`（仅新增历史实验报告）；该报告可从该提交查阅，当前交付树沿用清理范围。
本次不改变 DTB 算法执行逻辑或默认参数；原工作区的 staged/unstaged 内容不参与交付。

## 交付范围

- 保留 `dtb/`、版本化 3 个 vLLM patch、只读上游子模块、DEER 对照实现。
- 删除 API 转发参考 `think_budget/`、LightCode 评测接口/关联器、旧专用代理和 writer、过程性报表/设计、历史日志/采集数据及打包 tokenizer。
- 通用代理迁为 `vllm_gateway`：HTTP 透传、并发记录、SSE/JSON/工具观测、可选参数覆盖与离线报告保留。
  新入口 `python -m vllm_gateway --config vllm_gateway/example.yaml`；旧 `proxy` 入口已移除。
- 保留 633 条路由问题、4 条误分类回归、60 条真实 parser 链与小型介入 fixtures，总约 6.9 MiB。
  原问题/text/token IDs 不改写；仅去除测试不使用的字段，流式原数据无损压缩。
  来源与 SHA 见 [fixture 清单](tests/fixtures/provenance.json)，测试不依赖旧日志目录、不因数据删除而 skip。
- 新增 [7 条合成 replay](validation/replay/README.md)、精确答案/协议/配对门和独立 phase 审计，输出统一进入 Git 忽略的 artifacts/。
- 精简 README/AGENTS/设计索引，删除已过期提案和服务器路径，统一当前源码、命令及证据边界。

当前树中约 7.4 GiB 历史日志/采集数据已退出交付；主仓库跟踪文件由约 7708 MiB 减至约 8 MiB
（不含上游子模块及 Git 历史）。Git 历史没有重写，完整 clone 的历史体积不会因此下降；
需要紧凑源码包可用 `git archive --format=zip HEAD -o ../TB-delivery.zip`，该包不含上游子模块，
测试/部署仍应从 Git checkout 初始化固定依赖。旧数据可按清理前 commit 恢复，不能拿旧结果冒充本次实测。

## 本地验证

```bash
python -m pip install -r requirements-dev.txt
git submodule update --init --recursive
python tools/dtb/check_static.py --benchmark
```

2026-09-23 本地通过：**541 项测试、1966 个子测试**；固定依赖干净、3 个 patch 可应用、
Python 编译、fixture 哈希、全部导航链接及 CPU 微基准均通过。7 样本完整矩阵的 HTTP mock、
错误/截断/错误工具参数/缺失配对/缺账本负对照也在测试中，未向真实模型发请求。
静态优化包括关键词有界缓存/单次 casefold/饱和扫描、非流式一次 join 和 final 方法职责提取；
完整 633 条路由字典等价性检查保留。局部 CPU 基准只说明本机操作成本，不代表真实并发吞吐或模型 E2E。
被删除特性的测试（旧 writer/LightCode、历史图表）随特性移除；生产、通用网关及 parser 回归保留。
G0 的旧 14 请求归档复制测试改为独立的 7 请求精确提取/顺序/哈希回归，不再依赖完整旧采集。

可溯源的本地微基准见 [static_cpu_20260923.json](validation/static_cpu_20260923.json)：
legacy 路由 1k/16k/64k 字符的中位操作时间分别减少约 42%/35%/35%；
32 个预入队响应、每个 2048 chunk 的合成聚合批次约 321→143 ms。
环境是 Windows/Python 3.14.7，5×5 次局部重复，包含 fixture 构造/断言；并未模拟引擎调度。
记录的 candidate_dirty=true 表示在本次清理提交前执行；候选 SHA 按本地源码字节/换行计算，
绑定本次实现，不能把该数字标为服务器收益。

## 服务器最小验收：pending_remote

先按 [部署手册](docs/design/dtb_system_routing_deployment.md) 部署成套 controller/patch 和 Noemate 750 候选，
按 [replay 手册](validation/replay/README.md) 填写实际 server manifest。模型名、身份字段不可直接照抄占位值。

```bash
# 无请求发送的参数预览
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL --dry-run
# 7 条数据 × auto/cap750/probe128 × SSE/JSON × Vanilla/DTB
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL \
  --server-manifest artifacts/server-off.json --output artifacts/smoke-off
# 独立 phase 服务（已重启并更新 manifest）
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL \
  --server-manifest artifacts/server-phase.json --output artifacts/smoke-phase
python tools/dtb/audit_release_phase.py artifacts/smoke-phase /path/to/phase-server.log --require-intervention
# 切回 off、重启后检查重复配对样本的收益
python tools/dtb/validate_release.py --model YOUR_SERVED_MODEL \
  --server-manifest artifacts/server-off.json --profiles cap750 --protocol stream \
  --repetitions 10 --warmup 1 --concurrency 4 --require-gain --output artifacts/perf-c4
```

再以 concurrency=1 新目录重复性能命令。默认直连 localhost:9001；可加 --url 修改端点。
错误、截断、质量退化、缺失/重复配对返回 1；样本收益不足或 phase 介入未覆盖返回 3，不能写成通过。
功能正确和性能改善由两个门分别判定，不能预先保证服务器提速。合成小集不替代目标工作负载质量验收。
真实并发/E2E、NPU、实际 parser 绑定、目标数据质量与完整 Agent 成功率都仍是 pending_remote。
优化前/后代码的同参数对照另按 [runbook](docs/debug/deer_performance_analysis.md) 执行；本分支不提供虚构服务器结论。
