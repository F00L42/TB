# run_04 结果：DTB 裁决分支与生效配置审计

## 0. 结论

run_04 包含相同 13 个测试集的 `vanilla` 与 `dtb_default`，共 26 个具体结果文件。
Vanilla 是原生单次生成，不经过 `BudgetController`，因此没有 DTB 裁决分支；本报告的
裁决与配置统计针对 633 个 `dtb_default` 请求。

实测结论：

- 594/633 个请求命中 `scene_difficulty`；
- 39/633 个请求进入代码中的 `history` 兜底分支，但全部记录为
  `history_disabled`，实际采用默认预算，没有使用历史数据；
- 0/633 个请求进入 `fixed`，也没有任何预算字段被 fixed override；
- 560/633 个请求被分类为 `fault_diagnosis`，说明当前 scene 路由明显集中于该类；
- 同一产品测试集可能命中多个 scene，或因难度不同获得多个预算，不能把
  `dtb_default` 描述成“每个测试集使用一组固定配置”。

本报告只还原裁决与生效配置，不替代 `_SUMMARY.csv` 中的 E2E、token 和质量比较，也
不代表尚未运行的 `dtb_fixed_v1` 已通过质量或时延验收。

## 1. 证据与覆盖率

输入：

- [`test_result/_SUMMARY.csv`](test_result/_SUMMARY.csv)：26 个结果总览；
- `test_result/<具体结果>.csv`：1266 条客户端记录；
- [`extraction_manifest.json`](extraction_manifest.json)：提取覆盖率与隔离审计；
- [`request_index.csv`](request_index.csv)：客户端行到服务日志请求的绑定；
- `extracted/<dtb 结果名>/requests.jsonl`：请求级裁决、BudgetPlan 和 token ledger；
- `extracted/<dtb 结果名>/controller_phases.jsonl`：请求级 phase 明细。

覆盖率闭合：

| 检查 | 结果 |
| --- | ---: |
| 具体结果文件 | 26（13 Vanilla + 13 DTB） |
| 客户端结果行 | 1266（633 + 633） |
| 非空客户端 request_id | 1266 |
| DTB phase parent request | 633 |
| 服务日志 TB phase | 7824 |
| 被白名单选中的 TB phase | 7824 |
| 被排除的 TB phase | 0 |
| Vanilla 中出现 DTB phase | 0，符合原生路径预期 |

证据强度：以上及后文计数均为 **实测**；分支条件与字段覆盖顺序同时由
[`think_budget_vllm_adapter/budget_controller.py`](../../../../think_budget_vllm_adapter/budget_controller.py)
**源码确定**。

## 2. 分支语句与总体结果

当前源码的裁决顺序：

```text
if fixed_budget_tokens > 0:
    FIXED
elif scene_matched:
    SCENE_DIFFICULTY
else:
    HISTORY
    if history disabled:
        use default budget

apply per-field fixed overrides
apply hard token limit
apply fixed/override/branch confidence threshold
```

run_04 实际分支：

| `decision_branch` | 请求数 | 实际含义 |
| --- | ---: | --- |
| `scene_difficulty` | 594 | scene 命中；四个预算字段来自 `scene+difficulty` |
| `history` | 39 | scene 被拒识；history 已关闭，因此四个预算字段来自 `default` |
| `fixed` | 0 | 本轮未传 `fixed_budget_tokens` |

scene 结果：

| 最终 scene | 请求数 | 阈值来源 |
| --- | ---: | --- |
| `fault_diagnosis` | 560 | scene，0.95 |
| `code_generation` | 26 | scene，0.95 |
| `planning` | 6 | scene，0.93 |
| `retrieval` | 2 | scene，0.90 |
| `general`（拒识） | 39 | runtime default，0.95 |

所有 633 个请求共同使用：

```text
policy_version = think-budget-performance-v1
percentile_step = 0.75
percentile_step source = history_stride_percentile
hard_think_token_limit = 65536
hard limit source = context/request/config
```

`percentile_step` 的来源名称包含 `history`，不表示本轮使用了历史预算；是否真正读取
history 应以 `decision_details.history.budget_mode` 和预算字段来源为准。

## 3. 全部生效预算签名

签名定义：

```text
(check_length, min_think_budget, num_thinking, think_stride; confidence_threshold)
```

