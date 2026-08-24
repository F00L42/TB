# run_11_fixed_v5：量化增训权重五场景 fixed_v5 复验

## 0. 结论

本轮在 W8A8 量化、特定场景思维链压缩语料增训权重上，使用
`dtb-algorithm-v4` 和 run_11 规划的 5 组 fixed_v5 参数，共完成 240 条请求。质量主基线为
[`run_08_quant&post_train`](../run_08_quant&post_train/RESULT.md) 的同权重 Vanilla；
fixed_v4 工作点参考 [`run_11`](../run_11/RESULT.md)。用户后续补充确认：本轮服务为 **TP8**，
而 run_08 Vanilla 为 **TP4**。

- **实测——五组 fixed_v5 均进入既定质量层。** 五场景合计正确数与 Vanilla 完全相同，
  都是 **227/240**；4 个场景严格非劣，NetQAD 单步为 79/80、少 1 题，属于预声明容差层。
  相比 fixed_v4，白盒单步和机器数据分别少 1、2 题，但仍与 Vanilla 持平；其余三个场景
  分别多 1 题。
- **实测——参数本身产生了可审计的模型工作量收益。** 计入 visible thinking、hidden probe
  和 final answer 后，fixed_v5 总模型生成量为 **219,499 token**，相比 Vanilla 的
  262,737 token 减少 **43,238 / 16.46%**；5 个场景分别减少 8.82%～23.58%。相比 fixed_v4
  也减少 15.43%。这比客户端 `Token数` 更适合作为本轮参数价值证据。
- **实测但不可归因——phase-on E2E 观测到 68.04% 微平均和 69.73% 场景等权收益。**
  240 条中 239 条比 Vanilla 快，平均 E2E 从 62.8569 s 降到 20.0888 s；但 run_11 到本轮的
  phase decode 耗时/生成 token 从约 58.3 ms 降到 17.6 ms，变化远大于 15.43% 的模型工作量
  降幅。更关键的是，本轮 TP8 与 Vanilla TP4 的张量并行度不一致；这 68.04% 是跨 TP 的
  观测差，不能写成 fixed_v5 或 DTB 的因果收益。
- **实测——v4 completion 额度 telemetry 正常，旧截断用例尚未复验。** 240/240 个 final 的
  `answer_limit` 等于 `external_remaining_tokens`，范围为 63,935～65,235；全部自然 `stop`，
  final 最长 238 token，没有 2,048 cap 签名。但本轮不含 run_11 的 `TC00000069`、`YH0032`
  和 run_10 的 48 条 no-final 集，因此不能据此宣布额度协议定向验收完成。
- **决策——停止继续 fixed 网格搜索。** 这 5 组参数已足够进入量化增训权重专用 YAML 候选。
  下一步应完成协议定向回放、同运行时 telemetry-off Vanilla/自动 DTB 全集，然后转向
  “开源/后训练权重 × Vanilla/DTB”的 2×2 受控实验，量化 DTB 与后训练的独立及交互价值。

当前状态可概括为：**fixed_v5 参数可用作配置候选，但量化权重自动策略尚未达到发布验收。**

## 1. 输入、实验身份与证据边界

权威输入：

- 客户端汇总：[`test_result/_SUMMARY.csv`](test_result/_SUMMARY.csv)；
- 5 个客户端明细：`test_result/qp_dtb_*.csv`；
- 请求归属：[`extraction_manifest.json`](extraction_manifest.json)、
  [`request_index.csv`](request_index.csv)；
- 请求与 phase：`extracted/<result_stem>/requests.jsonl`、`controller_phases.jsonl` 和
  `phase_summary.csv`；
- 同权重 Vanilla：run_08 的 5 个对应 `qp_vanilla_*.csv`；
- fixed_v4：run_11 的 5 个对应 `dtb_*.csv` 及 phase 账本。

用户声明本轮继续使用同一量化增训权重、已部署 commit `d659f33e` 的 completion 额度修复，
且服务使用 TP8；run_08 Vanilla 使用 TP4。Telemetry 的 `policy_version=dtb-algorithm-v4` 和
新增额度字段与代码声明一致，但缺少 manifest，无法独立闭合部署参数。

