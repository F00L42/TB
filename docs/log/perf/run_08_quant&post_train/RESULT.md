# run_08_quant&post_train：W8A8 量化增训权重的自动 DTB 复验

## 0. 结论

本轮包含同一组 W8A8 量化、特定场景思维链压缩语料增训权重上的两个完整 variant：
13 场景 Vanilla 和 13 场景仅开启 DTB，各 633 条请求。主比较只使用本 run 内、同权重的
Vanilla，不沿用开源权重基线。

- **实测——分类和配置执行正确**：633/633 个 DTB 请求均从最新用户消息命中预期
  `scene`，全部进入 `scene_difficulty`；13 个生效预算签名与 run_07 的开源权重配置
  逐 scene 完全相同。没有 fixed、history、difficulty、reject 或 default 混入。
- **实测——性能仍有收益但显著收窄**：请求加权平均 E2E 从 77.1998 s 降到
  65.7576 s，改善 11.4422 s / **14.82%**；场景等权宏平均 paired gain 为
  13.3320 s，场景等权相对降幅为 **16.46%**。12/13 场景平均 E2E 改善，431/633
  个配对请求更快。
- **实测——质量门明确失败**：Vanilla 为 586/633，DTB 为 547/633，减少 39 题，
  微平均下降 6.16 pp；只有 2 个场景严格非劣、2 个场景少 1 题，另 9 个场景少 2～10
  题，超过预声明容差。58 题由 Vanilla 正确变为 DTB 错误，19 题反向改善。
- **实测——长文本根因推理发生双退化**：平均 E2E 增加 14.0330 s / 14.69%，响应
  completion token 增加 5.64%，正确数从 47 降至 40；该 scene 不应继续使用当前预算。
- **决策——最终只新增一份独立 YAML**：当前 13 组预算不能作为量化增训权重的默认
  配置。分类规则继续沿用；量化增训权重的 13 组入选参数最终写入一份独立 YAML，由
  部署人员在启动服务前人工选择并替换当前 `budget_config.yaml`。不增加运行时预算策略
  身份、模型选择器或动态配置路由。

这不表示现在应把 13 组新数值一次性写死。本轮只观测了每个 scene 的一个旧配置点，
不足以确定 9 个失败 scene 的新最优参数。已根据新权重 Vanilla 思考长度、run_08 的
停止位置和质量缺口构造
[`fixed_v4_quant_post_train`](../../../debug/fixed_v4_quant_post_train_candidate_plan.md)：
4 个通过场景原样保留，9 个失败场景推迟首次 probe。v4 仍须先做 fixed 实测，再决定
独立 YAML 中的最终值。

## 1. 输入、实验身份与证据边界

权威输入：

- 客户端汇总：[`test_result/_SUMMARY.csv`](test_result/_SUMMARY.csv)；
- 客户端明细：13 个 `qp_vanilla_*.csv` 和 13 个 `qp_dtb_*.csv`；
- request-id 与日志归属：[`extraction_manifest.json`](extraction_manifest.json)、
  [`request_index.csv`](request_index.csv)；
- DTB 请求与 phase：`extracted/<result_stem>/requests.jsonl`、`phase_summary.csv` 和
  `controller_phases.jsonl`。

用户已声明两个 variant 均使用 W8A8 量化和特定场景压缩语料增训权重。目录没有独立的
实验 `manifest.json`，因此缺少精确 checkpoint/model ID、量化工具及配置版本、服务和
adapter commit、sampling/seed、并发/到达节奏、prefix cache 等身份字段。本文可对同
run 两个 variant 的实测结果作配对比较，但不能把新权重与历史开源权重间的全部差异只
归因于量化或增训。

## 2. 完整性、配对与裁判审计

| 检查项 | 结果 |
| --- | ---: |
| 结果 CSV | 26（13 Vanilla + 13 DTB） |
| 客户端行 / 非空且 run 内唯一 request_id | 1,266 / 1,266 |
| 每个 variant 的场景 / 请求 | 13 / 633 |
| 同场景 QA_ID 集合一致 | 13/13 |
| 同场景、同 QA_ID 的问题和参考答案一致 | 633/633 |
| 重复 QA_ID / 缺失配对 | 0 / 0 |
| Vanilla 日志 phase | 0/633，合法原生路径 |
| DTB 日志覆盖 / TB phase 覆盖 | 633/633 / 633/633 |
| 选中 / 排除 phase | 3,223 / 0 |
| 客户端错误 Vanilla / DTB | 0 / 0 |
| 未解决 Judge Vanilla / DTB | 0 / 0 |

