# run_09_fixed_v4：量化增训权重 fixed_v4 搜索结果

## 0. 结论

本轮在与 [`run_08_quant&post_train`](../run_08_quant&post_train/RESULT.md) 相同的 W8A8
量化、特定场景思维链压缩语料增训权重和 13 个测试集上，显式传入
[`fixed_v4_quant_post_train`](../../../debug/fixed_v4_quant_post_train_candidate_plan.md)
的 13 组 fixed 参数，共完成 633 条请求。

- **实测——fixed 参数执行正确**：633/633 个请求进入 `decision_branch=fixed`，六个预算
  字段都来自对应 `fixed_*` 请求字段；633/633 个请求仍命中预期 `scene`，history 和
  difficulty 均未参与。当前问题不是分类或配置未生效。
- **实测——v4 性能失败**：请求加权平均 E2E 为 82.7154 s，相比同权重 Vanilla 的
  77.1998 s **增加 5.5156 s / 7.14%**；场景等权宏平均 paired E2E gain 为
  **-4.0556 s**。13 个场景中只有 4 个比 Vanilla 更快，且均是未修改参数的
  carry-forward 场景。
- **实测——v4 质量明显失败**：人工把唯一 `judge_invalid` 样本 `YH0023` 判为错误后，
  正确数为 507/633，较 Vanilla 的 586/633 少 79 题，也较旧 DTB 的 547/633 少 40 题。
  只有 2 个场景严格非劣、3 个场景少 1 题，另 8 个场景少 3～20 题。
- **实测——存在协议级 hard failure**：Vanilla 没有“`</think>` 后无最终答案”的样本，
  旧 DTB 有 25 条，v4 仍有 23 条。v4 的 23 条中有 10 条被 Judge 错记为正确、1 条
  Judge 无效；它们的 `final_answer_tokens` 都只有 1～2，且 0/23 命中 think hard
  limit。参数搜索不能掩盖这类终止路径错误。
- **实测——v4 的保守化假设不成立**：9 个修改过预算的场景全部比旧 DTB 更慢，合计
  正确数反而少 39 题；推迟第一次 probe 并未表现出“思考越长，质量单调恢复”。
- **决策**：不生成量化增训权重的最终 YAML，不继续直接做 13 场景 v5 全量参数搜索。
  先修复并验证 final-answer 非空不变量；随后只对 3 个可能因空答案而跨过质量门的旧预算
  和 1 个存在质量/时延交叉点的机器数据场景做有界复验。长文本根因推理停止 fixed 搜索，
  其余已被 v4 证伪的场景优先规划 scene 级 native bypass。

## 1. 输入、实验身份与证据边界

权威输入：

- 客户端汇总：[`test_result/_SUMMARY.csv`](test_result/_SUMMARY.csv)；
- 客户端明细：13 个 `test_result/dtb_*.csv`；
- 请求与日志绑定：[`extraction_manifest.json`](extraction_manifest.json)、
  [`request_index.csv`](request_index.csv)；
- 已提取 telemetry：`extracted/<result_stem>/requests.jsonl`、`phase_summary.csv`、
  `controller_phases.jsonl`；
- 同权重主基线和旧预算对照：[`run_08_quant&post_train`](../run_08_quant&post_train/RESULT.md)。

本目录未上传 `server.log`，但 633 个客户端 request_id 均已绑定到提取产物，3,590 条
phase 全部被选中且没有排除记录，因此足以审计本轮执行路径。目录仍没有独立
`manifest.json`，缺少精确 checkpoint/model ID、量化配置、服务和 adapter commit、
sampling/seed、并发/到达节奏及 prefix-cache 状态。本文依据用户声明把 run_08 与
run_09 视为同权重连续搜索；跨轮差异仍可能包含生成随机性和服务负载噪声。

## 2. 完整性、配对与裁判审计