本目录没有 `manifest.json`、原始 `server.log` 或 metrics。已提取请求可以支持 fixed 配置、
phase 和额度账本审计；metrics 缺失不阻塞本轮请求级分析。缺少 manifest 则无法独立闭合
checkpoint/tokenizer、vLLM 与 Ascend 版本、patch/YAML hash、sampling/seed、并发、到达节奏、
MTP、硬件负载和 cache 状态，因此跨 run E2E 只能记为候选实测。

## 2. 完整性、配对与裁判审计

| 检查项 | 结果 |
| --- | ---: |
| 结果 CSV / scene / 客户端行 | 5 / 5 / 240 |
| 非空且 run 内唯一 request_id | 240/240 |
| server request / DTB phase 覆盖 | 240/240 / 240/240 |
| 选中 / 排除 phase | 1,211 / 0 |
| 与 run_08 Vanilla 的 QA_ID 集合一致 | 5/5 |
| 与 run_11 fixed_v4 的 QA_ID 集合一致 | 5/5 |
| 同 QA_ID 问题、参考答案一致 | 240/240 |
| 重复 QA_ID / 缺失配对 | 0 / 0 |
| Judge 方法 / unresolved | 240 `llm_judge` / 0 |
| 客户端错误 / 空模型回答 | 0 / 0 |
| 明显工具标记不配对 | 0/240 |
| final phase `stop` / `length` | 240 / 0 |

客户端 `模型回答` 本轮只保存 final content，不再包含历史 CSV 中拼接的思考文本和
`</think>`；240 条均为非空 final。Telemetry 同时证明 visible thinking 和 reasoning boundary
已进入 parser-facing 额度账本，因此“客户端文本里没有 `</think>`”不能按旧 CSV 口径直接
判为空 final。正式协议验收仍应通过 stream/non-stream parser audit 核对，而不是只扫描该列。

## 3. 实际分支、配置与 completion 额度

### 3.1 fixed_v5 实际配置

| scene | N | `(check,min,num,stride)` | confidence | hard limit |
| --- | ---: | --- | ---: | ---: |
| `react_whitebox_single_step` | 79 | `(450,450,4,200)` | 0.93 | 1,100 |
| `react_machine_data_analysis` | 29 | `(750,750,3,100)` | 0.95 | 1,050 |
| `netqad_e2e_single_step` | 80 | `(300,300,3,300)` | 0.93 | 1,050 |
| `alarm_diagnosis_conclusion` | 13 | `(1600,1600,2,200)` | 0.95 | 2,000 |
| `complaint_tool_use` | 39 | `(1000,1000,3,250)` | 0.93 | 1,650 |

审计结果：

- `decision_branch=fixed`、`scene_matched=true`、
  `classification_source=latest_user_message` 均为 240/240；
- 5 个 scene 各只有一个预算签名，六个参数字段均来自对应 `fixed_*`；
- history、difficulty 均为 0；policy 全部为 `dtb-algorithm-v4`；
- 195 个请求因 `confidence_accepted` 进入 final，40 个为 `model_think_end`，5 个为
  `budget_exhausted`；accepted round 1/2/3/4 为 124/53/15/3。

### 3.2 completion 额度修复的本轮证据

| 字段/行为 | 结果 |
| --- | ---: |
| `completion_token_limit` 覆盖 | 240/240，均为 65,536 |
| final phase 与 request summary 字段一致 | 240/240 |
| `answer_limit == external_remaining_tokens` | 240/240 |
| `answer_limit` 范围 | 63,935～65,235 |
| final generated token 最小 / P95 / 最大 | 69 / 174.35 / 238 |
| final stop / length | 240 / 0 |

这些数据证明 v4 新额度字段在本轮服务器路径中生效，且没有旧 2,048 cap；但所有 final 都远短于
2,048 token，不能替代两个历史长 final 的定向回放，也没有覆盖较小外部 `max_tokens` 的边界。

## 4. 逐场景 paired 结果

`E2E gain = run_08 Vanilla - fixed_v5`，正值表示 v5 更快。E2E 是跨 run phase-on 候选实测；
“总模型 token”对 Vanilla 使用原生 completion token，对 DTB 使用完整 phase 账本，均表示模型
实际生成工作量。

