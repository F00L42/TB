# 汇报内容审阅的精简证据

本目录保存 [五页内容稿](../outlines.md) 的精简证据；[演示稿及重建说明](../presentation/README.md) 单独维护。历史性能数据来自
`debug_agent` 的固定提交 `32ab2268335e00d79e0aeda4395c6ecdc06d81eb`。
当前交付源码和独立 SDK 使用另外两个固定提交，具体身份见 [来源索引](../sources.md)。

## 按用途读取

| 文件 | 用途 | 来源定位 |
| --- | --- | --- |
| [display_data.json](display_data.json) | 分布、Noemate 五组重复回放、研发汇总及口径 | S01、S04、S10–S12 |
| [noemate_distribution.csv](noemate_distribution.csv) | 8,277 条分布点，完整用于绘图 | S01 对照 S02 原 features；S03 角色规则 |
| [alarm_reference.csv](alarm_reference.csv) | 12 条告警真值，含原 message 序号 | S05，对照 S09 原请求 |
| [noemate_cases.json](noemate_cases.json) | 主例 178/a1；备查失败 178/a3、有限成功 399/a10；原 reasoning/final 与参数 | S06–S09，N178S/N399S |
| [noemate_long_pairs.json](noemate_long_pairs.json) | 五个长案例、各 10 对的完整配对值 | S04，对照五份 N*S 原 summary |
| [noemate_long_summary.csv](noemate_long_summary.csv) | 上述 50 对按 case 汇总，保留 length 次数 | 同上，仅作备查，不增加演示页 |
| [noemate_phase.json](noemate_phase.json) | 独立 phase 399/a2 的实际介入轨迹 | S19，不与 off 性能样例拼接 |
| [development_distribution.csv](development_distribution.csv) | 第 3 页的 391 条研发全链路分布点，含标题 | S10 的 vanilla_chronological_requests，逐 ID 对照 S11 |
| [development_tasks.csv](development_tasks.csv) | Vanilla-06/run-08，各 9 任务共 18 行 | S10，对照 S11 的 742 请求 |
| [development_case37.json](development_case37.json) | 研发报告阶段的原文、工具调用及 phase | S10、S20 原日志第 20 行 |
| [reference_slide_text.json](reference_slide_text.json) | 用户参考 PPT 第 1 页的文字 | R01，仅作方案参考 |
| [source_manifest.json](source_manifest.json) | 原始 Git blob 与本目录生成文件的 SHA-256 | 精确字节，不经过 checkout 换行转换 |

CSV 为 UTF-8、LF，序号和请求 ID 保留。`above_750`、`above_850` 均是严格大于各自阈值。
第 3 页用研发分布（850），第 4 页用 Noemate 分布（750），不混用样本、坐标单位和统计口径。
reasoning token 沿用历史本地 tokenizer 重编码计数，**本次未重新分词**。
`alarm_review_all_attempts` 是历史规则核对和阅读结论，不是独立业务 Judge。
`pass_for_report` 沿用已披露的任务审核政策，含 Vanilla case35 原始 36/36，未重新执行业务测试。

## 复算

在仓库根目录执行：

```bash
python -X utf8 docs/report/closeout/sources/extract_evidence.py
```

只使用 Python 标准库和 Git。三个固定提交必须存在于本地 Git 对象库，用户参考 PPT 也必须存在。
浅克隆应先补齐这三个提交所在的历史；脚本缺来源会报错，不静默 skip，也不自动联网取数据。
脚本从自己的路径定位仓库，可从其他工作目录启动。

执行会重建本目录的 12 个生成文件，不修改 README、内容稿、用户参考文件、其他分支或工作区。
无需 checkout 历史分支，无需 tokenizer、模型、第三方包或仓库外备份。
没有恢复 `think_budget`、LightCode 实现或实验大日志。

本轮实际检查：

- 12,214 原始特征记录的 ID 唯一性；8,277 个有效主 Agent 与历史分布逐 ID 一致，长度合计、阈值计数和分位数复算一致。
- 五个 case 的 100 条 summary 与 50 对配对值一致，manifest 均固定输入、10 次重复、交替发送。
- 选中样例的 reasoning/final、耗时、finish 与原 summary 一致；12 条告警字段与原请求消息一致。
- 两组 742 请求可按任务、variant 求和对账；9 对主任务输入 hash 相同，后续调用轨迹不强行配对。
- 研发 391 个分布点与请求账本逐 ID 对账；11 条 >850 长尾中 9 条是标题，长度合计 20,263 token。
- case37 摘句按字符偏移与原始日志一致。

这些是**已有证据的本地校验**，不代表重新跑过服务器实验、不新增整体质量结论，也不替代业务审核。
历史源文件即使有 CRLF，manifest 记录的是 Git 原始 blob 哈希；当前生成文本统一 LF。

## 使用边界

主演示只采用内容稿选定的样例。其他长样本和失败记录用于审核选择是否偏颇，不自动追加到演示页。
不要因为 `finish_reason=stop`、结构检查无错误或所有重复更快，就把业务语义标为正确。
所有历史收益与当前重构后的服务器验收分开；后者仍为 `pending_remote`。