Vanilla 使用 623 条 `llm_judge` 和 10 条 `deterministic_exact`；DTB 使用 622 条
`llm_judge` 和 11 条 `deterministic_exact`。两侧 1,266 条均有字面量 `True` 或
`False`，无需人工覆盖。自动 Judge 的稳定性限制仍然存在，但本轮没有协议无效项。

## 3. 自动分类、决策分支与实际配置

### 3.1 总体路由

| 遥测项 | 计数/结果 |
| --- | ---: |
| `classification_source=latest_user_message` | 633/633 |
| 命中预期 scene / `scene_matched=true` | 633/633 / 633/633 |
| `decision_branch=scene_difficulty` | 633/633 |
| reject / default / fixed | 0 / 0 / 0 |
| difficulty 非空 / history 非空 | 0 / 0 |
| 每个结果文件的配置签名数 | 1 |
| 最小 top1-top2 score margin | 82 |
| scene classifier 平均 / P95 / 最大 CPU | 16.699 / 39.813 / 127.533 ms |
| `policy_version` | `think-budget-performance-v1` |

`scene_difficulty` 只是 scene 预算分支名；本轮 `difficulty=null`，没有难度估计参与。
`percentile_step=0.75` 的来源标签为 `history_stride_percentile`，但 history 本身关闭且
`history=null`，不能把它记作 history 命中。

### 3.2 13 个实际预算

四维顺序为 `(check_length, min_think_budget, num_thinking, think_stride)`。四维和
confidence 均来自 scene，hard limit 来自 `scene.max_think_tokens`。

| scene | N | 四维预算 | confidence | hard limit |
| --- | ---: | --- | ---: | ---: |
| `react_whitebox_single_step` | 79 | `(450, 450, 4, 200)` | 0.93 | 1,100 |
| `react_whitebox_overall_planning` | 16 | `(850, 850, 2, 450)` | 0.93 | 1,500 |
| `react_machine_data_analysis` | 29 | `(500, 500, 2, 250)` | 0.93 | 850 |
| `kpi_configuration_assurance` | 9 | `(500, 500, 4, 300)` | 0.93 | 1,600 |
| `netqad_e2e_single_step` | 80 | `(300, 300, 3, 300)` | 0.93 | 1,050 |
| `netqad_e2e_planning` | 101 | `(950, 950, 5, 450)` | 0.95 | 3,050 |
| `event_self_closure` | 25 | `(700, 700, 3, 350)` | 0.93 | 1,700 |
| `alarm_diagnosis_advice` | 11 | `(850, 850, 3, 450)` | 0.93 | 2,150 |
| `alarm_diagnosis_conclusion` | 13 | `(1300, 1300, 4, 450)` | 0.93 | 2,750 |
| `long_context_root_cause` | 47 | `(1200, 1200, 4, 400)` | 0.93 | 2,600 |
| `complaint_tool_use` | 39 | `(1000, 1000, 3, 250)` | 0.93 | 1,650 |
| `complaint_classification_zh` | 164 | `(400, 400, 3, 200)` | 0.93 | 1,000 |
| `complaint_classification_en` | 20 | `(400, 400, 4, 200)` | 0.93 | 1,050 |

对 run_07 的 633 条 `requests.jsonl` 做同样签名后，13/13 个 scene 与本轮逐字段相等。
因此本轮不是“分类器没有识别新权重”，而是**相同预算策略在新权重上不再满足质量门**。

## 4. 同权重 Vanilla → DTB 的逐场景结果

`E2E gain = Vanilla 平均 E2E - DTB 平均 E2E`，正值表示 DTB 更快。Token 只表示客户端
响应 completion token，不含 hidden probe。质量层为：严格非劣、少 1 题、失败（少
2 题及以上）。