| 检查项 | 结果 |
| --- | ---: |
| 结果 CSV / 场景 / 客户端行 | 13 / 13 / 633 |
| 非空且 run 内唯一 request_id | 633/633 |
| server request / TB phase 覆盖 | 633/633 / 633/633 |
| 选中 / 排除 phase | 3,590 / 0 |
| 与 run_08 Vanilla 的 QA_ID 集合一致 | 13/13 |
| 同 QA_ID 问题、参考答案一致 | 633/633 |
| 客户端错误 / 空输出 | 0 / 0 |
| Judge 方法 | 625 `llm_judge` / 7 `deterministic_exact` / 1 `judge_invalid` |
| 原始 Judge 结果 | 507 True / 125 False / 1 invalid |

唯一无效裁判为告警诊断建议总结的 `YH0023`，request_id 为
`chatcmpl-b940714062413cf2`。响应在 `</think>` 处结束，没有最终 JSON；不论裁判模型
为何返回非法 score，该条都应按错误和 hard failure 处理。因此本报告使用 507/633，
而不是 `_SUMMARY.csv` 中排除该条后得到的 507/632。

## 3. 路由、分支与实际预算

### 3.1 控制路径

| telemetry 项 | 结果 |
| --- | ---: |
| `classification_source=latest_user_message` | 633/633 |
| 预期 scene / `scene_matched=true` | 633/633 / 633/633 |
| `decision_branch=fixed` | 633/633 |
| history 非空 / difficulty 非空 | 0 / 0 |
| 每个结果文件的预算签名数 | 1 |
| `policy_version` | `think-budget-performance-v1` |

`check_length`、`min_think_budget`、`num_thinking`、`think_stride`、confidence 和 hard
limit 的 `source_by_field` 分别为 `fixed_check_length`、`fixed_min_think_tokens`、
`fixed_num_thinking`、`fixed_think_stride`、`fixed_prob_th`、`fixed_budget_tokens`，
每项都是 633/633。分类器虽然命中正确，但 fixed 覆盖了场景预算，因此本轮只能证明
分类标签正确，不能作为“仅开启特性时自动选中量化权重预算”的最终验证。

### 3.2 生效预算与停止行为

| scene | N | check/min | stride × num | prob | hard | 平均 probe | 接受 / 未接受 | hard 命中 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `react_whitebox_single_step` | 79 | 750 | 100 × 5 | 0.95 | 1,200 | 1.43 | 76 / 3 | 3 |
| `react_whitebox_overall_planning` | 16 | 1,550 | 150 × 3 | 0.95 | 1,900 | 1.00 | 16 / 0 | 0 |
| `react_machine_data_analysis` | 29 | 950 | 100 × 3 | 0.95 | 1,250 | 1.41 | 26 / 3 | 1 |
| `kpi_configuration_assurance` | 9 | 500 | 300 × 4 | 0.93 | 1,600 | 1.00 | 9 / 0 | 0 |
| `netqad_e2e_single_step` | 80 | 950 | 100 × 2 | 0.95 | 1,100 | 1.11 | 73 / 7 | 6 |
| `netqad_e2e_planning` | 101 | 2,100 | 200 × 3 | 0.95 | 2,550 | 1.04 | 100 / 1 | 1 |
| `event_self_closure` | 25 | 700 | 350 × 3 | 0.93 | 1,700 | 1.04 | 25 / 0 | 0 |
| `alarm_diagnosis_advice` | 11 | 1,750 | 150 × 3 | 0.95 | 2,100 | 1.00 | 11 / 0 | 0 |
| `alarm_diagnosis_conclusion` | 13 | 1,900 | 200 × 1 | 0.95 | 2,000 | 0.54 | 7 / 6 | 6 |
| `long_context_root_cause` | 47 | 1,450 | 100 × 1 | 0.95 | 1,450 | 0.00 | 0 / 47 | 47 |
| `complaint_tool_use` | 39 | 1,250 | 100 × 3 | 0.95 | 1,550 | 1.03 | 39 / 0 | 0 |
| `complaint_classification_zh` | 164 | 400 | 200 × 3 | 0.93 | 1,000 | 1.39 | 146 / 18 | 4 |
| `complaint_classification_en` | 20 | 400 | 200 × 4 | 0.93 | 1,050 | 1.30 | 20 / 0 | 0 |

