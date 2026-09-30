# DTB 五页汇报

打开 [DTB_closeout_20260930_r5.pptx](DTB_closeout_20260930_r5.pptx)。

本版将第 1 页改为紧凑背景与实现框架，第 3 页改为上下等高的分段纵轴；r3 保留为前一版。

正文为背景 1 页、实现 2 页、效果 1 页、交付 1 页，没有封面、章节页或演示附录。
案例采用 Noemate 178/a1 与研发 Vanilla-06/run-08 汇总。
完整数据口径、反例与固定引用放在讲者备注、[内容稿](../outlines.md) 和 [来源索引](../sources.md)。

## 编辑

- 正文、流程节点和连接线为可编辑对象。五个图表对象均为 PowerPoint 原生图表，带内嵌数据工作簿。
- 研发分布保留 391 个点，Noemate 保留 8,277 个点，阈值分别为 850 和 750；每个图另有两个阈值线端点。
- 研发分布使用两个相接的等高绘图区：下段 0–850、上段 850–3,500，合计 380＋11 个点，保留真实 token 值。各段内线性，不跨段比较视觉距离或斜率；Noemate 仍为连续线性轴。
- 两个耗时条形图都以零为起点。百分比从完整精度计算，展示值保留两位小数。
- 字体为 Microsoft YaHei，画布 16:9。缺少该字体的环境可能发生替换和换行变化。
- 已检查导出文件的逐页渲染与图表工作簿结构，未在 Microsoft PowerPoint 应用内打开验证。

## 重建

构建入口为 [build_presentation.mjs](../sources/build_presentation.mjs)，只读取当前精简证据，不调用模型或服务器。
使用 Codex 捆绑的 Node、Python、`@oai/artifact-tool` 与 presentations 技能工具；不依赖仓库 node_modules。

在仓库根目录，用 Windows PowerShell 执行：

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" docs/report/closeout/sources/build_presentation.mjs r6
```

每次指定新的修订号，不覆盖已有 PPT。可通过 `RUNTIME_ROOT` 和 `PRESENTATIONS_SKILL` 指定不同的捆绑运行时/技能目录。
构建草稿、渲染及校验记录写入 Git 忽略的 `artifacts/closeout-build/20260930/`。
[repair_chart_markers.py](../sources/repair_chart_markers.py) 显式设置散点标记样式和两段绘图区的位置、尺寸，不改变点值或来源。

修改数据后，先按 [证据复算说明](../evidence/README.md) 重新取数。
修改正文或版式后，重新渲染并逐页检查，再使用新版本。演示稿中的历史观测不替代当前交付版本的服务器验收。