| 场景 | N | Vanilla E2E | fixed_v4 E2E | fixed_v5 E2E | v5 vs V gain | 正确数 V/v4/v5 | 总模型 token V/v4/v5 | v5 工作量降幅 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ReAct 白盒单步 | 79 | 52.5016 s | 56.9346 s | 18.0972 s | 34.4043 s / 65.53% | 71/72/71 | 72,016/71,319/55,032 | 23.58% |
| ReAct 机器数据 | 29 | 66.0901 s | 72.6309 s | 22.3758 s | 43.7143 s / 66.14% | 26/28/26 | 32,467/32,211/29,603 | 8.82% |
| NetQAD E2E 单步 | 80 | 61.0197 s | 63.0594 s | 21.8665 s | 39.1531 s / 64.16% | 80/78/79 | 82,044/81,782/67,517 | 17.70% |
| 告警诊断结论 | 13 | 104.9861 s | 110.5067 s | 23.5831 s | 81.4030 s / 77.54% | 13/12/13 | 25,906/25,288/22,972 | 11.33% |
| 投诉工具调用 | 39 | 71.1545 s | 71.3053 s | 17.6107 s | 53.5438 s / 75.25% | 37/37/38 | 50,304/48,934/44,375 | 11.79% |

总体：

| 指标 | run_08 Vanilla | run_11 fixed_v4 | fixed_v5 | v5 vs Vanilla |
| --- | ---: | ---: | ---: | ---: |
| 总 E2E | 15,085.6571 s | 15,866.3764 s | 4,821.3035 s | -10,264.3536 s |
| 请求加权平均 E2E | 62.8569 s | 66.1099 s | 20.0888 s | **42.7681 s / 68.04%** |
| 5 场景等权相对 E2E gain | — | — | — | **69.73%** |
| 全体 P50 / P95 | 61.4890 / 100.8399 s | 62.7185 / 105.0865 s | 19.1834 / 30.1697 s | 68.80% / 70.08% |
| 配对请求更快 | — | — | 239/240 | 99.58% |
| 正确数 | 227/240 | 227/240 | 227/240 | 0 |
| 总模型生成量 | 262,737 | 259,534 | 219,499 | **-43,238 / 16.46%** |

Fixed_v5 相比 fixed_v4 的观测 E2E 改善为 69.61%，总模型生成量改善为 15.43%，240/240 条
请求更快。E2E 与工作量降幅不成比例，归因限制见第 6.2 节。

## 5. 质量转移与质量门

| 场景 | Vanilla→v5 | 质量层 | 相比 fixed_v4 |
| --- | ---: | --- | ---: |
| 白盒单步 | 71→71 | 严格 | -1 |
| 机器数据 | 26→26 | 严格 | -2 |
| NetQAD 单步 | 80→79 | 一题容差 | +1 |
| 告警结论 | 13→13 | 严格 | +1 |
| 投诉工具 | 37→38 | 严格（+1） | +1 |

请求级共有 6 条 Vanilla 对→v5 错、6 条 Vanilla 错→v5 对，净变化为 0：

- 白盒单步 3 条回退、3 条改善；机器数据 2 条回退、2 条改善，体现生成/Judge 的请求级交换；
- NetQAD 单步唯一净回退仍是 `TC00000029`：没有把“先等待 10 分钟”识别为人工/时间条件，
  与 run_11 fixed_v4 的同一失败一致；
- 投诉工具 `Complaint_Specialist_003` 从 Vanilla 错改善为 v5 对；告警结论无转移。

因此固定参数选择结论不是“v5 每条都优于 v4”，而是：按 Vanilla 独立分层后，4 个 v5 在严格
层、1 个在容差层；同层内 v5 的完整模型工作量均更低。NetQAD 单步若最终要求严格非劣，应
直接 native，不建议继续扩大网格追逐单题随机波动。

## 6. Token 语义、phase 账本与 E2E 归因

### 6.1 客户端 `Token数` 已改变语义

本轮客户端 `Token数` 合计 27,911，恰好等于 `final_answer_tokens=28,151 - 240`；而 telemetry
还记录了 174,198 visible thinking token。即：本轮 CSV 保存的是 final content 计数，并未保存
完整 assistant completion 工作量。它与 run_08/run_11 中包含思考文本的旧 `Token数` 不可直接
比较。

账本闭合为：

```text
visible thinking 174,198
+ hidden probe    17,150
+ final answer    28,151
= total model    219,499
```