全体接受轮次为 round 1/2/3/4 = 494/38/13/3，85 条没有接受 probe；其中 53 条
没有 probe。实际参数与 fixed_v4 文档逐字段一致。

## 4. 逐场景配对结果

`E2E gain = Vanilla - v4`，正值表示 v4 更快。质量列依次为
`Vanilla / 旧 DTB / v4`；`YH0023` 已人工计错。Token 是客户端 response completion
token，不含 hidden probe。

| 场景 | N | Vanilla E2E | 旧 DTB E2E | v4 E2E | v4 对 Vanilla gain | v4 对旧 DTB gain | Token V→v4 | 正确数 V/旧/v4 | 质量层 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ReAct 白盒单步规划 | 79 | 52.5016 | 50.0799 | 68.4806 | -15.9790 / -30.44% | -18.4007 / -36.74% | 911.59→1,009.15 | 71/68/54 | 失败（-17） |
| ReAct 白盒整体规划 | 16 | 102.2725 | 96.5518 | 112.0108 | -9.7383 / -9.52% | -15.4590 / -16.01% | 1,667.62→1,764.69 | 12/8/7 | 失败（-5） |
| ReAct 白盒机器数据分析 | 29 | 66.0901 | 60.3857 | 81.5993 | -15.5092 / -23.47% | -21.2136 / -35.13% | 1,119.55→1,154.72 | 26/24/25 | 少 1 题，但无性能收益 |
| KPI 配置保障 | 9 | 77.3798 | 45.3749 | 44.3239 | +33.0559 / +42.72% | +1.0510 / +2.32% | 1,492.56→763.56 | 9/9/9 | 严格 |
| NetQAD E2E 单步规划 | 80 | 61.0197 | 59.9231 | 74.0415 | -13.0218 / -21.34% | -14.1184 / -23.56% | 1,025.55→1,107.53 | 80/78/66 | 失败（-14） |
| NetQAD E2E 规划模块 | 101 | 126.8973 | 105.4834 | 156.9927 | -30.0954 / -23.72% | -51.5093 / -48.83% | 2,146.93→2,468.17 | 100/90/89 | 失败（-11） |
| 事件自闭环 | 25 | 108.9606 | 74.5084 | 71.9431 | +37.0175 / +33.97% | +2.5653 / +3.44% | 2,066.04→1,236.48 | 23/22/22 | 少 1 题 |
| 告警诊断建议总结 | 11 | 109.3083 | 78.2880 | 132.1429 | -22.8346 / -20.89% | -53.8549 / -68.79% | 2,082.73→2,307.73 | 10/5/6 | 失败（-4） |
| 告警诊断结论总结 | 13 | 104.9861 | 91.6207 | 115.5333 | -10.5472 / -10.05% | -23.9126 / -26.10% | 1,992.77→2,076.31 | 13/10/10 | 失败（-3） |
| 长文本根因推理 | 47 | 95.5431 | 109.5761 | 114.8398 | -19.2967 / -20.20% | -5.2637 / -4.80% | 1,501.11→1,750.87 | 47/40/27 | 失败（-20） |
| 投诉助理工具调用 | 39 | 71.1545 | 69.1994 | 89.4968 | -18.3423 / -25.78% | -20.2974 / -29.33% | 1,289.85→1,462.28 | 37/33/33 | 失败（-4） |
| 投诉分类中文 | 164 | 56.5358 | 37.7913 | 38.5205 | +18.0153 / +31.87% | -0.7292 / -1.93% | 929.89→512.88 | 138/141/140 | 严格（+2） |
| 投诉分类英文 | 20 | 47.1800 | 27.7303 | 32.6267 | +14.5533 / +30.85% | -4.8964 / -17.66% | 879.05→457.80 | 20/19/19 | 少 1 题 |

总体汇总：

