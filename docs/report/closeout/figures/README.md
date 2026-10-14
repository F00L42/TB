# 项目技术博客配图

本目录为[DTB 项目技术博客](../dtb_project_technical_blog.md)保存演示稿原图的导出版本，源文件为[演示稿 r10](../presentation/DTB_closeout_20260930_r10.pptx)。

源 PPTX SHA256：`cd8f1e6982090e0bdfb750ecf12e26dcbf8be346deab533146d181acad6b249c`。

| 图片 | 演示来源 | 内容与统计边界 |
| --- | --- | --- |
| [Noemate 思考分布](noemate_reasoning_distribution.png) | 第 4 页 | 8,277 次历史有效 main，750 阈值，连续线性纵轴 |
| [研发思考分布](development_reasoning_distribution.png) | 第 3 页 | 391 次有效调用，含标题；两段纵轴等高，0–850 / 850–3500 |
| [DTB 整体架构](dtb_architecture.png) | 第 1 页 | 职责概览，具体流式裁决和 child 生命周期由正文展开 |
| [原生观察管道](native_observation_pipeline.png) | 第 3 页 | 逻辑数据流与控制回路，cue 表达强制收束路径 |
| [协议修复时间线](protocol_fix_timeline.png) | 第 2 页 | 四轮定向回放，各自的分母和目标故障 |
| [Noemate 回放耗时](noemate_replay_latency.png) | 第 4 页 | 五输入各十对，平均 HTTP E2E 24.87→18.51 秒，−25.58% |
| [研发累计 LLM 耗时](development_llm_e2e.png) | 第 4 页 | 九个主任务，累计 LLM E2E 2,902.45→2,566.70 秒，−11.57% |

导出直接选择原稿 OOXML 对象，保留文本、字体、原生图表数据、工作簿、图表布局、连接线旋转与几何参数。仅在 ignored 临时副本中平移对象并设置对应区域的画布，以 2 倍尺度渲染为 PNG；源演示稿不修改，不重新绘图或运行模型。

Noemate 回放耗时图未选入 r10 新增的完整运行确认语句，避免将旧 50 对与新 11,441 请求的证据混在同一张图中。

每张图的源页、shape ID、原稿区域、图片 SHA256，以及全部图表/工作簿的原始哈希记录于[source_manifest.json](source_manifest.json)。统计来源仍以[证据目录](../evidence/README.md)和[引用源索引](../sources.md)为准。

## 重新导出

从仓库根目录用 Codex 捆绑 Node 和 `@oai/artifact-tool` 执行：

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" docs/report/closeout/sources/export_blog_figures.mjs
```

可通过 `RUNTIME_ROOT` 指定同类捆绑运行时目录，或将第一个命令行参数设为另一个图片输出目录。源 PPTX 哈希不符时脚本立即停止，必须先核对新版本的图表对象与证据口径。临时导出副本写入 `artifacts/closeout-blog-figures/20261014/`。

渲染在独立子进程中执行。捆绑渲染器存在图片全部写出后非零退出的现象，脚本分别记录渲染退出状态与图片验收：只有本次唯一 attempt 的 7 张图片、哈希、字节数、尺寸及源稿身份全部符合时才完成导出；旧 manifest 或缺失图片不能通过。原始输出与退出状态保存在临时目录的 `renderer.log` 和 `render-receipt.json`。
