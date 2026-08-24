# run_11：量化增训权重 fixed_v4 + guided fresh-final 全集复验

## 0. 结论

本轮在 W8A8 量化、特定场景思维链压缩语料增训权重上，对 13 个产品测试集显式传入
fixed_v4 参数，并使用 `dtb-algorithm-v3` 的 guided fresh-final 行为，共完成 633 条请求。
主基线为 [`run_08_quant&post_train`](../run_08_quant&post_train/RESULT.md) 中同权重的 633 条
Vanilla。

- **实测——answer cue 已解决本轮空 final。** run_08 旧 DTB 和 run_09 fixed_v4 分别有
  25/633、23/633 条 `</think>` 后无最终答案；run_11 为 **0/633**。Telemetry 显示
  309 个 `confidence_accepted` 和 9 个 `budget_exhausted` 请求的 final prompt 都增加了
  **24 token**，与目标 cue 完全一致；315 个模型主动 `</think>` 请求未走该 cue。
- **实测——质量已大幅恢复，但逐场景质量门仍差一个场景。** run_11 为 **587/633**，
  比 Vanilla 的 586/633 多 1 题；但逐场景只有 7 个严格非劣、5 个少 1 题，NetQAD E2E
  单步规划为 78/80，比 Vanilla 少 2 题，超过预声明容差。其他场景的净增益不能抵消该缺口。
- **实测——新增两条 final 截断 hard failure。** `TC00000069` 和 `YH0032` 的 final phase
  都生成到 2,048 token 后以 `length` 结束，客户端 JSON 被截断；两条均为 Vanilla 正确、
  run_11 错误。因此 hard-failure 门失败，不能只按 587/633 宣布质量通过。
- **实测——phase-on E2E 有正收益，但不能作为正式关闭态排名。** 请求加权平均 E2E 从
  77.1998 s 降到 71.0501 s，改善 **6.1497 s / 7.97%**；13 场景等权宏平均 paired gain
  为 **7.0278 s / 9.33%**。7/13 个场景平均更快，363/633 个请求更快；全体 P95 仅改善
  0.12%。本轮开启 phase telemetry，而基线是原生 Vanilla，且两轮都缺 manifest，故这些
  数字是候选实测，不是最终可发布性能结论。
- **阶段判断——guided finalization 目标达成，量化权重第一阶段尚未收口。** 下一步不应再
  修改 answer cue；先消除 2,048-token final 截断，再只对 5 个有明确质量—时延交叉点的
  scene 做有界 fixed_v5，最后以 telemetry off 的自动配置全集完成正式验收。

## 1. 输入、实验身份与证据边界

权威输入：

- run_11 客户端汇总：[`test_result/_SUMMARY.csv`](test_result/_SUMMARY.csv)；
- run_11 客户端明细：13 个 `test_result/dtb_*.csv`；
- 请求与日志绑定：[`extraction_manifest.json`](extraction_manifest.json)、
  [`request_index.csv`](request_index.csv)；
- run_11 请求与 phase：`extracted/<result_stem>/requests.jsonl`、`phase_summary.csv` 和
  `controller_phases.jsonl`；
- 同权重 Vanilla：[`run_08_quant&post_train`](../run_08_quant&post_train/RESULT.md)；
- 同 fixed_v4 的旧 finalization 对照：[`run_09_fixed_v4`](../run_09_fixed_v4/RESULT.md)。

用户声明 run_11 继续使用相同的量化增训权重和 fixed_v4 参数。Run_11 没有上传
`manifest.json`、原始 `server.log` 或 metrics；run_08 也没有独立 manifest。因此本文可以报告
请求级实测差异，并用已提取 phase 审计 run_11 的实际路径，但不能独立证明 checkpoint、
tokenizer、sampling/seed、并发、到达节奏、cache 状态和服务器 commit 跨 run 完全一致。

本轮 metrics 若覆盖每个数据集后的 Judge 请求，确实不能作为被测 workload 的 counter delta。
缺失 metrics 不影响客户端 E2E、Judge 和请求级 phase 账本本身，但失去了 vLLM 全局
prompt/generated/cache counter 的交叉校验。正式 E2E 仍需使用 `OBSERVABILITY.mode=off` 的
同身份 run；本轮 phase-on E2E 只用于筛选候选。

## 2. 完整性、配对与裁判审计