| 指标 | Vanilla | 旧 DTB | fixed_v4 | v4 对 Vanilla |
| --- | ---: | ---: | ---: | ---: |
| 总 E2E | 48,867.4710 s | 41,624.5493 s | 52,358.8435 s | +3,491.3725 s |
| 请求加权平均 E2E | 77.1998 s | 65.7576 s | 82.7154 s | **回退 7.14%** |
| 13 场景等权宏平均 paired gain | — | +13.3320 s | **-4.0556 s** | 未通过排序指标 |
| 全体 E2E P50 / P95 | 65.7183 / 138.6804 s | 61.9350 / 123.2265 s | 72.0842 / 160.6238 s | 回退 9.69% / 15.82% |
| response completion token | 834,888 | 603,704 | 802,082 | -32,806 / 3.93% |
| 正确数 | 586/633 | 547/633 | 507/633 | -79 / -12.48 pp |
| 场景等权正确率 | 93.50% | 82.04% | 77.76% | -15.74 pp |
| 配对请求更快 | — | 431/633 | 240/633 | — |

v4 相比旧 DTB 的总 E2E 增加 25.79%，response token 增加 32.86%。只有 KPI 和事件
两个 carry-forward 场景平均 E2E 比旧 DTB 更低；投诉中英文使用的也是同一组旧参数，
它们相对旧 DTB 的差异应首先视为跨轮负载/生成波动，而不是参数变化收益。

## 5. 质量门与 hard failure

### 5.1 正确性转移

相对 Vanilla，请求级转移为 98 条“Vanilla 对、v4 错”和 19 条反向改善，净少 79 条；
相对旧 DTB 为 83 条退化、43 条改善，净少 40 条。质量状态为：

| 状态 | 场景数 | 场景 |
| --- | ---: | --- |
| 严格非劣 | 2 | KPI 配置保障、投诉分类中文 |
| 少 1 题 | 3 | 机器数据分析、事件自闭环、投诉分类英文 |
| 少 2 题及以上 | 8 | 其余 8 个场景 |

机器数据分析虽然到达一题容差，但 E2E 比 Vanilla 慢 23.47%，不能入选。按“先质量，
再时延”的参数选择原则，v4 没有新增任何同时满足质量门和正 E2E 收益的 profile。

### 5.2 `</think>` 后无最终答案

对三组 633 条响应做确定性结构检查：

| variant | 无最终答案 | 其中 Judge True / False / invalid |
| --- | ---: | ---: |
| run_08 Vanilla | 0 | 0 / 0 / 0 |
| run_08 旧 DTB | 25 | 8 / 17 / 0 |
| run_09 fixed_v4 | **23** | **10 / 12 / 1** |

v4 的 23 条分布在白盒整体 4、NetQAD 单步 4、NetQAD 规划 7、告警建议 3、告警结论
1、投诉分类中文 3、投诉分类英文 1。telemetry 进一步确认：

- 23/23 的 `final_answer_tokens` 只有 1～2；
- 23/23 都在 1～4 轮 probe 之后进入 final answer，20 条接受了 probe，3 条耗尽轮次；
- 0/23 的 visible think 达到 hard limit，所以不能归因于“给最终答案没有上下文空间”；
- `YH0023` 的 final-answer phase 只生成 2 token，并以 `stop_reason=</think>` 结束。

**源码确定**：[`_FinalAnswerState`](../../../../think_budget_vllm_adapter/deer_think_budget/state.py)
只检查是否收到 vLLM output，把 1～2 个特殊/停止 token 也记作成功；它没有验证
`</think>` 后是否存在非空最终文本。该状态又在已追加 `</think>` 的主 prompt 上发起 fresh
generation，并把 `</think>` 加入 stop。**实测与源码共同支持**“final-answer 非空不变量
缺失”是当前 hard failure 的直接实现缺口；具体采用 answer cue、继续生成还是 native
fallback，仍需对照实验决定。

Judge 能把 10 条空最终答案判为正确，说明语义 Judge 会读取 thought 中的草稿，不能代替
协议检查。后续测试脚本应在调用 LLM Judge 前执行确定性检查：若 `</think>` 后为空，直接
记为错误和 `protocol_no_final_answer`。