| 场景 | N | Vanilla E2E | DTB E2E | E2E gain | 响应 Token 降幅 | 正确数 V→D | 质量层 | 更快请求 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| ReAct 白盒单步规划 | 79 | 52.5016 s | 50.0799 s | 2.4216 s / 4.61% | 26.52% | 71→68 | 失败（-3） | 45/79 |
| ReAct 白盒整体规划 | 16 | 102.2725 s | 96.5518 s | 5.7207 s / 5.59% | 15.81% | 12→8 | 失败（-4） | 9/16 |
| ReAct 白盒机器数据分析 | 29 | 66.0901 s | 60.3857 s | 5.7045 s / 8.63% | 28.96% | 26→24 | 失败（-2） | 15/29 |
| KPI 配置保障 | 9 | 77.3798 s | 45.3749 s | 32.0049 s / 41.36% | 48.53% | 9→9 | 严格 | 9/9 |
| NetQAD E2E 单步规划 | 80 | 61.0197 s | 59.9231 s | 1.0966 s / 1.80% | 25.84% | 80→78 | 失败（-2） | 41/80 |
| NetQAD E2E 规划模块 | 101 | 126.8973 s | 105.4834 s | 21.4139 s / 16.87% | 26.46% | 100→90 | 失败（-10） | 74/101 |
| 事件自闭环 | 25 | 108.9606 s | 74.5084 s | 34.4522 s / 31.62% | 38.12% | 23→22 | 少 1 题 | 22/25 |
| 告警诊断建议总结 | 11 | 109.3083 s | 78.2880 s | 31.0203 s / 28.38% | 35.54% | 10→5 | 失败（-5） | 10/11 |
| 告警诊断结论总结 | 13 | 104.9861 s | 91.6207 s | 13.3654 s / 12.73% | 19.67% | 13→10 | 失败（-3） | 13/13 |
| 长文本根因推理 | 47 | 95.5431 s | 109.5761 s | -14.0330 s / -14.69% | -5.64% | 47→40 | 失败（-7） | 3/47 |
| 投诉助理工具调用 | 39 | 71.1545 s | 69.1994 s | 1.9551 s / 2.75% | 12.92% | 37→33 | 失败（-4） | 25/39 |
| 投诉分类中文 | 164 | 56.5358 s | 37.7912 s | 18.7445 s / 33.16% | 45.20% | 138→141 | 严格（+3） | 146/164 |
| 投诉分类英文 | 20 | 47.1800 s | 27.7303 s | 19.4497 s / 41.22% | 51.13% | 20→19 | 少 1 题 | 19/20 |

总体汇总：

| 指标 | Vanilla | DTB | 收益/变化 |
| --- | ---: | ---: | ---: |
| 总 E2E | 48,867.4710 s | 41,624.5493 s | -7,242.9217 s |
| 请求加权平均 E2E | 77.1998 s | 65.7576 s | 11.4422 s / 14.82% |
| 13 场景等权宏平均 paired gain | — | — | 13.3320 s |
| 13 场景等权相对降幅 | — | — | 16.46% |
| 全体 P50 / P95 | 65.7183 / 138.6804 s | 61.9350 / 123.2265 s | 改善 5.76% / 11.14% |
| 响应 completion token | 834,888 | 603,704 | -231,184 / 27.69% |
| 正确数 | 586/633 | 547/633 | -39 / -6.16 pp |
| 场景等权正确率 | 93.50% | 82.04% | -11.46 pp |

平均 E2E 有 12/13 个场景改善；P50 有 11/13 个场景改善，白盒整体规划和长文本根因
推理回退；P95 有 10/13 个场景改善，白盒单步规划、NetQAD 单步规划和长文本根因推理
回退。不能用总体 P50/P95 改善掩盖这些 scene 级反例。

## 5. 质量转移与失败分布

| 质量状态 | 场景数 | 场景 |
| --- | ---: | --- |
| 严格非劣 | 2 | KPI 配置保障、投诉分类中文 |
| 少 1 题 | 2 | 事件自闭环、投诉分类英文 |
| 少 2 题及以上 | 9 | 其余 9 个场景 |

请求级转移为：

| 转移 | 数量 |
| --- | ---: |
| Vanilla 正确、DTB 错误 | 58 |
| Vanilla 错误、DTB 正确 | 19 |
| 净变化 | -39 |

NetQAD 规划模块单场景净少 10 题；长文本根因推理少 7 题；告警建议少 5 题。即使考虑
Judge 和生成重复性，39 题总缺口和 9 个失败场景也远大于当前一题容差，不能归类为
run_07_vanilla 中观察到的普通轮间波动。

本轮没有未解决 Judge 或客户端错误，但尚未对 58 条回退逐条进行解析错误、工具调用、
关键事实遗漏等 hard failure 人工审核。质量门已因正确数失败，hard failure 审核不影响
“当前配置不可入选”的结论，只影响后续新候选的最终验收。

## 6. 模型工作量与 phase 账本

| phase | calls | wall time | 账本占比 | generated token | token 占比 | queue/prefill/decode known |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `initial_think` | 633 | 25,339.434 s | 60.96% | 398,828 | 62.11% | 633/633/633 |
| `iterative_think` | 377 | 6,583.906 s | 15.84% | 97,815 | 15.23% | 377/377/377 |
| `confidence_probe` | 947 | 3,220.419 s | 7.75% | 38,127 | 5.94% | 947/947/947 |
| `final_answer` | 633 | 6,409.764 s | 15.42% | 107,371 | 16.72% | 633/633/633 |
| `decision` | 633 | 10.680 s | 0.03% | 0 | 0% | 0/0/0 |
| **合计** | **3,223** | **41,564.204 s** | **100%** | **642,141** | **100%** | — |

