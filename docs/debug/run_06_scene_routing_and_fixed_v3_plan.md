# run_06 场景路由与 fixed_v3 并行计划

## 1. 目的与边界

run_06 同时回答两个彼此独立的问题：

1. 对 fixed_v2 已通过质量门的 6 个场景，只传 Think Budget 开关时，是否能自动识别
   scene 并精确恢复已验证参数；
2. 其余 7 个场景的 fixed_v3 是否能补回质量，同时继续保留端到端时延收益。

本轮不合并相近场景的运行时参数，不开启 difficulty/history，也不验证 reject 后的
native bypass。最终自动配置收口等待 fixed_v3 结果。

## 2. 决策链路

```text
最新一条可见 user message
  → required_all / required_any 模板门
  → score + margin 拒识门
  → scene 或 reject
  → scene 冻结预算或 default budget
  → request / global / fixed / scene hard limit 取最小值
  → BudgetPlan + decision telemetry
```

fixed 字段仍具有最高优先级。因此 7 个 fixed_v3 场景即使分类器给出其他候选，也不会
改变本轮实际参数；路由结果只作为 telemetry 留存。

## 3. 冻结参数与聚类结果

聚类先按 hard limit 分成三档，目的是为 fixed_v3 后的类内交叉回放提出候选，而不是
立即减少运行时配置。当前 6 个场景的 check、stride、轮数和阈值仍不完全相同；在没有
“同一聚类中心对类内所有场景质量非劣”的证据前，运行时保留 6 组精确叶子参数。

| 候选簇 | scene | 适用测试集 | check / stride / rounds / min / hard / prob | 模板门 |
| --- | --- | --- | --- | --- |
| fast | `react_machine_data_analysis` | `2026-02-11_ReAct白盒-机器数据分析29题` | 500 / 250 / 2 / 500 / 850 / 0.93 | `<Packet Parameter Description>` 且 `Can_a_conclusion_be_drawn` |
| fast | `netqad_e2e_single_step` | `NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_单步规划_80条` | 300 / 300 / 3 / 300 / 1050 / 0.93 | `绝对不允许超出“当前诊断思路”` 且 `根据“当前诊断思路”、“已执行记录”` |
| medium | `react_whitebox_overall_planning` | `2026-02-11_ReAct白盒-整体规划` | 850 / 450 / 2 / 850 / 1500 / 0.93 | `## Executed Records step1` 且 API 分类约束句 |
| medium | `kpi_configuration_assurance` | `KPI配置保障专家-单网元用例` | 500 / 300 / 4 / 500 / 1600 / 0.93 | 中英文“根因分析规则和分析线索”任一版本 |
| deep | `long_context_root_cause` | `复杂逻辑推理_性能告警_长文本根因推理_下沉47` | 1200 / 400 / 4 / 1200 / 2600 / 0.93 | `### 背景知识` 且 `故障对应KPI说明` |
| deep | `netqad_e2e_planning` | `NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条` | 950 / 450 / 5 / 950 / 3050 / 0.95 | 多操作步骤约束且包含工单告警参数定义 |

`hard` 对应 fixed 实验的 `fixed_budget_tokens`。scene 分支通过
`max_think_tokens` 保存这一不可从四维向量可靠推导的停止上限。

## 4. run_06 输入矩阵

### 4.1 只传开关的 6 个场景

以下测试集不传任何 `fixed_*` 字段，或确保它们全部为零：

- ReAct 白盒整体规划；
- ReAct 白盒机器数据分析；
- KPI 配置保障；
- NetQAD E2E 单步规划；
- NetQAD E2E 规划模块；
- 长文本根因推理。

请求参数只有：

```text
enable_think_budget = true
```

测试框架原有的采样参数继续与基线对齐；其中 `max_tokens` /
`max_completion_tokens` 不能低于对应 scene 的 hard limit（统一设置时至少为 3050），
否则请求上限会先于 scene 上限生效，无法验证冻结参数。

### 4.2 显式 fixed_v3 的 7 个场景

人工从 `docs/debug/fixed_param/<原测试集名称>.txt` 复制对应 fixed_v3 块：

| 测试集 | 候选名 |
| --- | --- |
| ReAct 白盒单步规划 | `fixed_v3_recover_budget_080` |
| 事件自闭环 | `fixed_v3_recover_check_040` |
| 告警诊断建议总结 | `fixed_v3_bracket_check_mid` |
| 告警诊断结论总结 | `fixed_v3_recover_check_040` |
| 投诉助理工具调用 | `fixed_v3_recover_check_040` |
| 投诉分类中文 | `fixed_v3_bracket_budget_mid` |
| 投诉分类英文 | `fixed_v3_recover_check_budget` |

服务器配置必须保持：

```text
enable_scene_budget = true
enable_difficulty_estimator = false
enable_history_budget = false
scene_override = ""
```

history 暂不改为 `true`。虽然本轮已经能打印 reject 原因和 default 回退，阶段二要求的
“陌生 scene 首次原生生成”仍未实现；提前启用会把 run_06 变成分类与 history 的混合
实验。

## 5. 接口、不变量与失败处理

- 分类输入只能是最新可见 user message，不能包含 system prompt、参考答案或 Judge 文本；
- 产品模板门命中后加固定高分；若两个产品模板同时命中，margin 不足仍应 reject；
- 自动场景的四维预算字段必须全部来自 `scene`，不得出现 `scene+difficulty`；
- hard limit 是所有有效上限的最小值，并在 `source_by_field` 中记录候选集合和绑定来源；
- 未识别请求在第一阶段记录 `decision_branch=history`、
  `budget_mode=history_disabled`、`fallback_mode=default_budget`；这不是 native bypass；
- fixed_v3 请求必须记录 `decision_branch=fixed`，hard limit 由
  `fixed_budget_tokens` 绑定。

## 6. 离线验证与 run_06 验收

本地验证命令：

```powershell
python -m unittest `
  tests.test_adapter_scene_classifier `
  tests.test_adapter_scene_config `
  tests.test_adapter_budget_decision
```

当前 run_05_fixed_v2 明细回放的验收结果：

- 633 条请求全部进入离线分类测试；
- 6 个冻结场景 282/282 命中目标 scene；
- 其余 7 个场景 351/351 未误入上述 6 个产品 scene；
- 6 组内置 fallback 配置与 YAML 由契约测试约束；安装 PyYAML 的服务器/CI 会做结构
  等值校验，本地未安装 PyYAML 时该项明确 skip。

run_06 回传后先做裁决审计，再分析 E2E/质量：

- 6 个自动场景：请求数、`scene_matched=true`、目标 scene、
  `decision_branch=scene_difficulty`（现有枚举名）、有效字段值与来源全部闭合；
- 7 个 fixed_v3 场景：全部命中 fixed 分支，参数与候选 txt 一致；
- 任何自动场景 reject、预算字段不等于冻结值、出现 difficulty/history 参与，均视为配置
  或路由失败，不能进入收益汇总；
- 只有 fixed_v3 质量门通过后，才比较簇内共享中心并决定是否减少 profile 数。

## 7. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-08-07 | 初版：冻结 6 个自动 scene、形成 3 个候选预算簇、定义 7 fixed_v3 + 6 自动的 run_06 输入矩阵和验收条件。 |