`visible + final - client Token数 = 174,438`，又恰好等于 visible thinking 174,198 加每请求一个
非 content 的 boundary/terminal 账本 token。该关系支持“CSV 只记录解析后的 final content”，
但缺少测试脚本版本与同运行时 Vanilla，不能进一步判断变化来自客户端记录逻辑还是 parser
返回结构，也不能只凭差值断言该单 token 的具体类型。

### 6.2 Phase 账本

| phase | calls | wall time | wall 占比 | generated token | token 占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `initial_think` | 240 | 2,611.274 s | 54.40% | 139,606 | 63.60% |
| `iterative_think` | 148 | 819.399 s | 17.07% | 34,592 | 15.76% |
| `confidence_probe` | 343 | 715.748 s | 14.91% | 17,150 | 7.81% |
| `final_answer` | 240 | 648.736 s | 13.52% | 28,151 | 12.83% |
| `decision` | 240 | 4.842 s | 0.10% | 0 | 0% |
| **合计** | **1,211** | **4,799.999 s** | **100%** | **219,499** | **100%** |

Phase wall 比客户端总 E2E 少 21.305 s / 0.44%。生成 phase 的 queue/prefill/decode 合计约
0.010 / 860.151 / 3,854.690 s；hidden probe 已完整计入，不能把 27,911 个客户端 token 称为
总生成量。

### 6.3 为什么 68.04% E2E 不能归因于 fixed_v5

| 指标（同 240 请求） | fixed_v4 | fixed_v5 | 变化 |
| --- | ---: | ---: | ---: |
| visible thinking | 224,735 | 174,198 | -22.49% |
| hidden probe | 7,093 | 17,150 | **+141.79%** |
| final answer | 27,706 | 28,151 | +1.61% |
| 总模型生成量 | 259,534 | 219,499 | -15.43% |
| generate calls | 676 | 971 | **+43.64%** |
| prefill wall | 566.028 s | 860.151 s | **+51.96%** |
| decode wall | 15,137.352 s | 3,854.690 s | **-74.54%** |
| decode wall / generated token | 58.3 ms | 17.6 ms | **-69.89%** |

Fixed_v5 通过更早检查减少了 visible thinking，但付出了更多 probe、child call 和 prefill。
总工作量下降 15.43% 是参数直接证据；decode 单 token 快约 3.3 倍至少直接受 TP4→TP8 的
实验身份变化混杂，无法从现有数据分离 TP、运行时版本、负载和 DTB 各自贡献。后续正式对照
统一使用 TP4，只有同一服务身份的 telemetry-off Vanilla 与自动 DTB 才参与 E2E 排名。

## 7. 量化增训权重定制参数可用性简表

“可用”在此仅表示可进入量化专用 YAML 候选；所有项仍需同运行时 telemetry-off 自动模式验收。

| scene | fixed_v5 参数 `(p,check,stride,num,min,hard)` | 质量层 | 模型工作量收益 | 当前结论 |
| --- | --- | --- | ---: | --- |
| 白盒单步 | `(.93,450,200,4,450,1100)` | 严格 | 23.58% | 可入选；不再搜索 |
| 机器数据 | `(.95,750,100,3,750,1050)` | 严格 | 8.82% | 可入选；收益最小，off 复验不正则 native |
| NetQAD 单步 | `(.93,300,300,3,300,1050)` | 一题容差 | 17.70% | 条件入选；严格门下直接 native |
| 告警结论 | `(.95,1600,200,2,1600,2000)` | 严格 | 11.33% | 可入选；不再搜索 |
| 投诉工具 | `(.93,1000,250,3,1000,1650)` | 严格（+1） | 11.79% | 可入选；最终做工具结构审核 |

结合 run_11 其余 8 个场景，已新增
[`dtb/config/budget_config_quant.yaml`](../../../../dtb/config/budget_config_quant.yaml) 作为 TP4
端到端候选，构成为：

- 使用本轮 fixed_v5：上述 5 个 scene；
- 暂用 run_11 候选：KPI、事件自闭环、投诉分类中英文、白盒整体，共 5 个 scene；事件须先通过
  `TC00000069` 定向额度回放，白盒整体仍是一题容差候选；
- NetQAD 规划、告警建议、长文本根因暂保留 fixed_v4，仅作为 TP4 诊断候选。当前 resolver
  没有可由 YAML 表达的逐场景真实 native bypass，不能写一个不生效的 `native` 字段冒充旁路；
  三者在 TP4 正式 run 若无正收益或质量不合格，再实现并验收真正的原生旁路。

