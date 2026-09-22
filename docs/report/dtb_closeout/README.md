# Session B：分布、配对与轨迹证据

本次交付基于已有捕获和回放，完全离线。原始分析快照和交接清单已在 `f342e127` 提交，原始正式off结果来自 `884a00a54faf65fa5daca191943677fd2835e8be`。本目录只承接Session B；生产路由改动属于Session A，实际任务端到端结果由Session C回填。

## 直接使用

- [可编辑单页PPT](evidence/figures/vanilla_distribution.pptx) 与 [预览](evidence/figures/vanilla_distribution.png)：参考旧850图的时间散点、配色和计数，改用当前Noemate的750介入参考值，删除右侧长尾拆解。单一线性纵轴覆盖0–2000，无断轴。所有8,277个点和阈值线均为原生图表，内嵌XLSX可编辑，文字为文本框。
- [机器证据索引](evidence/index.json)：Session E按claim_id引用，保留分母、选样、语义状态和限制。
- [完整分类成功卡178/a1](evidence/cards/l178_a1.md)：12条记录的ID、级别、名称、时间、关联网元逐项核对，final自包含。
- [有限聚合成功卡399/a10](evidence/cards/l399_a10.md)：4个返回值的总和/均值正确，final披露源日期；只能证明所给数据聚合正确，不能称“今日查询完整成功”。
- [失败卡178/a3](evidence/cards/l178_a3.md)：保留严重组遗漏，与成功例并列。
- [319完整审阅包](evidence/review/REVIEW.md)：`pending_remote`，保留完整请求和10个DTB输出，不提前认定清单正确。

本次没有把“2张选例卡”包装成“2种业务完整成功”。178/a1可作完整分类成功例，399/a10可作明确限定范围的成功例。若最终汇报要求至少2–3种业务完整通过，仍需补319独立审阅或其他样例，不能通过放宽日期/事实检查凑数。

## 图表与分母

| 图 | 分母与用途 | 源表 |
|---|---|---|
| [时间分布](evidence/figures/vanilla_arrival.png) | 主Agent8,306次中的有效8,277次 | [逐请求](evidence/tables/vanilla_distribution.csv) |
| [用途分层](evidence/figures/traffic_by_role.png) | 全部12,214次，辅助行只画到达，不填思考零值 | [全部请求](evidence/tables/traffic_all.csv) |
| [直方图和ECDF](evidence/figures/length_ecdf_hist.png) | 同一8,277子集，保留输出上限约束 | [阈值表](evidence/tables/threshold_scan.csv) |
| [冻结长集配对](evidence/figures/frozen_long_pairs.png) | 5输入×10对，全部保留 | [290对明细](evidence/tables/frozen_pairs.csv) |
| [短集敏感性](evidence/figures/short_sensitivity.png) | greedy80对、原采样80对；同输出37对单列 | [分组统计](evidence/tables/frozen_case_summary.csv) |
| [真实phase](evidence/figures/phase_mechanism.png) | phase 399/a2，独立机制例 | [phase账本](evidence/tables/phase_ledger.csv) |

六组科学图均有同名SVG和PDF，SVG保留文本；[机制概念图](evidence/figures/mechanism.mmd)可编辑，probe分支明确标为概念，不附伪实测时间。

**实测分布：** >750有415条（5.01%），≤750有7,862条（94.99%），其中恰好750有1条。P50/P95/P99=207/753/1,002，最大1,841。分位数沿用原报告nearest-rank。415条的全部思考量占16.34%，阈值以上文本只占2.93%；两者都不是可安全删除量或净时延收益。原始采集未运行DTB对照，也不能从分布推导实际旁路率。

**排除和身份：** 主Agent排除29次（12个length、16个reasoning不可观察、1个解析异常）；推荐问题3,897次、对话压缩11次不混入长度分布。非流式历史解析异常不能当作模型失败，也不能把未观测思考填零。Vanilla沿用上传baseline标签，缺服务端实际原生开关/权重SHA的独立证明。8,294/8,306主请求max_tokens=2048，分布受总输出额度约束。