### 3.1 fault_diagnosis：17 种

| ID | 请求数 | 生效预算签名 |
| --- | ---: | --- |
| F01 | 512 | `(2819, 2646, 13, 2669; 0.95)` |
| F02 | 11 | `(2881, 2711, 13, 2731; 0.95)` |
| F03 | 6 | `(2949, 2783, 13, 2799; 0.95)` |
| F04 | 5 | `(3037, 2876, 14, 2887; 0.95)` |
| F05 | 4 | `(2904, 2736, 13, 2754; 0.95)` |
| F06 | 4 | `(3058, 2898, 14, 2908; 0.95)` |
| F07 | 3 | `(2834, 2662, 13, 2684; 0.95)` |
| F08 | 3 | `(2994, 2830, 14, 2844; 0.95)` |
| F09 | 3 | `(2927, 2760, 13, 2777; 0.95)` |
| F10 | 2 | `(3203, 3050, 14, 3053; 0.95)` |
| F11 | 1 | `(2858, 2687, 13, 2708; 0.95)` |
| F12 | 1 | `(2972, 2807, 14, 2822; 0.95)` |
| F13 | 1 | `(3162, 3008, 14, 3012; 0.95)` |
| F14 | 1 | `(3101, 2943, 14, 2951; 0.95)` |
| F15 | 1 | `(3080, 2921, 14, 2930; 0.95)` |
| F16 | 1 | `(3242, 3092, 14, 3092; 0.95)` |
| F17 | 1 | `(3016, 2853, 14, 2866; 0.95)` |

合计 560。全部四个预算字段来源均为 `scene+difficulty`，差异来自逐请求难度结果。

### 3.2 其他 scene 与拒识默认值

| ID | scene/分支 | 请求数 | 生效预算签名 |
| --- | --- | ---: | --- |
| C01 | `code_generation` | 26 | `(3569, 3546, 17, 2819; 0.95)` |
| P01 | `planning` | 6 | `(2869, 2696, 13, 2669; 0.93)` |
| R01 | `retrieval` | 2 | `(2669, 2571, 11, 2619; 0.90)` |
| D01 | `general` / `history_disabled` | 39 | `(200, 100, 10, 100; 0.95)` |

## 4. 按测试集还原

| 测试集 | 请求数 | 最终分支、scene 与预算签名 |
| --- | ---: | --- |
| `2026-02-11_ReAct白盒-单步规划` | 79 | `scene_difficulty/fault_diagnosis`：F01×79 |
| `2026-02-11_ReAct白盒-整体规划` | 16 | fault：F01×2；planning：P01×3；`history_disabled`：D01×11 |
| `2026-02-11_ReAct白盒-机器数据分析29题` | 29 | code：C01×26；fault：F01×2；retrieval：R01×1 |
| `KPI配置保障专家-单网元用例` | 9 | fault：F01×9 |
| `NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_单步规划_80条` | 80 | fault：F01×77；planning：P01×3 |
| `NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条` | 101 | fault：F01×82、F04×5、F07×2、F09×2、F06×2、F11/F03/F08/F05/F12/F13/F14 各×1；`history_disabled`：D01×1 |
| `事件自闭环_25题_0514` | 25 | fault：F01×20、F05×3、F08×2 |
| `告警诊断_recipe_ReAct方案诊断_建议总结_11条_0508` | 11 | fault：F03×5、F01×3、F15/F10/F16 各×1 |
| `告警诊断_recipe_ReAct方案诊断_结论总结_13条` | 13 | fault：F01×8、F07/F06/F17/F02/F09 各×1 |
| `复杂逻辑推理_性能告警_长文本根因推理_下沉47` | 47 | fault：F01×35、F02×10、F10/F06 各×1 |
| `投诉专家-提参_电信工具调用_React_投诉助理_39题_zh_0403_慢思考模式` | 39 | fault：F01×32；`history_disabled`：D01×7 |
| `投诉专家_分类_164题_zh_0203` | 164 | fault：F01×163；retrieval：R01×1 |
| `投诉专家_分类_20题_en_0203_慢思考模式` | 20 | `history_disabled`：D01×20 |

表中请求数合计 633；各预算签名次数与第 3 节合计一致。

## 5. 拒识请求

