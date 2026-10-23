# 两分支交付梳理（2026-10-21）

这是接手视角的源码/文档审计，不包含新服务器实验。审计起点：
DTB `c716f588d14e3428d72f5e5499997888d58e5291`，SDK `7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d`。
2026-10-23 按用户确认发布：本轮整理在两个分支分别形成独立交付提交，不合并两套实现或重写历史。
上面的 SHA 是审计/恢复起点；最终源码身份以各交付分支的 Git 提交为准。

## 结论

审计时 DTB 已有模块导航和本地门禁，但入口缺少升级步骤，旧体积/清理描述已过期。
SDK 包已有 API、生命周期和测试，分支根目录却缺 README/RESULT，根 AGENTS 指向旧算法。
本轮先补齐导航、示例与验证入口；随后用户确认 DEER 是调试遗留，无需保留接口，并授权裁剪 SDK 实验树。
交付范围现已确定为两个独立分支，DEER 和旧实验实现保存在固定 Git 历史。

| 接手问题 | 原状态与本轮补齐 | 剩余边界 |
| --- | --- | --- |
| 找到功能与实现 | DTB 原有符号→测试表；补分支表和接手路径。SDK 补根入口、局部 AGENTS 和开发指南 | 两分支各自维护验证和部署身份 |
| vLLM 换版本后出方案 | 补上游文件/签名→包内接口→patch→测试的迁移手册 | 未指定新版本，未生成或验证新版 patch |
| 从 SDK 获取文本/token | 补 `observe(delta)` 有界窗口示例与运行命令，说明 token 偏移与 text 的独立边界 | 尚未实现重复检测算法，也未验收实模型检测效果 |
| 复核质量/收益 | 保留最终演示、博客、全部精简统计投影和来源 | 历史模型 HTTP E2E 观测不等于当前版本独立因果收益或质量非劣 |
| 退役 deer | 移除调试目录及 serving patch 的字段、导入、初始化、路由 | 服务器需重新安装成套 patch 并重启 |

## 已确定的清理

- 移出 `proxy/results/noemate/agent_20261012T030114Z_daf6d49b/`：11,476 个跟踪文件，
  2,538,767,317 Git blob 字节（约 2.364 GiB）。清理前工作树与 HEAD 相同；该目录在 HEAD
  和固定源 `03d9ec6c` 的 tree 都是 `d9e1cf1e85b0128dc6d71709f38266c26f43f8fc`。
- 精简证据包保留 12,214 + 11,441 条完整统计投影及来源/hash；`recompute.py` 不读上面的活树目录，
  `extract_features.py` 读固定 Git blob。清理不会把全量统计变成抽样。
- 演示保留 r10 及必需构建输入 r5/r9；移出无依赖 r3/r8，恢复点为审计起点的 presentation 目录。
- 保留全部正式 fixtures、7 条合成 replay、失败样例和构建源；“测试数据”不能一概删除。
- `.gitignore` 防止 `proxy/results/` 再次意外进入交付；新输出仍使用 `artifacts/`。
- 原设计正文不删，统一阅读顺序、职责与版本解释入口。冻结报告内容、哈希和历史判定不改。

本地旧 `artifacts/` 含额外分析脚本和验证回执，未逐项证明可替代的部分保留。
第一阶段候选源码树约 20.8 MiB / 224 文件；DEER 退役后的统计见根 RESULT。该口径包含最终报告与冻结统计投影，不含子模块、Git 历史和忽略的本地产物。
`build/dtb-agent-report/node_modules` 是指向共享运行时的 junction，不递归删除整个 build。
删除源码树副本不会回收 Git 历史体积，也不会自动清理另一个分支。

## DEER 退役边界

原 `dtb/` 就不依赖 DEER；耦合在 serving patch。现一并移除 `deer/` 和
[dtb_serving.patch](../../patches/vllm/v0.23.0/dtb_serving.patch) 的四个 DEER 请求字段、导入、
controller 初始化和路由。保留 DTB/native 的开关、场景路由及 fixed 优先级，不把旧 DEER 字段映射为 DTB。

原生请求模型允许未知扩展字段；旧 `enable_deer` 仍可能被接受，但不再触发控制器，不能据 HTTP 200
认定接口有效。客户端应删除调试字段。旧源码和接口文档均可从 `c716f588` 的 `deer/` 及 patch 恢复。
部署和旧版本回滚见[退役升级步骤](../design/dtb_system_routing_deployment.md#deer-调试接口退役后的升级)。

## 已确定的双分支交付方案

| 分支 | 保留内容 | 历史来源 |
| --- | --- | --- |
| `codex/dtb-static-refactor` | DTB、当前三份 patch、网关、正式回归/验收工具、最终演示/结论/精简证据、维护文档 | `c716f588` 保留 DEER 与本轮清理前材料；采集来源另按证据索引 |
| `codex/thinking-observer` | 独立 SDK 全包、示例、协议 fixtures、验证/打包工具、三份独立 patch、固定上游身份、根导航与历史索引 | `7ccd6b99` 保留原旧算法和完整实验树；最终 DTB 演示由特性分支维护 |

SDK 原树约 7,708 MiB，核心 SDK 仅 26 文件/约 0.15 MiB（补文档前）。已移出 29,466 个跟踪文件、
8,082,514,253 字节（约 7.53 GiB）；删除前确认目标无本地差异并记录源 commit、路径、Git blob 和字节数。
上轮文档与窗口示例、SDK 协议 fixtures、验证工具完整保留；根 AGENTS 收敛为 SDK 规则，旧规则由历史索引定位。
裁剪后 34 单测、示例、3 patch、7 parser/8 serving 以及构建/独立安装均通过；sdist 新增打包 AGENTS.md。
裁剪后的候选源码树为 36 个文件 / 约 180 KiB（不含子模块、Git 历史及忽略产物），验证详情见 SDK 分支根 RESULT。
真实服务仍为 `pending_remote`。

两个分支不重写历史，不 rebase 或自动合并；SDK 不引入 DTB 算法。两套 patch 都改 serving，当前不能直接叠加。
后续若需要同进程组合，应另定义路由/优先级并做组合验收；本次保持各自可独立部署的边界。

当前验证结果以根 [RESULT](../../RESULT.md) 为准。发布时在两个分支分别固定源码/patch/配置身份，
并保留未执行的目标服务器项目为 `pending_remote`。
