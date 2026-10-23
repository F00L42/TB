# 交付结果

## 当前交付状态（2026-10-21）

按用户确认的范围，DEER 调试实现已退回历史；当前 serving patch 移除四个 DEER 请求字段、
导入、初始化与路由。DTB 算法和预算默认不变，native/请求关闭/服务关闭仍为原生单次生成。
旧代码及接口可从 `c716f588` 恢复。部署须使用[干净上游升级步骤](docs/design/dtb_system_routing_deployment.md#deer-调试接口退役后的升级)。

当前特性分支候选源码树约 **20.8 MiB / 219 个文件**，保留最终演示、构建底稿、精简证据与正式回归数据。
SDK 分支已裁剪旧算法与实验树，移出 **29,466 个跟踪文件 / 8,082,514,253 字节（约 7.53 GiB）**；
保留独立 SDK、示例、协议测试、三份 patch、验证/发行工具和固定上游身份，候选源码树为 **36 个文件 / 约 180 KiB**。
SDK 验证与统计口径见该分支根 RESULT。
2026-10-23：本轮整理在两个分支分别形成独立交付提交；最终源码身份以各分支 Git 提交为准。
Git 历史未重写，完整 clone 的历史体积不会减少；真实服务器验收状态不因发布而改变。

DEER 退役后的完整本地门禁 **PASS：567 项测试、1,980 个子测试**（Python 3.14.6 / pytest 9.1.1）；
最终文档的 284 个导航链接、22 个 fixture 哈希、20 个冻结报告哈希、固定上游、3 个 patch 和编译均通过。
路由专项从 53 增至 70 项，覆盖不安装 DEER 的初始化、旧字段无路由效果、取消/异常与 DTB 自动/fixed 接管。
最终日志为 `artifacts/delivery-audit/feature-deer-retired-verified.log`。
一次完整运行中，未改动的网关磁盘故障测试遇到 Windows `os.replace` 拒绝访问；未修改网关或放宽断言，
单项复核与全量复跑均通过，首次失败日志保留在 `feature-deer-retired-final.log`。

SDK 裁剪后的 34 项单测、观察示例、3 patch 应用/编译、7 个原生 parser 场景、8 条 serving 路由，
以及 wheel/sdist 构建和隔离安装后的 34 项测试均通过；sdist 包含新 Agent 导航。
所有以上验证均为本地无模型检查，真实服务器/质量/性能仍为 `pending_remote`。

## 2026-10-21 接手与交付整理

审计起点为 `codex/dtb-static-refactor@c716f588` 和 `codex/thinking-observer@7ccd6b99`。
本节记录整理过程；最终交付包含下述改动。先读[接手导航](docs/maintenance/README.md)与
[两分支交付评估](docs/maintenance/delivery_review.md)；新版 vLLM 的接口迁移见[升级手册](docs/maintenance/vllm_upgrade.md)。

本轮补齐分支能力、维护入口和设计阅读顺序；移出 11,476 个可从固定 Git 恢复的采集文件
（约 2.364 GiB）及无构建依赖的 r3/r8 演示稿。保留 r10、构建输入 r5/r9、精简证据、全部正式 fixtures 和 replay。
第一阶段候选源码树约 **20.8 MiB / 224 个文件**（含新文档，不含子模块、Git 历史和忽略的本地产物）。
随后已按用户确认的范围执行 DEER 退役与 SDK 实验树裁剪：DEER 调试接口不再交付；DTB 算法与预算默认不变。

初始本地门禁发现冻结证据 CSV 的行尾误判；已改为先核验 bundle 字节/hash，再仅豁免其中的 CSV。
冻结文件不改写。完整 E2E 证据 `recompute.py --verify` 在 Python 3.14.6 通过
（1,483 共同初始问题：1,475 短、8 长；114 个 native 同输入对照）；Python 3.12.14 的精确统计比较未通过，
复算应使用已验证运行时。

本轮第一阶段（DEER 退役前）的完整静态门禁 **PASS：550 项测试、1,980 个子测试**（Python 3.14.6 / pytest 9.1.1）。
固定上游身份/干净状态、3 个 patch 可应用、编译、22 个 fixture 哈希、20 个冻结报告文件哈希和
281 个本地导航链接均通过；没有执行 CPU 微基准或新的服务器请求。
测试临时目录改用 `artifacts/checks/<run>/`，避免旧 `.pytest_cache` 的 Windows 权限问题。
9 项新增回归覆盖证据破坏/缺失、非法清单和普通 CSV 不被豁免；没有为通过门禁修改冻结输入。

第一阶段 SDK 工作区写入 11 个导航/文档/示例文件，原 34 项 SDK 无模型测试和新观察窗口 smoke 通过；
该阶段 SDK 的 parser/serving/发行包验证未重跑；裁剪后的复核另行记录。两个分支的真实模型/设备/质量/性能仍为 `pending_remote`。
详细本地日志：`artifacts/delivery-audit/feature-verified.log`、`artifacts/delivery-audit/sdk-prepared/validation.json`。

## 2026-09-23 历史交付记录

分支 `codex/dtb-static-refactor`。基于 `f342e127` 完成静态优化，已合入 system 路由 `34f39983`。
清理前可恢复提交：`cb6d67dee5a65ad50959330e4c58d246ded35226`。
推送前同步合入远端最新 `32ab2268`（仅新增历史实验报告）；该报告可从该提交查阅，当前交付树沿用清理范围。
本次不改变 DTB 算法执行逻辑或默认参数；原工作区的 staged/unstaged 内容不参与交付。

### 当时交付范围

- 保留 `dtb/`、版本化 3 个 vLLM patch、只读上游子模块、DEER 对照实现。
- 删除 API 转发参考 `think_budget/`、LightCode 评测接口/关联器、旧专用代理和 writer、过程性报表/设计、历史日志/采集数据及打包 tokenizer。
- 通用代理迁为 `vllm_gateway`：HTTP 透传、并发记录、SSE/JSON/工具观测、可选参数覆盖与离线报告保留。
  新入口 `python -m vllm_gateway --config vllm_gateway/example.yaml`；旧 `proxy` 入口已移除。
- 保留 633 条路由问题、4 条误分类回归、60 条真实 parser 链与小型介入 fixtures，总约 6.9 MiB。
  原问题/text/token IDs 不改写；仅去除测试不使用的字段，流式原数据无损压缩。
  来源与 SHA 见 [fixture 清单](tests/fixtures/provenance.json)，测试不依赖旧日志目录、不因数据删除而 skip。
- 新增 [7 条合成 replay](validation/replay/README.md)、精确答案/协议/配对门和独立 phase 审计，输出统一进入 Git 忽略的 artifacts/。
- 精简 README/AGENTS/设计索引，删除已过期提案和服务器路径，统一当前源码、命令及证据边界。

该轮清理使约 7.4 GiB 历史日志/采集数据退出交付；当时主仓库跟踪文件由约 7708 MiB 减至约 8 MiB
（不含上游子模块及 Git 历史；不是 2026-10-21 新增报告后的体积）。Git 历史没有重写，完整 clone 的历史体积不会因此下降；
需要紧凑源码包可用 `git archive --format=zip HEAD -o ../TB-delivery.zip`，该包不含上游子模块，
测试/部署仍应从 Git checkout 初始化固定依赖。旧数据可按清理前 commit 恢复，不能拿旧结果冒充本次实测。

### 当时本地验证

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
后续统一了源码/文档/JSON 的 LF 行尾，并修正 fixture 来源哈希为原提交 Git blob 的哈希；
CSV 问题正文保留原始换行。该格式修正不改变 Python 逻辑，但与上述 Windows 微基准当时的文件字节哈希可能不同。

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
优化前/后代码的同参数对照另按 [runbook](docs/debug/dtb_performance_analysis.md) 执行；本分支不提供虚构服务器结论。
