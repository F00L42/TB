# fixed_v1 候选构造实例：NetQAD 云核 E2E 规划模块 101 条

## 0. 结论与状态

本文按 `docs/design/think_budget_generalization_plan.md` 6.2 构造一个首探候选，适用
测试集为：

```text
NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条
```

候选参数见（文件名直接复用测试集名称，仅去掉 variant 前缀和时间戳后缀）：
`docs/debug/fixed_param/NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条.txt`：

```text
===== fixed_v1 =====
enable_think_budget = true
fixed_prob_th = 0.93
fixed_check_length = 950
fixed_think_stride = 450
fixed_num_thinking = 4
fixed_min_think_tokens = 950
fixed_budget_tokens = 2350
```

状态：**待服务器精确复算、待 fixed 实测**。这只是该测试集的 `fixed_v1` 首探候选，
不是最终最优参数，也不能直接外推为整个 `fault_diagnosis`、`planning` 或其他测试集的
默认参数。

## 1. 设计目的与边界

### 1.1 目的

给出一个可复现的完整实例，验证“从 Vanilla 思考长度分布按固定公式生成首个 fixed
候选”的链路，并为后续一次性构造 13 个产品测试集候选提供相同口径和脚本。

### 1.2 不解决的问题

- 本文不证明该候选质量非劣或时延优于 Vanilla；必须由下一轮 `dtb_fixed_v1` 实测。
- 本文不选择最终 profile，不做 scene 自动绑定，也不校准拒识门。
- 本文不使用 DTB completion token 代替 Vanilla 思考 token。
- 本文不把 Judge 请求、隐藏 probe 或全局吞吐率当作被测请求 E2E。

### 1.3 证据边界

服务器与当前工作区的本轮证据目录均为 `docs/log/perf/run_04`，其中包含 13 个
Vanilla、13 个 `dtb_default`、结果 CSV 及隔离后的 DTB phase 提取。本文全部统计和
候选构造均基于 **run_04**，不存在跨 run 混用。当前本地环境没有目标模型 tokenizer，
因此思考 token 暂由同 run 的 DTB phase 校准；在服务器上使用测试时相同 tokenizer
执行精确模式后，若参数发生变化，应更新 TXT 和本文。

## 2. 为什么选择这个测试集

run_04 中该测试集有 101 对相同 `QA_ID`，无缺失配对，也没有客户端错误。配对结果为：

| 项目 | Vanilla | dtb_default | 配对变化 |
| --- | ---: | ---: | ---: |
| 样本数 | 101 | 101 | 101 对 |
| 平均 E2E | 287.4605 s | 212.3838 s | 改善 75.0768 s（26.1172%） |
| 平均 completion token | 5209.35 | 3649.76 | 减少 1559.58（29.9382%） |
| Judge 正确 | 94/101 | 93/101 | -1 条 |
| 客户端错误 | 0 | 0 | 无新增 |

逐样本质量迁移是：双方均正确 89 条、仅 Vanilla 正确 5 条、仅 DTB 正确 4 条、双方均
错误 3 条。DTB phase 还记录了平均 63.8614 个隐藏 probe token（合计 6450），这些
token 不在上表 completion token 的解释中被遗漏。

选择它作为实例，是因为样本量较大且默认 DTB 已观察到明确时延空间；同时质量少 1 条
说明 fixed 候选必须重新过质量硬门，不能从默认 DTB 结果直接宣称可上线。

证据强度：以上均为 **实测**（run_04 CSV、请求级 ledger 和 phase 提取）；候选在 fixed
模式下的表现仍是 **待实测**。

## 3. 输入与核心数据

### 3.1 输入文件

Vanilla：

```text
docs/log/perf/run_04/test_result/
vanilla_NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条_2026-08-03_13-07-41.csv
```

DTB 校准结果：

```text
docs/log/perf/run_04/test_result/
dtb_NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条_2026-08-04_02-34-41.csv
```

DTB 请求级精确 token ledger：

```text
docs/log/perf/run_04/extracted/
dtb_NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条_2026-08-04_02-34-41/
requests.jsonl
```

### 3.2 只保存与推导的数据

请求时只人工复制 TXT 中所选候选标题下的七个请求参数；`===== 候选名 =====` 只是
人工可读的组间分隔符，不属于请求参数。`T50`、各分位数、校准误差和派生的最大可见
思考长度仅用于实验审计，不进入运行时模型。按既有约束：

```text
derived_max_visible_think
  = fixed_check_length + fixed_num_thinking * fixed_think_stride
  = 950 + 4 * 450
  = 2750
```

## 4. 完整计算步骤

### 4.1 分离思考和答案

对 Vanilla `模型回答` 在第一个 `</think>` 处分割：标记之前为思考文本，之后为最终
答案。101/101 条均可分割；当前响应不含必需计入思考的开标签。

run_04 没有 Vanilla phase token，当前本地环境也没有目标模型 tokenizer。为遵守“不能
用 completion token 直接代替思考 token”，这里使用同测试集 DTB phase 做 tokenizer
关系校准；服务器复算时改用真实 tokenizer，后者为权威值。

### 4.2 本地校准 Vanilla 思考 token

对每条 DTB 结果同样分离思考和答案，并由 `request_id` 关联 `requests.jsonl` 中的
`visible_think_tokens` 与 `final_answer_tokens`。用 101 条 DTB 答案拟合以下线性关系：

```text
answer_text_features -> final_answer_tokens
```

特征只含可见文本统计：CJK 字符、ASCII 字母、数字、空白、标点、其他 ASCII、其他
非 ASCII、英文词段数和非空白段数。对每条记录再定义：

