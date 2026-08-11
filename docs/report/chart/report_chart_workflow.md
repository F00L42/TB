# 测试结果汇报图表工作流

本目录把一组基线与候选实验的请求级 CSV 转换为可审计的 ALLin1 表、总览图和逐场景图。
当前示例包括 `run_04_fixed_v1` 和 `run_07` 相对同一份 `run_04` Vanilla 的结果；统计口径
与图表选择依据见 `fixed_v1_report_chart_workflow_design.md`。

## 1. 输出契约

每个版本写入 `generated/<comparison_id>/<revision>/`，旧版本不会被覆盖：

```text
allin1.csv                         # 机器可读汇总
allin1.md                          # 文档内可直接引用的汇总
allin1.xlsx                        # 公式驱动的格式化工作簿
data/allin1_workbook_source.json   # 工作簿唯一规范化数据源
figures/overview/                  # 3 张总览图，SVG + PNG
figures/scenes/                    # 每个场景 1 张详情图，SVG + PNG
figures/scenes/index.md            # 全量场景索引
report_manifest.json               # 输入/输出哈希、公式、版本和实际指标
```

`overview` 与 `scenes` 是图的两个固定层级；不筛选“主场景”。图统一使用灰蓝主色、低饱和
绿/红表达正负变化，背景和网格保持低对比度。SVG 带 `<title>`/`<desc>`，PNG 用于预览。

## 2. 数据要求与口径

配置文件声明基线/候选目录、文件名前缀、13 个场景的显示名和 slug，以及必要的回归断言。
每个 run 需包含 `test_result/_SUMMARY.csv` 和逐场景 CSV。生成器会：

1. 去掉 variant 前缀与末尾时间戳，唯一匹配场景；
2. 按“规范化场景名 + `QA_ID`”严格配对，拒绝缺失或重复；
3. 用逐请求数据复算行数、平均 E2E、平均 Token、Judge 正确数，并与 `_SUMMARY.csv` 对账；
4. 从同一 `PairRecord`/`SceneSummary` 写表和绘图，避免图表口径漂移；
5. 用配置内 `expected` 指标作为已知实验的回归门。

若自动 Judge 无效但已有人工复核，可在配置的 `judge_overrides` 中按 variant、场景和
`QA_ID` 声明布尔裁决，并用 `additional_inputs` 把裁决来源文档纳入 manifest 哈希。覆盖项必须
唯一且实际命中；未使用的覆盖会让流程失败，避免无声改写质量结果。

正向定义如下：

```text
E2E gain (%)       = mean(Vanilla E2E - candidate E2E) / mean(Vanilla E2E)
Token saving (%)   = mean(Vanilla visible Token - candidate visible Token)
                     / mean(Vanilla visible Token)
accuracy delta pp  = 100 × (candidate Judge accuracy - Vanilla Judge accuracy)
overview Judge Δ   = candidate correct count - Vanilla correct count
```

“可见 Token”只读取客户端结果中的 completion `Token数`，不代表模型总工作量，也不包含隐藏
confidence probe。`MACRO` 是场景等权；`MICRO` 是请求等权，两者不得互换。ALLin1 保留
Judge 正确率和 pp 便于审计，但 `01_overview_matrix` 只显示净正确条目绝对数，避免小样本场景
因一题变化呈现夸张的百分比；该指标在 MACRO 行显示为“不适用”，MICRO 行显示全量净变化。

## 3. 生成新版本

先安装 Python 绘图依赖：

```powershell
python -m pip install -r docs/report/chart/requirements-report.txt
```

生成表格数据和全部静态图；`--revision` 必须是尚不存在的版本名：

```powershell
python docs/report/chart/generate_report.py `
  --config docs/report/chart/configs/run_07.json `
  --profile all `
  --revision v1
```

`--profile overview` 只生成 3 张总览图，`--profile scenes` 只生成逐场景图，`all` 生成两层。
三种 profile 都会生成 CSV、Markdown、工作簿数据源和 manifest。

格式化 XLSX 使用 Node.js 与 `@oai/artifact-tool`。在已提供该包的 Codex 文档运行时执行：

```powershell
node docs/report/chart/build_allin1_workbook.mjs `
  docs/report/chart/generated/run_07_vs_vanilla/v1/data/allin1_workbook_source.json `
  docs/report/chart/generated/run_07_vs_vanilla/v1/allin1.xlsx `
  <临时预览目录> `
  docs/report/chart/generated/run_07_vs_vanilla/v1/report_manifest.json
```

工作簿包含 `ALLin1`、`Pair Data`、`Notes`。汇总行由 `Pair Data` 公式驱动；构建脚本按规范化
数据源检查配对数、更快请求数、两侧正确数、单边正确数、净正确条目和客户端错误，并扫描公式错误。
构建结束后必须查看三个预览 PNG；确认无截断、重叠或异常值后，才把 manifest 中
`workbook.preview_verified` 标记为 `true`。

## 4. 迭代另一组实验

复制 `configs/run_04_fixed_v1.json`，只修改：

- `comparison_id` 与 baseline/candidate 的显示名、run 目录、文件前缀；
- `scenes` 的规范化键、展示名和文件 slug；
- `expected` 的已知指标。新实验第一次接入时可先仅保留 `scenes`/`pairs`，人工核验后再补齐
  时延、Token 和 Judge 断言。
- 可选的 `judge_overrides` 与 `additional_inputs`；只有具备可审计人工裁决时才允许使用。

视觉迭代修改 `generate_report.py` 顶部的 `PALETTE` 或对应 renderer，并使用新的 revision；
不要覆盖旧图。统计公式改变时还需提升 `SCRIPT_VERSION`，更新本说明与设计文档变更记录。

## 5. 验收清单

- `python -m unittest tests.test_report_chart_workflow` 通过；
- manifest 的 `actual` 复现已知指标，所有输入与产物有 SHA-256；
- `allin1.csv`、`allin1.md`、`allin1.xlsx` 的 13 个场景及 MACRO/MICRO 一致；
- `all` profile 恰有 3 张总览图和 13 张逐场景图，每张同时有 SVG/PNG；
- 总览确认 13/13 场景时延、Token 均正收益；Judge 面板以净正确条目显示总体和局部变化；
- 逐场景图使用共享模板，parity 图、请求级 gain 分布和 Judge 转移均可独立解读；
- 本地产物标为既有日志“实测”的复算，不表述为重新运行了 vLLM 性能测试。

## 6. 当前样例

`generated/run_07_vs_vanilla/v1/`：13 个场景、633 对请求，宏平均 E2E 优化 50.65%，
请求加权 E2E 优化 44.58%，可见 Token 节省 55.61%；606/633 请求更快。人工裁决 1 条后，
两侧均为 558/633，Vanilla-only / run_07-only 均为 22 条，净正确条目变化为 0；6 个场景
各少 1 题，没有场景低于一题容差门。

历史样例 `generated/run_04_fixed_v1_vs_vanilla/v4/`：13 个场景、633 对请求，宏平均
E2E 优化 49.47%，请求加权 E2E 优化 47.70%，可见 Token 节省 60.06%；Judge 宏平均
+0.13 pp、微平均 -0.79 pp。该历史图不随 v1.2 工作流回刷；新生成的总览使用绝对条目数。