## 6. 模型工作量与 phase 账本

| phase | calls | wall time | 账本占比 | generated token | token 占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `initial_think` | 633 | 37,395.827 s | 71.51% | 594,243 | 71.58% |
| `iterative_think` | 980 | 6,537.354 s | 12.50% | 97,927 | 11.80% |
| `confidence_probe` | 711 | 2,185.766 s | 4.18% | 26,790 | 3.23% |
| `final_answer` | 633 | 6,167.918 s | 11.79% | 111,238 | 13.40% |
| `decision` | 633 | 10.677 s | 0.02% | 0 | 0% |
| **合计** | **3,590** | **52,297.543 s** | **100%** | **830,198** | **100%** |

账本闭合情况：

- request ledger 为 visible think 692,170、hidden probe 26,790、final answer 111,238，
  总模型生成量 830,198，与 phase 完全一致；
- phase wall 比客户端总 E2E 少 61.3005 s / 0.117%；
- hidden probe 相当于 response completion token 的 3.34%；
- 客户端看起来比 Vanilla 少 3.93% completion token，但加回 hidden probe 后，总模型
  生成量只比 Vanilla 的原生 completion token 少 4,690 / **0.56%**，不足以覆盖重复
  prefill、probe 和状态切换，因此 E2E 反而回退；
- v4 总模型生成量比旧 DTB 增加 188,057 / 29.29%，主要来自推迟首次 probe 后的
  `initial_think` 增长，而不是 hidden probe。

长文本根因推理的 47 条请求全部在 1,450 visible think token 命中 hard limit、没有
probe，平均 E2E 仍比 Vanilla 慢 20.20%，正确数从 47 降到 27。它直接完成了 v4 文档
预设的控制器适用性诊断：即使不经过 probe，截断思考后再 fresh-generate final answer
仍不能保持该场景质量，因此应按原计划停止该 scene 的 fixed 搜索。

## 7. v4 候选处置

| 候选组 | v4 结果 | 处置 |
| --- | --- | --- |
| KPI、事件 carry-forward | 质量门、时延门均通过且无空最终答案 | 保留为候选，不再改参数 |
| 投诉中英文 carry-forward | 正确数与时延门通过，但本轮出现 3/1 条空最终答案 | 参数暂存；先修终止路径再复验，不能直接冻结 |
| 白盒单步、NetQAD 单步 | v4 质量大幅恶化且时延转负；旧 DTB 分别有 2/1 条空答案 | 丢弃 v4；修复后只复验旧预算一次 |
| 机器数据分析 | v4 恢复到少 1 题，但 E2E 慢 23.47%；旧预算快 8.63% 但少 2 题 | 在旧/v4 之间做一次单变量有界搜索 |
| 投诉工具调用 | v4 不恢复质量且时延转负；旧 DTB 的 3 个空答案足以解释其 4 题缺口中的大部分 | 丢弃 v4；修复后只复验旧预算一次 |
| 白盒整体、NetQAD 规划、告警建议、告警结论 | 推迟至 P75/P95 后仍少 3～11 题，且 E2E 全部转负 | 停止继续向更大预算搜索，优先 native bypass |
| 长文本根因推理 | 无 probe、全体 hard-limit，仍慢 20.20% 且少 20 题 | 立即停止 fixed 搜索，使用 native bypass |

9 个修改参数的场景中，v4 相对旧 DTB只有机器数据和告警建议各多判对 1 题；其余不变
或更差。所有 9 个场景都更慢。因此不能用 v4 数值更新 YAML，也没有证据支持继续按
T50/P75/P95 统一向右搜索。

## 8. 下一步计划

### 8.1 先修复 final-answer 完整性

在继续参数实验前完成最小实现闭环：

1. `_FinalAnswerState` 累积最终可见文本，新增 `final_content_tokens`、
   `final_answer_empty`、`final_answer_retry_count` telemetry；只有 `</think>` 后存在非空
   内容才记 `completion_status=success`。