| 检查项 | 结果 |
| --- | ---: |
| run_11 结果 CSV / scene / 客户端行 | 13 / 13 / 633 |
| 非空且 run 内唯一 request_id | 633/633 |
| server request / DTB phase 覆盖 | 633/633 / 633/633 |
| 选中 / 排除 phase | 2,492 / 0 |
| 与 run_08 Vanilla 的 QA_ID 集合一致 | 13/13 |
| 同 QA_ID 问题、参考答案一致 | 633/633 |
| 重复 QA_ID / 缺失配对 | 0 / 0 |
| 客户端错误 / 空模型回答 | 0 / 0 |
| Judge 方法 | 625 `llm_judge` / 8 `deterministic_exact` |
| Judge 字面量 | 587 `True` / 46 `False` / 0 unresolved |
| `</think>` 后空 final | 0/633 |
| 每条响应的 `</think>` 数 | 633/633 恰好 1 个 |
| `<tool_call>` 起止标记计数不等 | 0/633 |

提取器只使用 633 个结果 request_id；`extraction_manifest.json` 中没有排除 phase，说明 Judge
请求未形成混入被测目录的 DTB phase。由于原始 `server.log` 未上传，本地不能重新提取或检查
非 DTB 的 Judge 日志，但这不改变现有 633 个白名单请求的归属闭合。

自动 Judge 全部有效，本报告没有人工覆盖。工具标记数量扫描只能排除明显的不配对，不能替代
工具参数 JSON、关键事实和业务语义的人工 hard-failure 审核。

## 3. 实际分支、配置和 finalization 行为

### 3.1 路由与 fixed 参数来源

| telemetry 项 | 结果 |
| --- | ---: |
| `classification_source=latest_user_message` | 633/633 |
| `scene_matched=true` | 633/633 |
| `decision_branch=fixed` | 633/633 |
| history 非空 / difficulty 非空 | 0 / 0 |
| 每个 scene 的预算签名数 | 1 |
| `policy_version` | `dtb-algorithm-v3`（633/633） |
| 六个预算字段来源 | 对应 `fixed_*`（各 633/633） |

本轮能证明分类标签仍正确、fixed 参数确实生效，但 fixed 覆盖了 scene 配置，因此不能证明
量化权重的独立 YAML 已能在“只传开关”时自动恢复这些预算。

四维预算顺序为 `(check_length, min_think_budget, num_thinking, think_stride)`：

| scene | N | 四维预算 | confidence | hard limit |
| --- | ---: | --- | ---: | ---: |
| `react_whitebox_single_step` | 79 | `(750, 750, 5, 100)` | 0.95 | 1,200 |
| `react_whitebox_overall_planning` | 16 | `(1550, 1550, 3, 150)` | 0.95 | 1,900 |
| `react_machine_data_analysis` | 29 | `(950, 950, 3, 100)` | 0.95 | 1,250 |
| `kpi_configuration_assurance` | 9 | `(500, 500, 4, 300)` | 0.93 | 1,600 |
| `netqad_e2e_single_step` | 80 | `(950, 950, 2, 100)` | 0.95 | 1,100 |
| `netqad_e2e_planning` | 101 | `(2100, 2100, 3, 200)` | 0.95 | 2,550 |
| `event_self_closure` | 25 | `(700, 700, 3, 350)` | 0.93 | 1,700 |
| `alarm_diagnosis_advice` | 11 | `(1750, 1750, 3, 150)` | 0.95 | 2,100 |
| `alarm_diagnosis_conclusion` | 13 | `(1900, 1900, 1, 200)` | 0.95 | 2,000 |
| `long_context_root_cause` | 47 | `(1450, 1450, 1, 100)` | 0.95 | 1,450 |
| `complaint_tool_use` | 39 | `(1250, 1250, 3, 100)` | 0.95 | 1,550 |
| `complaint_classification_zh` | 164 | `(400, 400, 3, 200)` | 0.93 | 1,000 |
| `complaint_classification_en` | 20 | `(400, 400, 4, 200)` | 0.93 | 1,050 |

### 3.2 Guided fresh-final 命中证明

| `final_answer_reason` | 请求 | 接受轮次/行为 | 反推 final 后缀 |
| --- | ---: | --- | ---: |
| `model_think_end` | 315 | 模型主动结束思考 | 不追加 cue；边界账本差为 -4～0 token |
| `confidence_accepted` | 309 | round 1/2/3/4 = 268/32/8/1 | **309/309 为 24 token** |
| `budget_exhausted` | 9 | 未接受 probe 或命中 hard limit | **9/9 为 24 token** |