这是一份**待验收配置**，不是已发布策略；本轮显式 fixed 也没有验证自动分类恢复参数。

## 8. DTB 与后训练方案的差异价值

Run_08 的同权重 Vanilla 已代表“后训练模型、不启用 DTB”；本轮 fixed_v5 代表在该模型上继续
增加运行时控制。因此本轮能证明 DTB 在后训练之上仍有 16.46% 的五场景模型工作量增量价值，
但不能替代后训练本身的受控对照。

| 维度 | DTB 运行时压缩 | 自蒸馏最短路径 SFT + 长度/正确性加权 RL |
| --- | --- | --- |
| 作用位置 | 请求时选择思考预算、probe 和提前 final | 训练时改变模型生成分布与能力 |
| 迭代速度 | 参数/YAML/规则可快速回放，核心集成完成后迭代较快 | 需数据构造、训练、量化、回归和新 checkpoint，周期更长 |
| 前期成本 | 无训练算力，但需 serving/parser/telemetry 工程 | 训练与数据成本高，推理链路可以保持原生单次生成 |
| 运行时成本 | 多 child、hidden probe、重复 prefill；本轮平均 4.05 次 generate/请求 | 无控制器 probe/prefill 开销，成本固化在模型权重中 |
| 轻量化含义 | 部署和策略回滚轻；底层 vLLM 集成并不轻 | 服务逻辑轻；权重制作、存储和发布不轻 |
| 模型泛化 | 算法框架可复用，但预算不可跨权重直接继承；run_08 已证明旧配置有 9 场景质量失败 | 每个基础模型/量化版本通常需重新训练，训练配方可复用但 checkpoint 不可复用 |
| 应用泛化 | 已知 scene 可精细控制；陌生请求需 reject/native/history，规则存在覆盖边界 | 若训练学到一般性短推理，可越过显式 scene；但分布外任务可能质量退化且难以局部回滚 |
| 可控性 | 请求/scene 级开关、预算和 native fallback，局部回滚快 | 主要以模型版本整体切换，难以只恢复一个 scene 的长思考 |
| 能力上限 | 只能控制已有推理过程，不能补足模型不会的能力 | 可改变推理策略和正确性—长度 Pareto 前沿，但有训练偏置风险 |

现有证据更支持**互补而非二选一**：后训练负责把典型推理路径整体变短，DTB 负责已知场景的
尾部约束、快速局部修正和可回滚策略。模型已经足够短、DTB 净收益小于 probe/prefill 时，应
native；不能为了证明 DTB 存在而强行对每个 scene 启用。

### 8.1 建议的最小 2×2 价值实验

在相同 vLLM/Ascend、硬件、MTP、sampling、负载和数据上测试：

| 权重 | Vanilla | DTB |
| --- | --- | --- |
| 开源权重 | A：原生基线 | B：开源权重冻结配置 |
| 量化增训权重 | C：后训练原生基线 | D：量化专用配置 |

当前第二种权重同时包含 W8A8 和后训练，因而该最小实验只能计算“量化+后训练权重包”效果，
不能把量化收益从 SFT/RL 收益中拆开：

```text
量化+后训练包效应 = C - A
DTB@开源增量      = B - A
DTB@后训练增量    = D - C
交互效应          = (D - C) - (B - A)
```

E2E 使用负差表示更优时应在报告中统一换算为 gain。除质量、E2E 和总模型 token 外，还应记录
训练 GPU-hours/数据量、checkpoint 体积、DTB child calls/hidden probe/prefill、配置迭代人天和
scene/OOD 回归，以回答“轻量、快速迭代、模型与应用泛化”而非只比较一次平均时延。

如果能够同时提供“开源 W8A8”和“后训练未量化”权重，最终应升级为
`训练状态 × 量化状态 × DTB 开关` 的 2×2×2 实验；只有该设计才能把后训练、量化、DTB 三个
主效应和交互效应分别识别。若没有这些中间权重，报告必须始终把 `C-A` 称为权重包效果。

## 9. 下一步实验与停止条件

### 9.1 P0：协议定向闭环，不再改参数

1. 使用 commit `d659f33e` 定向重放 `TC00000069`、`YH0032` 各至少两次；
2. 重放 run_10 的 48 条 no-final 集，要求继续保持 0 空 final；
3. 增加一个较小 `max_tokens` 样本，验证 parser-facing token 不越界且原样返回
   `finish_reason=length`；
