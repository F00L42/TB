# SDK 交付状态

## 2026-10-23 集成流程简化

日常集成为：安装 observer → 向目标 vLLM 0.23 直接 `git apply` 三份 patch → 启用并重启服务。
取消本地完整验收、`third_party/` 初始化/存在检查以及发行包验证前置。压缩算法可直接修改 observer 源码，
外部 detector factory 为可选接入方式。

README、开发导航与服务器手册已统一。可选 patch/原生源码检查不再绑定精确 HEAD 或生成器输出；
保留补丁应用、Python 编译及既有协议检查。补丁和生产逻辑本轮未改，未执行完整本地验收或服务器测试；
真实协议、质量和净 E2E 收益仍为 `pending_remote`。

针对性检查通过：50 个本地文档链接、2 个改动 Python 工具的语法检查；3 个合成 patch 检查场景
覆盖无 Git 元数据源码可用、错误补丁拒绝、无效 Python 拒绝。未读取或检查 `third_party/`。

## 历史裁剪交付记录

2026-10-21：按确认的交付范围完成 SDK 分支裁剪。基线为
`codex/thinking-observer@7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d`。
2026-10-23：整理内容随独立交付提交固定；最终源码身份以本分支 Git 提交为准，服务器验收仍为 `pending_remote`。
保留上轮补齐的根入口、SDK Agent 导航、文本/token 有界观察示例；不实现重复检测算法。

## 当前交付范围

- 根 README、RESULT、AGENTS、Git 属性/忽略规则及固定子模块身份。
- [`thinking_observer/`](thinking_observer/README.md)：独立 SDK 源码、API/设计/runbook、示例、测试、parser fixtures、三份版本 patch 与验证工具。
- `third_party/`：vLLM `v0.23.0` / Ascend `v0.23.0rc1` 的固定 gitlink；初始化后只读。
- [历史来源索引](thinking_observer/docs/history.md)：原设计、实验、最终 DTB 演示与结论的固定提交，不重复携带历史实验树。

已移出 29,466 个跟踪文件、8,082,514,253 Git blob 字节（约 7.53 GiB）：
旧 `deer/`、`dtb/`、`think_budget/`、`lightcode/`、`proxy/`、根 `patches/`、旧 `tests/`/`tools/` 和 `docs/` 实验树。
删除前逐项确认路径边界、无 junction/reparse point、相对 HEAD 无本地修改；原文件可从上述固定提交恢复。
不重写 Git 历史，因此完整 clone 的历史体积不变。正式 SDK 测试数据与原生 parser fixtures 完整保留。
当前候选源码树为 36 个文件、约 180 KiB（不含子模块、Git 历史和忽略的本地产物）。
另外清理包内 3 个可重建的旧 ignored 发行文件；新构建仅保留于 `artifacts/dist/`，未删除未归档的唯一实验结果。

新开发从 [SDK 导航](thinking_observer/AGENTS.md) 和 [观察窗口示例](thinking_observer/docs/detector_development.md)开始。
上次裁剪时 SDK 的 `src/`、`tests/`、`patches/`、`tools/` 无差异；当时仅补文档、源码包的 AGENTS 收录和仓库交付范围。
Python 3.11+，SDK 无第三方运行时依赖。DTB 与 SDK 的 serving patch 仍是独立方案，不直接叠加。

## 裁剪后本地验证

Windows / CPython 3.12.13：

| 检查 | 结果 |
| --- | --- |
| SDK 协议单测 | 34 项通过（1.711 秒） |
| 观察窗口合成示例 | PASS |
| 三份 patch 组合应用/编译 | PASS，5 个上游文件；固定源码未修改 |
| 原生 parser / patched serving | 7 组 / 8 条通过；数据和引擎为 stub |
| wheel / sdist 构建 | PASS；sdist 收录 Agent 导航、文档、示例、测试与补丁 |
| wheel 隔离安装 | 无依赖安装后，仓库外 34 项测试通过（1.725 秒） |
| 导航与源码包一致性 | 源码 50 个本地链接、sdist 24 个本地链接通过；sdist 所有交付源文件逐字节匹配当前树 |

这些是裁剪时的历史验证结果；可选命令和证据范围见[验证说明](thinking_observer/docs/validation.md)，不再作为日常集成前置。
本机 SDK 子模块未初始化，原生源码检查使用主特性工作区同 SHA 的只读 vLLM checkout；未修改上游。
发行产物在本工作区 `artifacts/dist/`，不进 Git。详细恢复/验证清单保存在主工作区
`artifacts/delivery-audit/sdk-trim/`；交付的历史恢复不依赖这些本地日志。

真实 tokenizer、EngineCore abort/取消、HTTP/SSE、Ascend、多 DP/LoRA/trace 实际路由、质量和净 E2E
收益仍为 `pending_remote`。不能沿用 DTB 实验结果作为 SDK 效果结论；部署按 [服务器手册](thinking_observer/docs/server_runbook.md)。