后缀 token 由 `final.prompt_tokens - decision.prompt_tokens - visible_think_tokens` 反推。
controller-forced 的 318 条全部为 24 token，证明 `confidence_accepted` 不再使用裸
`</think>`。315 条模型原生边界中 313 条为 -1/0，另 2 条为 -4/-3；这些差值来自
stop-boundary token 归属，不表示存在负 prompt。

Run_11 没有 final phase 只生成 1～2 token 的旧失败签名。631 条 final 正常 `stop`；另 2 条
达到 2,048 token 后 `length`，见第 6 节。

## 4. Run_08 Vanilla → run_11 的逐场景结果

`E2E gain = Vanilla - run_11`，正值表示 run_11 更快。Token 是客户端 response completion
token，不包含 hidden probe。质量层按每个 scene 独立判断：正确数不低于 Vanilla 为严格；
严格候选不存在时少 1 题可接受；少 2 题及以上失败。

| 场景 | N | Vanilla E2E | run_11 E2E | E2E gain | 可见 Token 降幅 | 正确数 V→11 | 质量层 | 更快请求 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| ReAct 白盒单步规划 | 79 | 52.5016 s | 56.9346 s | -4.4331 s / -8.44% | 5.72% | 71→72 | 严格（+1） | 24/79 |
| ReAct 白盒整体规划 | 16 | 102.2725 s | 98.8128 s | +3.4597 s / +3.38% | 4.47% | 12→11 | 少 1 题 | 9/16 |
| ReAct 白盒机器数据分析 | 29 | 66.0901 s | 72.6309 s | -6.5408 s / -9.90% | 4.70% | 26→28 | 严格（+2） | 11/29 |
| KPI 配置保障 | 9 | 77.3798 s | 43.3649 s | +34.0149 s / +43.96% | 54.39% | 9→8 | 少 1 题 | 9/9 |
| NetQAD E2E 单步规划 | 80 | 61.0197 s | 63.0594 s | -2.0397 s / -3.34% | 2.56% | 80→78 | **失败（-2）** | 30/80 |
| NetQAD E2E 规划模块 | 101 | 126.8973 s | 126.6463 s | +0.2511 s / +0.20% | 2.98% | 100→99 | 少 1 题 | 51/101 |
| 事件自闭环 | 25 | 108.9606 s | 68.8516 s | +40.1091 s / +36.81% | 40.68% | 23→23 | 严格；含 1 条截断 hard failure | 25/25 |
| 告警诊断建议总结 | 11 | 109.3083 s | 109.2324 s | +0.0759 s / +0.07% | 6.91% | 10→9 | 少 1 题；含 1 条截断 hard failure | 5/11 |
| 告警诊断结论总结 | 13 | 104.9861 s | 110.5067 s | -5.5206 s / -5.26% | 3.48% | 13→12 | 少 1 题 | 2/13 |
| 长文本根因推理 | 47 | 95.5431 s | 97.8647 s | -2.3215 s / -2.43% | 0.61% | 47→47 | 严格 | 18/47 |
| 投诉助理工具调用 | 39 | 71.1545 s | 71.3053 s | -0.1508 s / -0.21% | 3.82% | 37→37 | 严格 | 18/39 |
| 投诉分类中文 | 164 | 56.5358 s | 37.7649 s | +18.7709 s / +33.20% | 46.32% | 138→143 | 严格（+5） | 145/164 |
| 投诉分类英文 | 20 | 47.1800 s | 31.4931 s | +15.6869 s / +33.25% | 47.89% | 20→20 | 严格 | 16/20 |

总体汇总：

| 指标 | Run_08 Vanilla | run_11 | 收益/变化 |
| --- | ---: | ---: | ---: |
| 总 E2E | 48,867.4710 s | 44,974.7325 s | -3,892.7385 s |
| 请求加权平均 E2E | 77.1998 s | 71.0501 s | **6.1497 s / 7.97%** |
| 13 场景等权宏平均 paired gain | — | — | **7.0278 s / 9.33%** |
| 全体 P50 / P95 | 65.7183 / 138.6804 s | 63.4365 / 138.5138 s | 改善 3.47% / 0.12% |
| response completion token | 834,888 | 707,265 | -127,623 / 15.29% |
| 正确数 | 586/633 | 587/633 | +1 / +0.16 pp |
| 场景等权正确率 | 93.50% | 91.46% | -2.03 pp |
| 配对请求更快 | — | 363/633 | 57.35% |