账本闭合：

- `visible_think=496,643`，`hidden_probe=38,127`，`final_answer=107,371`，三者之和
  为 642,141，与 phase generated token 完全相等；
- `visible_think + final_answer = 604,014`，比客户端 completion token 603,704 多
  310（0.05%），属于 usage/tokenizer 边界差；
- phase wall 为 41,564.204 s，DTB 客户端 E2E 为 41,624.549 s，未覆盖差值
  60.346 s / 0.145%；
- 若把 hidden probe 计入，DTB 总模型生成量相对 Vanilla 响应 completion token 的
  降幅是 **23.09%**，不是客户端 usage 显示的 27.69%；hidden probe 相当于 DTB
  completion token 的 6.32%，并占 DTB E2E 账本的 7.75%。

长文本根因推理平均 visible think 为 1,363.60 token、hidden probe 为 50.45、final
answer 为 224.51；总模型生成量已经高于该场景 Vanilla 的平均 1,501.11 completion
token，与其 Token 和 E2E 双回退一致。告警建议和告警结论均在首轮 probe 接受全部请求，
却分别少 5 题和 3 题，说明对新权重而言当前最小 think budget/置信度组合过早，而不是
增加相同 stride 就能自动补救。

## 7. 与开源权重结果的关系

下表仅用于说明工作点变化，不是同实验身份的因果比较：

| 权重 / variant | 请求加权平均 E2E | 平均响应 completion token | 正确数 |
| --- | ---: | ---: | ---: |
| 开源权重 run_07_vanilla | 220.0387 s | 3,149.97 | 567/633 |
| 开源权重 run_07 自动 DTB | 94.5468 s | 1,388.02 | 558/633（含人工裁决） |
| 量化增训权重 Vanilla | 77.1998 s | 1,318.94 | 586/633 |
| 量化增训权重自动 DTB | 65.7576 s | 953.72 | 547/633 |

相对最新一轮开源权重 Vanilla，本轮量化增训 Vanilla 观测到 E2E 低 64.92%、响应 Token
低 58.13%，且多判对 19 题。13 个场景的 Vanilla 平均响应 Token 均下降，范围为
40.10%～66.41%。由于缺少实验 manifest，不能把这些跨 run 差异完全归因于 W8A8 或
增训；但“同一配置的性能—质量工作点已经变化”由本 run 的同权重 Vanilla/DTB 配对直接
证明。

开源权重配置在 run_07 相对当时主基线取得 44.58% 请求加权 E2E 收益并通过一题容差
门；同样 13 个预算签名在本轮只有 14.82% 收益且少 39 题。预算是模型行为和成本曲线的
函数，不能仅按 scene 名跨权重继承。

## 8. 独立 YAML 决策与 fixed_v4

### 8.1 决策

**最终新增一份量化增训权重专用 YAML，由人工选择替换；不新增预算策略身份。**

理由：

1. 分类器 633/633 命中预期 scene，最小 margin 为 82，不是当前失败点；
2. 13 组预算与 run_07 完全相同，却有 9 个 scene 质量失败，证明预算不可跨权重冻结；
3. 新 Vanilla 已自然缩短到平均 1,318.94 token，可压缩空间明显低于开源权重；
4. 长文本根因推理出现负收益，继续全量启用旧配置会同时损害质量和性能；
5. 当前 [`budget_config.yaml`](../../../../think_budget_vllm_adapter/config/budget_config.yaml)
   和 [`unified_config.py`](../../../../think_budget_vllm_adapter/config/unified_config.py)
   已是单一固定路径加载模式，人工替换文件即可满足单模型服务部署，不需要引入运行时
   模型到策略的映射。

### 8.2 最终文件与人工选择边界

最终收口方式：

1. 保留当前开源权重 [`budget_config.yaml`](../../../../think_budget_vllm_adapter/config/budget_config.yaml)
   的 13 组预算冻结，不用 run_08 的失败结果覆盖；
2. v4 搜索完成后，以当前 YAML 为结构模板新增且仅新增一份量化增训权重专用文件，建议
   名为 `budget_config_quant_post_train.yaml`；