2. 对“只生成 EOS/stop/`</think>`”的情况比较两种最小方案：在 closed reasoning 后追加
   明确 answer cue 再生成；若仍为空，回到真正的原生单次生成作为兜底。兜底只在异常
   样本触发，成本不能进入普通请求。
3. 补充无模型状态机单测：final phase 首次返回空文本/停止 token、正常 final、重试仍空、
   流式与非流式输出各一组；保证只输出一个 `</think>`，取消和错误仍透传。
4. 测试脚本先做 `protocol_no_final_answer` 确定性判定，再运行语义 Judge，禁止 thought
   中的草稿被当作最终答案。

先只回放本轮 23 个 request_id。验收为 23/23 有非空 final、0 客户端错误、0 重复
`</think>`，telemetry 能区分正常生成、retry 和 native fallback。该小回放只验证实现，
不用于宣称场景质量通过。

### 8.2 有界 fixed_v5，而不是再次全量网格

实现修复通过后只测以下候选：

1. 用 run_08 **旧 DTB 参数**复验白盒单步、NetQAD 单步、投诉工具调用。旧预算分别只差
   3/2/4 题，并有 2/1/3 条空最终答案，同时仍有 4.61%/1.80%/2.75% E2E 收益，是最可能
   因终止修复跨过一题容差的三个点。
2. 机器数据分析在旧预算和 v4 之间只构造一个中间点，并保持其余字段尽量不变，用于判断
   是否存在“质量少 1 题且 E2E 正收益”的交叉点；若仍失败则 native bypass。
3. KPI、事件不改参数；投诉中英文使用原 carry-forward 参数复验终止完整性。
4. 白盒整体、NetQAD 规划、告警建议、告警结论、长文本不再做更保守 fixed；先按 native
   进入最终组合。如果后续确有新增证据，再单独重启搜索。

每个候选仍按顺序验收：正确数不低于 Vanilla；无严格候选时最多只少 1 题；不新增
hard failure；通过后才比较 paired E2E，且必须为正收益。收益近似时选择更少的 profile。

### 8.3 最终配置与全集复验

当前“只新增一份独立 YAML，由人工选择替换”的部署决策可以保留，但若部分已识别 scene
必须走 native，YAML/控制器还需要一个最小的 scene 级 `native_bypass`（或等价布尔字段）。
它不引入模型策略身份、动态路由或 PolicyBundle，只表达该量化增训权重下某个已知 scene
不适用 DTB；不能用超大预算伪装 native。

有界 v5 收口后再生成 `budget_config_quant_post_train.yaml`，随后只做一次 633 条全集
“仅开启 `enable_think_budget=true`、不传 fixed”复验。该轮必须满足：

1. 633/633 scene 分类正确；DTB scene 的预算签名与 YAML 一致，bypass scene 真正进入
   原生单次生成；fixed/history/difficulty 不混入；
2. 每场景和宏平均质量达到既定一题容差，不新增 hard failure；
3. 通过质量门后，唯一排序指标仍是 13 场景等权宏平均 paired E2E gain；
4. 完整记录 response token、visible think、hidden probe、final answer 和 phase wall；
5. 补齐 `manifest.json`，记录 checkpoint、量化配置、YAML SHA256、adapter/server commit、
   sampling、并发和 cache 状态。

## 9. 证据强度与变更记录

- 633 请求配对、E2E、Token、Judge、实际预算、分支、phase 账本和 23 条无最终答案为
  **实测**；
- final-answer 状态缺少非空检查、fresh prompt 已关闭 reasoning、并配置 `</think>` stop
  为**源码确定**；
- answer cue、继续生成或 native fallback 中哪一种能恢复质量和控制成本仍是**待实验假设**；
- run_09 未提供完整 manifest，因此不能把 run_08→run_09 的跨轮噪声全部归因于参数。

| 日期 | 变更 |
| --- | --- |
| 2026-08-13 | 初版：完成 fixed_v4 与同权重 Vanilla、旧 DTB 的 633 请求配对，审计 fixed 分支与 phase 账本，发现 23 条无最终答案，并决定先修终止路径、再做有界 v5 与 scene 级 native 收口。 |