39 个拒识全部因为 `score_margin=0 < min_margin=1`，没有 `below_min_score`。进入
`DecisionBranch.HISTORY` 后又因 history 关闭而使用 D01。

| 测试集 | 数量 | 客户端结果 CSV 行 | 候选 scene 与分数 |
| --- | ---: | --- | --- |
| ReAct白盒-整体规划 | 11 | 2、3、5、6、7、8、9、10、11、12、14 | planning，`9/9`，margin 0 |
| NetQAD 云核 E2E 规划模块 101 条 | 1 | 18 | planning，`14/14`，margin 0 |
| 投诉助理工具调用 39 题 | 7 | 2、3、16、20、22、30、35 | fault_diagnosis，`4/4`，margin 0 |
| 投诉分类英文 20 题 | 20 | 2–21 | fault_diagnosis，`3/3`，margin 0 |

这里的 CSV 行号包含表头偏移，可直接与请求级 ledger 的 `test_result_row` 对照。

## 6. 对泛化性实验的含义

1. 当前分类器把 88.5%（560/633）的 DTB 请求路由为 `fault_diagnosis`，包括规划、
   分类和投诉任务；它不能作为“产品测试集语义类型已经识别正确”的证据。
2. `dtb_default` 的收益或回归同时混合了 scene 选择和逐请求难度预算，不能只归因于某个
   scene 预设。
3. 英文投诉分类全部拒识并使用很小的 D01，属于后续 scene 证据和拒识规则必须覆盖的
   明确样本。
4. 下一轮 fixed 实验应消除动态预算混杂。每个请求必须记录：

```text
decision_branch = fixed
source_by_field.check_length.source = fixed_budget 或 fixed_check_length
source_by_field.min_think_budget.source = fixed_budget 或 fixed_min_think_tokens
source_by_field.num_thinking.source = fixed_budget 或 fixed_num_thinking
source_by_field.think_stride.source = fixed_budget 或 fixed_think_stride
source_by_field.confidence_threshold.source = fixed_prob_th
```

若 fixed 结果没有满足以上条件，该轮不能作为候选参数性能证据。

## 7. 固定分析流程

每个包含 DTB 的 run 在完成 `tools/extract_tb_phase_metrics.py <run_dir>` 后，必须执行
一次裁决审计并把结果写入同目录 `RESULT.md`：

1. 从 `_SUMMARY.csv` 取得具体结果文件清单，只分析预期经过控制器的 variant；
2. 以每个隔离目录的 `requests.jsonl` 为请求级权威输入，禁止从全局日志采样推断；
3. 按测试集汇总 `decision_branch`、`scene`、`scene_matched` 和 `reject_reason`；
4. 以
   `(check_length, min_think_budget, num_thinking, think_stride,
   confidence_threshold, percentile_step, hard_think_token_limit)`
   作为生效配置签名，并统计 `source_by_field`；
5. 单列同一测试集内的多 scene、多配置、拒识及 fixed 未生效请求；
6. 完成以下闭合检查，任一失败则该 run 不进入性能选择：

```text
sum(各结果客户端行) = extraction_manifest.client_rows
sum(DTB 各测试集请求) = DTB requests.jsonl 总数
sum(decision_branch) = sum(scene) = DTB 请求总数
每个 DTB 请求恰有一个 effective BudgetPlan
Vanilla 原生路径没有 DTB decision phase
dtb_default 不出现 fixed 分支或 fixed 字段来源
dtb_fixed_* 全部进入 fixed，且字段来源与传入参数一致
```

7. 在 `RESULT.md` 中区分 **实测**、**源码确定** 和 **待验证**，再进行 E2E/质量候选
   选择。

## 8. 当前未覆盖项

- 本报告没有目标模型 tokenizer，Vanilla 思考 token 的精确分解另见 fixed 候选构造
  文档；
- 尚无 `dtb_fixed_v1` 请求，因此 fixed 分支校验仍待下一轮运行；
- 本报告不判断 scene 标签在语义上是否正确，只报告分类器实际输出；
- 本报告不把 Judge 请求计入被测请求 E2E 或 BudgetPlan 统计。

## 9. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-08-05 | 初版：固化 run_04 的 633 个 DTB 请求分支、scene、有效配置、拒识和后续审计流程。 |