3. 两份 YAML 都包含完整 `RUNTIME`、`PERFORMANCE_POLICY`、`SCENES`、priority、
   difficulty 和 history 配置；分类 indicators 保持一致，差异集中在 13 个产品 scene
   的预算值；
4. 部署人员根据实际加载权重，在服务停止状态下人工把所选文件复制/替换为
   `think_budget_vllm_adapter/config/budget_config.yaml`，再启动服务；不修改
   `unified_config.py` 或 `BudgetController`，也不增加 `model_policy_id`；
5. 实验 manifest 记录所选源 YAML 的文件名和 SHA256。若启动日志出现
   `TB_WARNING event=config_fallback`，或首批 telemetry 的 13 组签名与所选文件不一致，
   本轮立即判为配置错误；现有 `policy_version` 和完整 BudgetPlan telemetry 足以审计
   实际值，无需新增策略身份字段。

人工替换会把“选错权重配置”的风险留给部署流程，因此不得同时依靠文件名和记忆判断；
启动前 checksum、YAML 解析和启动后配置签名抽检是必要验收步骤。

### 8.3 fixed_v4 候选

完整计算和 13 组参数见
[`fixed_v4_quant_post_train_candidate_plan.md`](../../../debug/fixed_v4_quant_post_train_candidate_plan.md)，
实际请求块已追加到 [`docs/debug/fixed_param`](../../../debug/fixed_param/) 的 13 个同名
TXT，统一标签为 `fixed_v4_quant_post_train`。

- KPI、事件自闭环、投诉分类中英文：原样保留 run_08 已通过质量门的参数；
- 白盒单步、机器数据分析、NetQAD 单步：首次 probe 推迟到量化增训 Vanilla 的 T50，
  hard budget 取 P95；
- 白盒整体、NetQAD 规划、告警建议/结论、投诉工具：首次 probe 推迟到 P75，hard
  budget 取 P95；
- 长文本根因推理：首次思考和 hard budget 取估计 Vanilla 最大思考长度向上取整后的
  1,450，诊断保留完整思考后控制器是否仍会损害质量。

v4 是待测候选，不应提前写成最终 YAML。

## 9. 下一轮实验

1. **执行 fixed_v4 全集**：13 个场景都显式复制各自 TXT 中的
   `fixed_v4_quant_post_train` 七行参数，history/difficulty 继续关闭；所有请求必须命中
   fixed 分支，实际 BudgetPlan 与输入逐字段一致。
2. **先质量后性能**：正确数不低于同权重 Vanilla；无严格候选时最多少 1 题；
   同一质量层才选择 E2E 更低的参数。每个 scene 少 2 题及以上仍直接失败。
3. **只重开失败 scene**：若 v4 恢复质量但无正时延收益，在 T50～P75 区间局部二分；
   若仍少 2 题以上，继续增加思考没有依据时应停止该 scene 搜索。长文本 v4 若保留完整
   思考仍双退化，说明问题不只是预算太小。
4. **生成独立 YAML**：仅在 13 个场景都有入选参数后，从当前 YAML 复制生成一份量化
   增训专用 YAML，替换 13 个产品 scene 预算；由人工在部署时选择替换，不增加代码选择
   逻辑。
5. **最终只开开关复验**：固定模型/量化/增训 checkpoint、adapter/server commit、
   sampling/seed、并发、到达节奏、cache 和 Judge，上传实验 `manifest.json`；逐条审核
   Vanilla-only 回退中的 hard failure，并记录人工选择的 YAML 名称和 SHA256。

本轮属于原 13 场景 search 集上的模型迁移校准，只能证明旧配置不适配以及新配置搜索的
起点，不能替代产品 holdout 或通用/OOD 泛化验收。

## 10. 证据强度与变更记录

- 样本闭合、E2E、Token、质量、路由、预算签名和 phase 账本均为**实测**；
- “当前实现固定加载一个默认 YAML 路径，人工替换无需增加模型选择器”由当前源码
  确定，属于**源码确定**；
- fixed_v4 的思考分位数来自同 run phase 校准估计，候选表现仍为**待实验**；
- 量化和增训分别造成多少变化、九个失败 scene 的最终最优参数为何，当前证据不足。

| 日期 | 变更 |
| --- | --- |
| 2026-08-12 | 初版：完成量化增训权重 Vanilla/DTB 的 633 请求配对、配置审计、质量与 phase 账本，并决定新增模型专用预算配置。 |
| 2026-08-12 | 按人工决策取消预算策略身份、运行时模型选择器和动态旁路方案；最终只新增一份独立 YAML，由人工选择替换。基于 run_08 构造 13 场景 fixed_v4。 |