4. 验收 `answer_limit=min(model_context_remaining, external_remaining)`，并保存 manifest。

### 9.2 P1：量化专用配置的一次正式收口

不再做 fixed_v6。使用第 7 节已创建的独立量化 YAML：5 个 fixed_v5 加 8 个 fixed_v4
复验点。停服后把该文件复制为规范的 `dtb/config/budget_config.yaml` 并重启，不在运行时
按权重自动切换配置。下一轮：

1. 服务统一回到 **TP4**，重新采集 13 场景 Vanilla；
2. 保持同一 TP4 服务身份，只传 `enable_think_budget=true` 运行自动配置全集；
3. 两个正式 variant 都使用 `OBSERVABILITY.mode=off`；另以同身份少量 phase audit 验证
   scene、预算和总工作量；
4. 补齐 manifest，记录 TP、量化 YAML 文件名与 SHA，确保两侧 parser/客户端 Token 口径一致。

发布门保持：每场景严格层优先、严格层为空才允许少 1 题；0 个 scene 少 2 题；0 空 final、
0 final 截断、0 工具/解析 hard failure；质量通过后，13 场景等权 paired E2E gain 必须为正。

若任一 DTB scene 在同运行时 off 结果中 E2E 不为正，或质量落出允许层，直接改为 native；
除非存在新的系统性轨迹证据，不再启动参数网格。

### 9.3 P2：转向 DTB × 后训练价值实验

完成 P0/P1 后先开展第 8.1 节的权重包 2×2 对照；若中间权重可得，再升级到 2×2×2，并加入
未参与规则设计的产品 holdout 与通用/OOD。
这比继续在 633 条 search 集上寻找更细参数更能回答方案选型，也能避免把训练、量化、runtime
升级和 DTB 的收益重复记账。

## 10. 阶段验收与证据强度

| 验收项 | 状态 | 依据 |
| --- | --- | --- |
| 五场景 fixed_v5 质量层 | **候选通过** | 4 严格 + 1 一题容差 |
| 五场景完整模型工作量 | **通过** | 每场景降低 8.82%～23.58%，总体 16.46% |
| fixed_v5 E2E 正收益 | **不可裁决** | fixed_v5 为 TP8、Vanilla 为 TP4；跨 TP 观测不能归因 |
| v4 额度 telemetry | **本轮通过** | 240/240 字段闭合、无 2,048 cap |
| 历史两条 final 截断 | **未验证** | 本轮不含 `TC00000069`、`YH0032` |
| 自动分类与量化 YAML | **未验证** | 本轮全部显式 fixed |
| 量化权重阶段一发布门 | **未通过** | 尚缺 P0、telemetry-off 自动全集和 manifest |

- QA_ID 配对、E2E、Judge、fixed 分支、预算、额度字段和 phase 账本为**实测**；
- 用户声明的相同量化增训权重、commit `d659f33e`、本轮 TP8 与 run_08 Vanilla TP4 为
  **用户提供的实验事实**，缺 manifest 未独立闭合完整服务器身份；
- 客户端 `Token数` 只表示 final content 由账本等式**实测推导**，具体变化发生在测试脚本还是
  parser 返回层仍是**待核对**；
- fixed_v5 在 telemetry-off 同运行时仍有相同 E2E 收益、2×2 实验中 DTB 与后训练的主效应大小
  均为**待实验**。

## 11. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-08-24 | 补充实验身份：fixed_v5 使用 TP8、对照 Vanilla 使用 TP4，故撤销 E2E“候选通过”并要求下一轮统一 TP4；创建量化 13 场景候选 YAML，5 个 fixed_v5 与 8 个 fixed_v4 复验点通过人工部署选择，3 个低收益场景等待 TP4 结果后再决定是否实现真实 native bypass。 |
| 2026-08-24 | 初版：完成 240 条 fixed_v5 与 run_08 Vanilla、run_11 fixed_v4 的请求级配对；确认五组参数进入质量层并降低完整模型工作量，审计 v4 completion 额度与 phase 账本；识别客户端 Token 口径变化和跨 run decode 性能身份漂移，决定停止 fixed 搜索并转向协议闭环、自动配置全集及 DTB×量化增训权重包 2×2（条件允许时 2×2×2）价值实验。 |