正宏平均主要由 KPI、事件自闭环和投诉分类中英文四个大收益点贡献；六个场景平均 E2E
仍回退。P95 几乎不变，说明本轮还不能声称尾时延已经优化。

## 5. 质量转移与质量门

| 质量状态 | 场景数 | 场景 |
| --- | ---: | --- |
| 严格非劣 | 7 | 白盒单步、机器数据、事件、长文本、投诉工具、投诉分类中英文 |
| 少 1 题 | 5 | 白盒整体、KPI、NetQAD 规划、告警建议、告警结论 |
| 少 2 题及以上 | 1 | NetQAD 单步（-2） |

请求级转移为 22 条 Vanilla-only、23 条 run_11-only，净增 1 题。微平均看似通过，但
投诉分类中文单场景净增 5 题掩盖了若干小场景的下降；按既定规则不能用它抵消 NetQAD
单步的两题缺口。

NetQAD 单步的两条回退为：

- `TC00000029`：`confidence_accepted` round 1；Judge 认为没有识别“等待 10 分钟”是人工/
  时间条件，错误地继续自动执行；
- `TC00000068`：`model_think_end`；Judge 认为跳过当前思路第一步，直接使用后续分支信息。

第一条可能受首次 probe 时机影响；第二条在模型主动结束思考后进入 final，不会因提高
confidence threshold 或增加 probe 轮次而直接改变。因此下一轮不能假设单纯扩大 fixed budget
一定能同时修复两条。

## 6. Final 协议与 hard failure

### 6.1 空 final 修复通过

| variant | `</think>` 后空 final | 备注 |
| --- | ---: | --- |
| run_08 Vanilla | 0/633 | 原生单次生成 |
| run_08 旧 DTB | 25/633 | 裸 close 的旧行为 |
| run_09 fixed_v4 | 23/633 | fixed_v4 + 裸 close |
| run_11 fixed_v4 + v3 cue | **0/633** | 318 个 controller-forced final 使用 24-token cue |

这项结果与 48 条定向集的 8→0 一致，足以停止继续改写 cue。它尚不等于“final 协议全部
通过”，因为本轮暴露了另一类长度截断。

### 6.2 两条 2,048-token final 截断

| scene / QA_ID | request_id | final reason | final phase | 后果 |
| --- | --- | --- | --- | --- |
| 事件自闭环 / `TC00000069` | `chatcmpl-98976f1ca8584bba` | `confidence_accepted` round 1 | 2,048 token / `length` | JSON 截断，Vanilla 对→run_11 错 |
| 告警建议 / `YH0032` | `chatcmpl-a4f7763591260738` | `confidence_accepted` round 1 | 2,048 token / `length` | 推荐字段截断，Vanilla 对→run_11 错 |

**实测**：两条都不是空 final，而是生成到同一 2,048-token 上限后被截断；Judge 原因也直接
指出 JSON/文本末尾不完整。

**源码确定的候选原因**：当前仓库 `DtbAlgorithmConfig.final_answer_max_tokens=2048`，
`_FinalAnswerState` 用 `min(remaining_context, final_answer_max_tokens, request limit)` 作为 final
上限。该实现会给原生请求额外增加一个 2,048-token 硬帽。由于 run_11 缺 manifest，不能独立
证明服务器文件逐字一致，但 telemetry 的精确 2,048-token `length` 与当前源码完全吻合。

这两条属于新增结构/终止 hard failure，必须先修复。仅把它们在 Judge 中计错不等于协议门
通过。

## 7. Phase 账本与模型工作量

| phase | calls | wall time | wall 占比 | generated token | token 占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `initial_think` | 633 | 36,725.705 s | 81.77% | 592,497 | 82.06% |
| `iterative_think` | 142 | 1,441.453 s | 3.21% | 20,264 | 2.81% |
| `confidence_probe` | 451 | 1,239.885 s | 2.76% | 14,157 | 1.96% |
| `final_answer` | 633 | 5,495.954 s | 12.24% | 95,069 | 13.17% |
| `decision` | 633 | 10.697 s | 0.02% | 0 | 0% |
| **合计** | **2,492** | **44,913.694 s** | **100%** | **721,987** | **100%** |

