# DTB 五页汇报

打开最新版 [DTB_closeout_20260930_r9.pptx](DTB_closeout_20260930_r9.pptx)。

2026-10-08 修订：第 4 页采用 Noemate 全部 50 对长请求回放的平均 E2E，24.87→18.51 秒，下降 25.58%。
沿用第 3 页分段轴位置、短请求空心描边点与页脚说明移入备注的修复。
以 `49ff2f8d` 中的 r5 为基础，保留手工调整的连接线与绑定；r3、r5、r8 保留为历史版本。

正文为背景 1 页、实现 2 页、效果 1 页、交付 1 页，没有封面、章节页或演示附录。
第 4 页展示 Noemate 5 个输入 × 10 对回放，以及研发 Vanilla-06/run-08 的 9 任务汇总；178/a1 退入备注与备查。
Noemate 精确均值为 24.8744579→18.51114568 秒，按 `1−ΣDTB E2E/ΣVanilla E2E` 计算降幅 25.5817%，43/50 对更快。
完整数据口径、反例与固定引用放在讲者备注、[内容稿](../outlines.md) 和 [来源索引](../sources.md)。

## 编辑

- 正文、流程节点和连接线为可编辑对象。五个图表对象均为 PowerPoint 原生图表，带内嵌数据工作簿。
- 研发分布保留 391 个点，Noemate 保留 8,277 个点，阈值分别为 850 和 750；每个图另有两个阈值线端点。
- 研发分布使用两个相接的等高绘图区：下段 0–850、上段 850–3,500，合计 380＋11 个点，保留真实 token 值。各段内线性，不跨段比较视觉距离或斜率；Noemate 仍为连续线性轴。
- 两个耗时条形图都以零为起点。百分比从完整精度计算，展示值保留两位小数。
- 字体为 Microsoft YaHei，画布 16:9。缺少该字体的环境可能发生替换和换行变化。
- 第 3 页分段轴与空心标记已在 r8 的本机 Microsoft PowerPoint 放映中检查，r9 沿用这些修复；r9 的第 4 页已在本机 PowerPoint 放映中检查。图表仍为原生可编辑对象，显示效果以 PowerPoint 为准。

## 重建

本版修订入口为 [revise_presentation.mjs](../sources/revise_presentation.mjs)，保留 Office 手工修订的对象，更新第 4 页 Noemate 耗时图及其内嵌工作簿，并沿用图表布局和页脚修复。
原始从零构建入口 [build_presentation.mjs](../sources/build_presentation.mjs) 用于重建初稿，不包含 r5 中的手工连线形状调整。
使用 Codex 捆绑的 Node、Python、`@oai/artifact-tool` 与 presentations 技能工具；不依赖仓库 node_modules。

在仓库根目录，用 Windows PowerShell 执行：

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" docs/report/closeout/sources/revise_presentation.mjs r10
```

每次指定新的修订号，不覆盖已有 PPT。默认自动查找本机最新可用的 presentations 技能目录；可通过 `RUNTIME_ROOT` 和 `PRESENTATIONS_SKILL` 显式指定捆绑运行时/技能目录。
本版构建草稿、渲染及校验记录写入 Git 忽略的 `artifacts/closeout-build/20261008/`。
[revise_presentation.py](../sources/revise_presentation.py) 固定核对 r5 哈希，修复绝对绘图区位置、为轴标签留出空间、设置空心标记并迁移页脚；第 4 页 Noemate 图按全部 50 对回放取数。
Noemate 耗时图及其内嵌工作簿同步更新，其他图表工作簿保持原样；各图表数值缓存与其工作簿一致，消除 Office 保存产生的末位浮点文本差异。
原始构建脚本使用的 [repair_chart_markers.py](../sources/repair_chart_markers.py) 同步修正了布局与标记样式。

修改数据后，先按 [证据复算说明](../evidence/README.md) 重新取数。
修改正文或版式后，重新渲染并逐页检查，再使用新版本。演示稿中的历史观测不替代当前交付版本的服务器验收。
