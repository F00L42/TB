# 历史设计与材料索引

2026-10-21 按交付范围裁剪 SDK 分支。当前开发入口是包内 API、设计、示例、测试和独立 patch；
原设计与实验并未从 Git 历史删除，不需要恢复旧目录才能开发 SDK。以下固定身份可用于追溯，不能当作当前部署说明。

| 材料 | 固定来源与范围 |
| --- | --- |
| SDK 提取时的完整仓库 | `7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d`；包含旧根 AGENTS、DTB/DEER/API 转发、LightCode、代理和实验树 |
| 原 Session D 范围/交接设计 | [dtb_closeout_handoff.md](https://github.com/F00L42/TB/blob/7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d/docs/design/dtb_closeout_handoff.md)；当前 API 以本 SDK 文档为准 |
| parser fixture 提取来源 | TB `f342e127` 的 `tools/dtb/parser_partition_equivalence.py`；所需测试数据/import stub 已独立保留在本 SDK `tools/parser_fixtures.py` |
| 原 Qwen parser patch 来源 | 上述 SDK 基线提交的 `patches/vllm/v0.23.0/`；本 SDK 的两份 parser patch 独立保留，验证不读取已移除的根目录 |
| 最终 DTB 演示与构建材料 | [固定演示说明](https://github.com/F00L42/TB/blob/c716f588d14e3428d72f5e5499997888d58e5291/docs/report/closeout/presentation/README.md)；r10、必要 r5/r9、精简证据与构建源由特性分支维护 |
| DTB 技术结论 | [固定技术博客](https://github.com/F00L42/TB/blob/c716f588d14e3428d72f5e5499997888d58e5291/docs/report/closeout/dtb_project_technical_blog.md)、[完整 E2E 观测](https://github.com/F00L42/TB/blob/c716f588d14e3428d72f5e5499997888d58e5291/docs/report/closeout/noemate_e2e_20261013.md)；不构成 SDK 质量/性能证据 |

在有完整历史的 checkout 中可以只读查看，不必 checkout 大数据：

```bash
git show 7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d:AGENTS.md
git show 7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d:docs/design/dtb_closeout_handoff.md
```

需要导出某个专题时用 `git archive --output=<归档路径> <固定提交> <专题路径>`，路径和大小先核对。
浅克隆若没有这些对象，需要先补齐对应历史。SDK 的普通开发、单测与打包不依赖这些历史对象。
本次不改 Git 引用、不重写历史；源码树裁剪不减少完整 clone 的历史存储体积。