账本闭合：

- request ledger 为 visible thinking 612,761、hidden probe 14,157、final answer 95,069，
  合计 721,987，与 phase generated token 完全一致；
- visible thinking + final answer 为 707,830，比客户端 completion token 707,265 多 565 / 0.080%，
  属于 usage/tokenizer 边界差；
- phase wall 比客户端总 E2E 少 61.038 s / 0.136%；
- 全部 1,859 个模型生成 phase 都有 queue/prefill/decode，合计约 0.022 / 1,622.673 /
  42,887.726 s；缺失 metrics 只影响全局 counter 交叉校验；
- 将 hidden probe 计入后，总模型生成量相对 Vanilla 的 834,888 token 减少 112,901 /
  **13.52%**，低于 response usage 显示的 15.29%；hidden probe 占总模型生成量 1.96%。

### 7.1 为什么不能把 run_09 → run_11 全部归因于 cue

| variant | 平均 E2E | response token | 正确数 | 空 final |
| --- | ---: | ---: | ---: | ---: |
| run_08 Vanilla | 77.1998 s | 834,888 | 586 | 0 |
| run_08 旧 DTB | 65.7576 s | 603,704 | 547 | 25 |
| run_09 fixed_v4 | 82.7154 s | 802,082 | 507 | 23 |
| run_11 fixed_v4 + v3 cue | 71.0501 s | 707,265 | 587 | 0 |

Run_11 相比 run_09 的 E2E、质量和工作量都显著改善，但 cue 不是唯一变化证据：

- run_09 有 2,957 个模型 `generate` call，run_11 只有 1,859 个，减少 1,098 / 37.13%；
- run_09 总模型生成 830,198 token，run_11 为 721,987，减少 108,211 / 13.03%；
- run_11 有 315 个 `model_think_end` 直接进入 final，远少于 run_09 的 iterative/probe 工作量；
- 两轮没有 manifest，生成随机性、负载和重构后状态机/parser 行为无法完全隔离。

因此可以把“24-token cue 消除了本轮空 final”标为强实测，但不能把正确数 +80 或 E2E
改善 14.10% 全部写成 cue 的因果收益。

## 8. 当前实验进展

| 目标 | 状态 | 依据 |
| --- | --- | --- |
| guided cue 覆盖 `confidence_accepted` | **通过** | 309/309 后缀为 24 token |
| 全集无空 final | **通过本轮** | 0/633；无 1～2 token fresh-final 失败签名 |
| fixed 参数执行与分类标签 | **通过** | 633/633 fixed，scene 全命中，13 个签名一致 |
| 每场景质量门 | **未通过** | NetQAD 单步少 2 题 |
| 不新增 hard failure | **未通过** | 2 条 final `length` 截断 |
| 正宏平均 E2E | **候选通过** | phase-on 宏平均 +9.33%，不是 off 正式结果 |
| 自动配置 | **未验证** | 本轮显式传入 fixed，不是只传开关 |

结论是：**finalization 兼容性修复完成了第一层目标，但量化权重的可发布自动策略仍未完成。**
不能生成最终 YAML，也不能把 run_11 作为正式性能基线。

## 9. 下一步实验方向

### 9.1 P0：先消除 final 的额外 2,048-token 硬帽

先做单变量协议实验，不调整任何 fixed 参数：

1. 让 final child 使用调用方剩余 completion budget，而不是再被控制器的 2,048-token 上限
   截断；短期可用 `final_answer_max_tokens: 4096` 验证，最终实现应与原生请求上限语义一致；
2. 定向重放 `TC00000069`、`YH0032` 至少两轮，要求 final 不以 2,048 `length` 截断、JSON
   完整、无第二个 `</think>`、客户端错误为 0；
3. phase 增加或确认可审计的 `answer_limit`，避免以后只能从 generated token 反推；
4. 用 run_10 的 48 条 no-final 集做一次 smoke，确认修复长度上限没有破坏 cue 的 0 空 final。

这个改动只提高允许的最大生成长度；正常提前 stop 的请求不会因此必然增加 token。它应作为
统一 DTB final 契约修复，不做量化权重专属算法分支。

### 9.2 P1：只做 5 个 scene 的 fixed_v5 有界搜索