**实测配对：** 正式off长集319的10/10对更快，E2E均值40.662→22.476秒，合计快44.73%，正文长度也变化。全部长集50对只是冻结集合，不是全流量。短集每输入×条件20对，全样本E2E非劣未证明；37个同输出对约+0.10ms只是事后敏感性。A/A80对保存在源表，且分块执行，不能拿来精确扣除漂移。所有慢请求、空final、length和工具动作变化均保留。

**phase边界：** 399/a2在服务计数753触发，abort=1，drain尾部0，final168，无probe。原生观察/决策/final的wall分别9.806070/0.007507/6.697425秒，客户端E2E16.576秒。图中各phase独立画耗时，不重建逐token时间轴；abort/drain/handoff诊断字段可能嵌套，不再相加。`model_work_complete=false`，不是完整引擎工作量。该请求与off 399/a10不是同一请求。

## 本轮新增语义发现

[语义核对表](evidence/semantic_review.json)区分字面覆盖和完整回答：

- 178/a1：12条记录全字段、分组和final自包含通过。
- 178/a2、a5：分类表完整，但附加建议将输入“重要告警8小时”写成“次要告警8小时”，因此不作为整篇正确例。
- 178/a6：即使最终表含12条，也夹带草稿和字面`</think>`，还存在上述级别错误；不能视为成功。原自动协议检查未覆盖这项，不能反推raw parser原因。
- 178/a3、a4、a7–a10：final缺严重组4条。
- 399/a10：Decimal算术和4条时间/值核对通过，源日期是2026-07-30，调用在9月，用户问“今日”。明确保留此边界，不将它判为完整任务成功。
- 319：性能成立于冻结输入，语义仍待独立审阅。完整资料足以交给服务器审阅，不要求重新跑全池或新增cue实验。

审阅是本地源码/原始输入对照和确定规则，不是独立业务Judge，也没有执行参考答案评测。本轮没有得出总体质量非劣、服务器发布验收或Agent任务成功率结论。

## 复现与验证

从仓库根目录执行：

```powershell
python -X utf8 docs/report/dtb_closeout/sources/collect_evidence.py
uv run --no-project --with matplotlib python docs/report/dtb_closeout/sources/plot_evidence.py
```

数据脚本需要`tokenizers`，仅加载本地tokenizer，不访问模型服务。绘图需要matplotlib/numpy和Microsoft YaHei或Noto Sans CJK SC字体。PPT构建用Codex bundled Node与artifact-tool，设置`RUNTIME_ROOT`、`PRESENTATIONS_SKILL`可更换安装路径：

```powershell
& 'C:/Users/F-O-O/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' docs/report/dtb_closeout/sources/build_distribution.mjs
python -X utf8 docs/report/dtb_closeout/sources/verify_evidence.py
```

数字仅由JSON/CSV生成，PPT不维护第二套数值。PPT横轴小时数舍入至8位小数以匹配Excel精度，完整UTC时间仍保存在CSV。

PPT脚本默认仅导出并验证PPTX，退出码0。追加`--render-preview`可更新最终PPT的PNG。本机bundled PNG渲染库在成功写完图片后发生原生退出异常`0xc0000409`，因此预览单列为可选操作；已交付PNG完成了视觉检查，PPTX和独立数据验证通过。这个异常不作为服务器或PowerPoint运行结果。

验证范围：本轮核对63个输入文件SHA、17个测量目录manifest/summary、290对配对和同输出标记；引用此前已核验的原始SSE字节，未重复运行模型或重新提取缺失archive。对卡片思考重新分词并对账，38条phase事件与serverlog逐条相同。PPT通过包结构、原生图表/工作簿引用、字体/版式和重新导入检查，并检查最终渲染。没有在Microsoft PowerPoint实机打开或编辑验证。

输入SHA清单为 [source_manifest.json](evidence/source_manifest.json)，含精确本机字节SHA与规范化LF SHA，便于跨Git换行设置复核。`.build/`只存私有构建及检查记录，不提交。

## Session E接收边界

优先引用B01分布、B02冻结性能、B03短集边界、B04真实机制及两张范围明确的选例卡。B05/319语义与Session C端到端任务结果保持pending_remote。生产自动路由不从这些fixed回放推断。旧RESULT保留原轮历史，本目录记录本次更严格的语义复核，debug总变更记录由成果合并时集中追加，避免与其他session同时编辑。