```text
ledger_residual
  = completion_tokens - visible_think_tokens - final_answer_tokens

estimated_vanilla_think_tokens
  = vanilla_completion_tokens
  - predicted_vanilla_answer_tokens
  - median(ledger_residual)
```

本测试集 `median(ledger_residual) = 0`。按结果行号取模做确定性 5 折校验，在每折只用
另外四折拟合，得到 DTB 已知思考 token 的回放误差：

| 校准检查 | 结果 |
| --- | ---: |
| MAE | 10.4575 token |
| 中位绝对误差 | 6.2523 token |
| P95 绝对误差 | 26.3703 token |
| 平均偏差 | +1.8332 token |
| 预测 T50 / 实际 T50 | 2823.1007 / 2819.0000 |

这个误差只说明估计足以生成首探候选，不把估计提升为“精确 tokenizer 实测”。

### 4.3 Vanilla 思考长度分布

把校准器应用到 101 条 Vanilla，得到：

| 分位点 | 估计思考 token |
| --- | ---: |
| min | 2742.0736 |
| P25 | 3953.4475 |
| **P50 / T50** | **4668.7062** |
| P75 | 5545.7613 |
| P95 | 7154.1867 |
| max | 8200.5809 |

### 4.4 代入 6.2 公式

统一使用最接近 50 的倍数，恰好在中点时 half-up：

```text
T50 = 4668.7062

fixed_check_length
  = clamp(round_to_50(0.20 * 4668.7062), 100, 2000)
  = clamp(round_to_50(933.7412), 100, 2000)
  = 950

fixed_min_think_tokens
  = fixed_check_length
  = 950

fixed_think_stride
  = clamp(round_to_50(0.10 * 4668.7062), 50, 1000)
  = clamp(round_to_50(466.8706), 50, 1000)
  = 450

fixed_budget_tokens
  = max(950, round_to_50(0.50 * 4668.7062))
  = max(950, round_to_50(2334.3531))
  = 2350

fixed_num_thinking
  = max(1, ceil((2350 - 950) / 450))
  = max(1, ceil(3.1111))
  = 4

fixed_prob_th = 0.93
```

## 5. 可复用工具与接口

脚本：`tools/construct_fixed_candidate.py`。

本地复现当前估计：

```powershell
python tools/construct_fixed_candidate.py docs/log/perf/run_04 `
  --vanilla-result "test_result/vanilla_NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条_2026-08-03_13-07-41.csv" `
  --candidate-label fixed_v1 `
  --output-dir docs/debug/fixed_param
```

服务器上对同一个 run_04 做权威复算：

```bash
python tools/construct_fixed_candidate.py docs/log/perf/run_04 \
  --vanilla-result 'test_result/vanilla_<该测试集>_<timestamp>.csv' \
  --tokenizer /absolute/path/to/the-tested-model \
  --candidate-label fixed_v1 \
  --output-dir docs/debug/fixed_param \
  --report-json 'docs/debug/fixed_param/NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条.audit.json'
```

接口规则：

- 提供 `--tokenizer` 时，脚本直接对每条 Vanilla 思考段执行
  `encode(add_special_tokens=False)`，这是最终采用的模式。
- 不提供 `--tokenizer` 时，脚本自动查找同测试集 DTB CSV 和隔离后的
  `requests.jsonl`，执行本文的 phase 校准估计，并在报告中明确标记
  `dtb_phase_calibrated_estimate`。
- 输出文件固定为 `<output-dir>/<原测试集名去前缀和时间戳>.txt`，不接受人工重命名；
  Windows 文件名不能包含 `*`，因此保留原测试集名称中的下划线。
- 每组以 `===== <candidate-label> =====` 分隔；同名候选重跑时原位更新，不同名称追加
  到同一测试集 TXT，便于后续记录 `budget_0.35`、`budget_0.50` 等多组参数。
- stdout 和可选 audit JSON 保存分位数、校准误差、原始计算值及候选。使用
  `--dry-run` 可只计算而不写 TXT。

## 6. 下一轮验收标准

候选构造完成不等于候选通过。对该测试集至少满足以下检查：

- [ ] run_04 的 101 条 Vanilla 均能分离思考和答案，并用测试时相同 tokenizer 精确计数；
- [ ] 记录模型、tokenizer revision/hash，脚本输出与人工代入 6.2 公式一致；
- [ ] `dtb_fixed_v1` 与 Vanilla 的 101 个 `QA_ID` 完整配对，采样、并发和 cache 口径一致；
- [ ] 每场景质量及纳入 13 场景后的宏平均质量通过预声明非劣界；
- [ ] 不新增解析错误、工具错误、关键事实错误等 hard failure；
- [ ] 该场景 `mean(E2E_vanilla_i - E2E_fixed_i) > 0`；
- [ ] 隐藏 probe token 和 phase 时间进入总模型成本解释；
- [ ] 通过质量门且收益为正后，才以 `0.35/0.50/0.65 * T50` 搜索 budget。

若 run_04 精确 T50 使任何 `round_to_50` 结果改变，以精确输出覆盖当前 TXT，并保留
audit JSON 说明变化原因。

## 7. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-08-05 | 使用本地 run_04 构造 NetQAD 云核 E2E 规划模块 101 条 fixed_v1 示例；新增精确/校准双模式构造脚本和待验证候选 TXT。 |
| 2026-08-05 | 参数结果统一下沉到 `docs/debug/fixed_param/`，文件名严格复用原测试集名；同一文件用候选标签分隔并支持多组参数。 |