完成 P0 后，不再跑 13 场景大网格。建议只测试以下单个候选：

| scene | run_11 状态 | 下一候选 | 决策目的 |
| --- | --- | --- | --- |
| 白盒单步 | 质量 +1，但 E2E -8.44% | run_08 旧预算 `(450,450,4,200), p=.93, hard=1100` + v3 cue | 旧点快 4.61%，且旧轮 2 条空 final；验证 cue 后能否跨质量门 |
| 机器数据 | 质量 +2，但 E2E -9.90% | 中点 `(750,750,3,100), p=.95, hard=1050` | 在旧点快但少 2 题、v4 慢但多 2 题之间找唯一交叉点 |
| NetQAD 单步 | 少 2 题且 E2E -3.34% | run_08 旧预算 `(300,300,3,300), p=.93, hard=1050` + v3 cue | 旧点快 1.80% 且有 1 条空 final；若仍少 2 题则停止搜索并 native |
| 投诉工具调用 | 质量严格但 E2E -0.21% | run_08 旧预算 `(1000,1000,3,250), p=.93, hard=1650` + v3 cue | 旧点快 2.75% 且 3 条空 final，最可能在修复后得到正收益质量点 |
| 告警结论 | 少 1 题但 E2E -5.26% | 中点 `(1600,1600,2,200), p=.95, hard=2000` | 在旧点快 12.73%/少 3 题与 v4 慢/少 1 题之间验证一次交叉点 |

每个候选仍按“hard failure → 严格质量层 → 一题容差层 → E2E”顺序裁决。建议这一轮使用
`OBSERVABILITY.mode=off` 做请求级排序；另用每候选少量请求做独立 phase audit，避免 telemetry
开销再次进入候选排名。

### 9.3 P2：冻结、native 与最终自动全集

- 暂时冻结当前参数：KPI、事件自闭环、投诉分类中英文；事件冻结以 P0 两条截断修复为前提；
- 白盒整体当前可作为一题容差候选，但 +3.38% 需 telemetry-off 确认；
- NetQAD 规划和告警建议收益仅 +0.20%/+0.07%，长文本与 Vanilla 比还慢 2.43%；这些 scene
  优先使用真正的 scene-level native bypass，不用超大预算伪装原生；
- 若 P1 的任一 scene 找不到质量合格且正收益的点，同样转 native，不继续扩大 fixed 网格。

收口后按既定决策生成一份人工选择的量化增训 YAML。若需要 scene-level native，YAML 只增加
最小的 `native_bypass` 语义，不引入模型策略身份、动态选择器或请求级模式。

最终全集应包含：

1. 同一实验身份的新 Vanilla 和“只传 `enable_think_budget=true`”自动配置，各 633 条；
2. 正式性能使用 telemetry off；另以同配置 phase audit 验证 scene、预算和 native 分支；
3. 每场景严格优先、无严格候选才允许少 1 题，0 个 scene 少 2 题；
4. 0 空 final、0 final length 截断、0 工具/解析 hard failure；
5. 通过质量后再以 13 场景等权宏平均 paired E2E gain 决定是否达成量化权重阶段一目标；
6. 补齐 manifest；metrics 若无法在 Judge 前截断采集，可以明确缺失，不能上传受 Judge 污染的
   counter 后再选择性归因。

## 10. 证据强度与变更记录

- 633 对 QA_ID、E2E/Token/Judge、0 空 final、2 条截断、实际 fixed 配置、final reason、
  24-token 后缀和 phase 账本为**实测**；
- run_11 使用相同量化增训权重和 fixed_v4 为**用户提供的实验事实**；缺 manifest，未独立核对
  checkpoint 和服务 commit；
- 当前仓库的 2,048-token final cap 与两条截断轨迹一致为**源码确定的候选原因**，服务器源码
  未用 manifest/SHA 独立闭合；
- telemetry off 后仍有 7.97%/9.33% 微/宏 E2E 收益、P1 候选能跨质量门均为**待实验**。

| 日期 | 变更 |
| --- | --- |
| 2026-08-24 | 初版：完成 run_11 与 run_08 Vanilla 的 633 请求配对、guided cue/fixed 分支审计、逐场景质量与 E2E、phase 账本；确认空 final 归零，同时发现 NetQAD 单步两题缺口和两条 2,048-token final 截断，并规划 P0 final cap、P1 有界 v5、P2 native/自动全集。 |
