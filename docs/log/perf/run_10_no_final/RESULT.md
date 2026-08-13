# run_10_no_final：无最终答案定向重测

## 0. 结论

本 run 先后对 [`quant_post_train_no_final_48.csv`](../../../debug/quant_post_train_no_final_48.csv)
中的 48 条历史“`</think>` 后无最终答案”请求执行了两次自动 DTB：07:11 版本中
`confidence_accepted` 只追加裸 `</think>`；09:06 版本由用户在服务器上手工改为追加
`BUDGET_EXHAUSTED_FINAL_PROMPT`，仍保留 fresh-final。

- **实测——answer cue 对当前故障有效。** 空 final 从 **8/48 降为 0/48**；旧轮 8 条失败
  在新轮全部生成 232～345 个 final token、有实际 decode、以普通 `stop` 结束，且 8 条新答案
  全部被 Judge 判为正确。`1 token + decode_ms=0 + stop_reason=248044` 的失败签名也从 8 条降为 0。
- **实测——改动确实进入了目标分支。** 由
  `final prompt token - 原始 prompt token - visible think token` 反推，旧轮
  `confidence_accepted` 后缀为 1 token（47/47），新轮两个 final 原因的后缀均为 24 token
  （48/48），与手工把 `THINK_END_TAG` 改成完整诱导语一致。
- **实测——定向集质量没有退化。** 原始 Judge 从 37/48 升至 43/48；把旧轮 3 条
  “空 final 但 Judge=True”强制计错后，严格正确数从 **34/48 升至 43/48**。请求级转移为
  11 条改善、2 条退化、32 条保持正确、3 条保持错误。
- **实测——未观察到整体时延代价。** 平均客户端 E2E 从 89.9622 s 降至 86.6701 s，
  改善 3.2920 s / 3.66%，P95 基本持平；response completion token 只增加 155 / 0.24%。
  但两轮的 probe 接受轮次和生成长度不同，且缺少完整实验 manifest，不能把总体变快归因于
  23-token cue。旧失败 8 条由于不再快速空停，平均 E2E 合理地增加了 5.8453 s。
- **阶段判断——单轮定向验证通过，完整修复门尚未闭合。** 该结果强烈支持“裸
  `</think>` 是量化增训权重 fresh-final 立即停止的触发条件”；仍需把手工改动正式提交，加入
  final 非空不变量，并重复 2～3 轮及执行 633 条全集，才能宣称 hard failure 已消除。

下一步可以评估“接受后复用 trial answer 并续写”，它有机会避免高置信 trial 被丢弃后从头
重采样；但必须改成**同一 final prompt 上的事务式 speculative prefix**，不能直接重放当前
`TRIAL_PROMPT` 生成的隐藏结果，否则会破坏 parser-facing token 连续性。详细约束见第 10 节。

## 1. 输入、实验身份与证据边界

权威输入与产物：

- 定向输入：[`quant_post_train_no_final_48.csv`](../../../debug/quant_post_train_no_final_48.csv)，
  由 run_08 旧 DTB 的 25 条和 run_09 fixed_v4 的 23 条历史失败组成；
- 客户端汇总：[`test_result/_SUMMARY.csv`](test_result/_SUMMARY.csv)；
- 客户端明细：
  [`dtb_quant_post_train_no_final_48_2026-08-13_07-11-48.csv`](test_result/dtb_quant_post_train_no_final_48_2026-08-13_07-11-48.csv)、
  [`dtb_quant_post_train_no_final_48_2026-08-13_09-06-14.csv`](test_result/dtb_quant_post_train_no_final_48_2026-08-13_09-06-14.csv)；
- 绑定审计：[`extraction_manifest.json`](extraction_manifest.json)、
  [`request_index.csv`](request_index.csv)；
- telemetry：`extracted/` 下两个同名轮次子目录中的 `requests.jsonl`、
  `controller_phases.jsonl` 和 `phase_summary.csv`。

根据用户说明，两轮均使用 W8A8 量化、特定场景思维链压缩语料增训的权重并保留
fresh-final；09:06 轮在服务器上手工把 `confidence_accepted` 的裸结束标签改为完整 answer
cue。目录没有上传 `manifest.json` 和原始 `server.log`，因此无法
独立核对 checkpoint、adapter/server commit、实际 tokenizer 特殊 token 表、sampling/seed、
并发、cache 和服务器代码 SHA。已提取产物足以审计本轮 48 条的请求归属和算法轨迹，但本文
不会把恢复率的变化全部归因于某一项代码改动。

本 run 没有 Vanilla，因此不做相对 Vanilla 的优化收益排名。07:11 与 09:06 的比较只用于
评估 answer cue 修复，不能替代完整产品集的质量与性能验收。

## 2. 07:11 初次复测：完整性与确定性协议检查

| 检查项 | 结果 |
| --- | ---: |
| 输入 / 结果行 | 48 / 48 |
| 唯一 `QA_ID` / 唯一非空 `request_id` | 48 / 48 |
| 问题、参考答案与定向输入逐条一致 | 48/48 |
| `server.log` 请求覆盖 / TB phase 覆盖 | 48/48 / 48/48 |
| 选中 / 排除 phase | 259 / 0 |
| 客户端 `错误` 非空 | 0 |
| 响应中恰有一个 `</think>` | 48/48 |
| `</think>` 后非空 / 为空 | 40 / **8** |
| 原始 Judge | 37 True / 11 False / 0 invalid |
| 空 final 被 Judge 错判为 True | **3** |

协议检查必须先于语义 Judge：空 final 无论 thought 中是否已经出现草稿，都不能算成有效最终
答案。本轮严格覆盖后的正确数为 34/48；这不是新的总体质量指标，只是避免 Judge 把 thought
当 final 读取。

## 3. 07:11 初次复测：实际路由、配置和 final 原因

| telemetry 项 | 结果 |
| --- | ---: |
| `scene_matched=true` | 48/48 |
| `decision_branch=scene_difficulty` | 48/48 |
| fixed / reject / default | 0 / 0 / 0 |
| `final_answer_reason=confidence_accepted` | 47 |
| `final_answer_reason=budget_exhausted` | 1 |
| `policy_version` | `think-budget-performance-v1` |

48 条覆盖 10 个 scene；8 条失败只落在两个已正确识别的 scene 上：

| scene | 定向集 N | 无 final | 实际预算 `(check, min, num, stride)` | confidence | hard limit |
| --- | ---: | ---: | --- | ---: | ---: |
| `netqad_e2e_planning` | 13 | **2** | `(950, 950, 5, 450)` | 0.95 | 3,050 |
| `long_context_root_cause` | 12 | **6** | `(1200, 1200, 4, 400)` | 0.93 | 2,600 |
| 其余 8 个 scene | 23 | 0 | 各自 scene 配置 | — | — |

这是历史失败集内部的分布，不代表两个 scene 在完整产品集上的真实发生率。它能说明分类器和
配置读取不是本轮 8 条错误的直接原因：它们都命中预期 scene、拿到有效预算，并且在达到
hard limit 前由高置信 probe 提前进入 final。

## 4. 07:11 初次复测：复现明细

| QA_ID | request_id | 历史来源 | scene | Judge | fresh-final |
| --- | --- | --- | --- | ---: | --- |
| `NOFINAL_015` | `chatcmpl-bd09e03439b1231a` | run_08 DTB | `netqad_e2e_planning` | False | 1 token / `248044` |
| `NOFINAL_020` | `chatcmpl-b0fb6f2dc384ebb9` | run_09 fixed_v4 | `netqad_e2e_planning` | False | 1 token / `248044` |
| `NOFINAL_026` | `chatcmpl-977264cf1782d744` | run_08 DTB | `long_context_root_cause` | **True（误判）** | 1 token / `248044` |
| `NOFINAL_028` | `chatcmpl-b2639cedfe7ce0e5` | run_08 DTB | `long_context_root_cause` | **True（误判）** | 1 token / `248044` |
| `NOFINAL_029` | `chatcmpl-83117784f7eff48c` | run_08 DTB | `long_context_root_cause` | False | 1 token / `248044` |
| `NOFINAL_033` | `chatcmpl-9183ee3a5bc25280` | run_08 DTB | `long_context_root_cause` | False | 1 token / `248044` |
| `NOFINAL_034` | `chatcmpl-a498b3ae1e3f5a1a` | run_08 DTB | `long_context_root_cause` | **True（误判）** | 1 token / `248044` |
| `NOFINAL_036` | `chatcmpl-beb59e3178fd9e6c` | run_08 DTB | `long_context_root_cause` | False | 1 token / `248044` |

40/48 在本轮恢复，8/48 再次失败。按历史来源看，run_08 来源重现 7/25，run_09 来源重现
1/23；由于来源同时混入了旧预算、采样随机性和不同运行时刻，这个拆分只用于定位原文件，
不作为策略优劣比较。

## 5. 07:11 初次复测：8 条失败的算法轨迹

下表中 `Tn(reason)` 表示一次 thinking 生成及 token 数，`Pk confidence` 表示第 k 次隐藏
probe；`✓/×` 表示是否达到该 scene 的 confidence threshold。14 次 probe 全部使用
`average_fallback` 评分。

| QA_ID | thinking / probe 轨迹 | 接受点 | 进入 final 的原因 | final phase |
| --- | --- | ---: | --- | --- |
| `NOFINAL_015` | `T950(length) → P1 .919087× → T450(length) → P2 .903669× → T9(</think>) → P3 .892349× → T371(stop) → P4 .988609✓` | round 4 | `confidence_accepted` | `1 token, decode=0, stop=248044` |
| `NOFINAL_020` | `T950(length) → P1 .947646× → T450(length) → P2 .932442× → T182(</think>) → P3 .943095× → T294(stop) → P4 .994463✓` | round 4 | `confidence_accepted` | 同上 |
| `NOFINAL_026` | `T1135(</think>) → T306(stop) → P1 .986146✓` | round 1 | `confidence_accepted` | 同上 |
| `NOFINAL_028` | `T1164(</think>) → T262(stop) → P1 .988394✓` | round 1 | `confidence_accepted` | 同上 |
| `NOFINAL_029` | `T1136(</think>) → T282(stop) → P1 .987542✓` | round 1 | `confidence_accepted` | 同上 |
| `NOFINAL_033` | `T1100(</think>) → T301(stop) → P1 .990340✓` | round 1 | `confidence_accepted` | 同上 |
| `NOFINAL_034` | `T1172(</think>) → T320(stop) → P1 .987779✓` | round 1 | `confidence_accepted` | 同上 |
| `NOFINAL_036` | `T1092(</think>) → T256(stop) → P1 .988552✓` | round 1 | `confidence_accepted` | 同上 |

六条长文本请求都在首次 thinking 中主动生成 `</think>`，但当时尚未达到
`min_think_budget=1200`，状态机按设计继续一个 stride；达到 minimum 后，第一次 probe 以
0.986～0.990 的高分被接受。两个 NetQAD 请求则分别到第 4 次 probe 才被接受。尽管前置轨迹
不同，进入 fresh-final 后都收敛到完全相同的失败签名。

原请求与重测的 final 对照也一致：

- 8 个原 request 的 final phase 全部为 `generated_tokens=1`、`decode_ms=0`、
  `stop_reason=248044`；
- 8 个新 request 仍全部为同一组合；
- NetQAD 两条的 probe 轮次从历史的 round 3/1 变为本轮 round 4/4，仍然失败，说明问题不依赖
  某个固定接受轮次；六条长文本均保持 round 1 后失败。

## 6. 07:11 初次复测：发生机制

### 6.1 已由 telemetry 直接证明的轨迹

1. scene 分类和预算生成成功；
2. hidden probe 生成了 50 token 的试答，并以 `average_fallback` 达到阈值；
3. 状态机记录 `final_answer_reason=confidence_accepted`；
4. fresh-final 启动后只有 prefill，没有可计量 decode 时间；
5. 第一个也是唯一一个生成 token 触发 `stop_reason=248044`；
6. vLLM 不把停止 token 作为可见文本返回，客户端因此只看到控制器先发出的 `</think>`。

### 6.2 当前源码揭示的实现缺口

当前 [`state.py`](../../../../think_budget_vllm_adapter/deer_think_budget/state.py) 中：

- `budget_exhausted` 选择 `BUDGET_EXHAUSTED_FINAL_PROMPT`，而
  `confidence_accepted` 只选择 `THINK_END_TAG`；
- hidden probe 的高置信只说明带 `TRIAL_PROMPT` 的 50-token 子回答看起来可信，并不能保证
  另一次、不带 answer cue 的 fresh-final 会输出内容；
- `_FinalAnswerState` 只检查 `last_output is not None`，随后无条件写入
  `completion_status="success"`，没有校验可见文本或“最后一个 `</think>` 后非空”。

因此，**当前修复只覆盖预算耗尽路径，而本轮故障全部发生在置信度接受路径**。量化增训模型
在仅以 `</think>` 结束的 fresh prompt 上有概率立即选择停止 token，控制器又把这个合法的
vLLM stop 当成成功，最终形成协议空答案。这一解释由实测轨迹与当前源码共同支持；具体是后训练
改变了哪个特殊 token 的概率，仍需目标 tokenizer/采样参数打点才能确认。

### 6.3 `248044`、`248046` 与此前 `284044` 的边界

用户此前确认 `284044` 解码为 `<|endoftext|>`、主 `eos_token_id=248046`。但本轮及这 8 条
历史原请求的 telemetry **实际记录的是 `stop_reason=248044`**，不是 `284044`，也不是
`248046`。在没有本轮 tokenizer 和 generation config 的情况下，不能把 `248044` 直接标成
`<|endoftext|>`；它可能是额外 EOS、chat-template 结束 token 或显式 stop-token 集合中的成员。

下一次服务器打点应同时记录并解码：

```text
first_final_token_id
first_final_token_text
sampling_params.stop_token_ids
sampling_params._eos_token_id / ignore_eos
tokenizer.eos_token_id
generation_config.eos_token_id
```

并直接执行 `convert_ids_to_tokens([248044, 248046, 284044])`。这能消除“主 EOS 与额外 stop
token”之间的歧义，但不影响本轮“fresh-final 立即停止且无可见答案”的结论。

## 7. 07:11 初次复测：Phase 账本

| phase | calls | wall time | 占 phase wall | generated token |
| --- | ---: | ---: | ---: | ---: |
| `initial_think` | 48 | 2,612.418 s | 60.58% | 41,130 |
| `iterative_think` | 39 | 777.354 s | 18.02% | 11,907 |
| `confidence_probe` | 76 | 274.049 s | 6.35% | 3,510 |
| `final_answer` | 48 | 647.695 s | 15.02% | 11,366 |
| `decision` | 48 | 0.981 s | 0.02% | 0 |
| **合计** | **259** | **4,312.497 s** | **100%** | **67,913** |

客户端总 E2E 为 4,318.1844 s，phase wall 少 5.6875 s / 0.132%。visible thinking
53,037 token + final 11,366 token = 64,403，比客户端 response completion 64,351 多 52
token / 0.081%；加上隐藏 probe 后，服务内记录的总生成量为 67,913。这个小差值应视为
usage/tokenizer 边界差，而不能把 response completion 当作总模型工作量。

本轮是 48 条故障定向重测，没有同轮 Vanilla，所以这些时延只用于闭合轨迹，不形成性能收益
结论。另有 3 条非空 final 以 `stop_reason=</think>` 结束，1 条以 2,048 token `length`
结束；它们不属于本次空 final，但后续全集质量审计应检查是否存在截断或多余 reasoning 标记。

## 8. 当前修复状态与回归矩阵

### 8.1 最小修复顺序

1. **统一 answer cue：定向单轮已通过，尚未正式入库。** 09:06 轮证明
   `confidence_accepted` 使用 `BUDGET_EXHAUSTED_FINAL_PROMPT` 后，48 条均产生非空 final；
   服务器手工修改仍需转成可审查代码和配置。
2. **增加非空不变量：待实现。** 累积 fresh-final 的可见文本；若为空、仅有空白或仅由停止
   token 结束，则标记 `protocol_no_final_answer`，不得写 `completion_status=success`。
3. **增加一次有界重试：作为保险而非正常路径。** 第一次为空时追加更强 answer cue 再
   fresh-generate 一次；仍为空才触发可审计 fallback。不要优先用 `ignore_eos` 强行续写，
   因为它可能造成特殊 token 泄漏或无界生成。
4. **trial 复用可以进入下一轮开发，但必须重构为事务式 exact-prefix。** 当前 probe 有独立
   `TRIAL_PROMPT` 且通常被 50-token 上限截断，不能把其文本直接拼到最终回答；详细契约和
   实验条件见第 10.4～10.5 节。

### 8.2 48 条定向回归矩阵

| 变体 | 状态 | 目的 |
| --- | --- | --- |
| A：裸 `</think>` + fresh-final | 已完成（07:11） | 8/48 空 final，复现故障 |
| B：统一 answer cue + fresh-final | 已完成 1 轮（09:06） | 0/48 空 final，验证 cue 有效性 |
| C：B + 非空校验 + 一次重试 | 待实现 | 闭合协议不变量和异常兜底 |
| D：B/C + accepted trial exact-prefix 复用 | 待实现 | 比较质量、总工作量与 E2E，不以牺牲 parser 契约换收益 |

后续变体保持同一模型、sampling/seed、并发和 cache 条件；B/C 至少再重复 2～3 次。优先验收：

1. 每轮 48/48 都有非空 final，`protocol_no_final_answer=0`；
2. 不再由 Judge 决定空答案是否有效；协议失败先强制计错；
3. telemetry 能区分首次成功、重试成功和 fallback，并记录首 token/停止 token 的解码；
4. B/C 通过后，回到 633 条全集检查质量非劣、hard failure 和 E2E；D 必须以 B 或 C 为
   对照，不能用 48 条历史失败集单独决定是否上线。

## 9. 证据强度与变更记录

- 两轮各 48 条绑定、空 final 8→0、严格正确 34→43、旧失败 8 条恢复、scene/预算、后缀
  token 数、stop reason 和 phase 账本为**实测**；
- 09:06 服务器把 `THINK_END_TAG` 手工替换为 `BUDGET_EXHAUSTED_FINAL_PROMPT` 是**用户提供的
  实验事实**；仓库当前源码没有这项修改，不能把服务器具体 diff 标成已独立核对；
- 当前工作区的 fresh-final 非空检查缺失、probe 默认隐藏且接受后重新 fresh-generate、原生
  parser 对 `text`/`token_ids` 连续性的要求为**源码确定**；
- “裸 `</think>` 是量化增训权重立即终止的主要触发条件”受到本次 A/B 的强支持，但因缺少
  manifest、采样轨迹不同且只有一次修复轮，仍保留为**待重复验证的因果判断**；
- 服务器代码与当前工作区逐 SHA 一致、`248044` 的具体 token 文本均未得到独立证明。

| 日期 | 变更 |
| --- | --- |
| 2026-08-13 | 初版：完成 48 条历史空 final 定向重测审计，确认 8 条以相同 one-token stop 签名复现，并定位到未恢复诱导语的 `confidence_accepted` fresh-final 路径。 |
| 2026-08-13 | answer-cue 复测：纳入 09:06 客户端结果和 telemetry，确认空 final 8→0、严格正确 34→43，并给出 accepted trial 事务式复用的实现边界与验收矩阵。 |

## 10. 09:06 answer-cue 复测与 trial 复用评估

### 10.1 请求绑定与改动命中证明

两轮各有 48 个唯一 `QA_ID` 和 `request_id`，问题与参考答案逐条一致。由于客户端执行顺序有
变化，比较一律按 `QA_ID` 配对；telemetry 再用
`(scene, classification_input_chars, decision prompt_tokens, scene_scores)` 唯一指纹复核，
48/48 都能找到唯一对应请求。更新后的提取清单覆盖 96/96 个客户端请求、531 个 phase，排除
phase 为 0，没有 Judge 请求混入。

两轮都为 48/48 `scene_matched=true`、`decision_branch=scene_difficulty`，10 个 scene 的请求数
完全一致，核心预算配置也逐条一致。final 分支因随机轨迹由旧轮 47 个
`confidence_accepted` + 1 个 `budget_exhausted` 变为新轮 43 + 5，但不影响 cue 是否生效的判断：

| final prompt 额外后缀 token | 07:11 | 09:06 |
| --- | ---: | ---: |
| `confidence_accepted` | 47/47 为 **1** | 43/43 为 **24** |
| `budget_exhausted` | 1/1 为 **24** | 5/5 为 **24** |

额外后缀由 `final prompt_tokens - decision prompt_tokens - visible_think_tokens` 反推。final
prompt 总量增加 2,040 token，恰好可分解为 visible thinking 增加 959 token，加上后缀总量由
71 增至 1,152 token（增加 1,081）；这证明 09:06 的完整 answer cue 确实进入了所有 final
路径，而不只是客户端文本发生偶然变化。

### 10.2 有效性、质量与客户端 E2E

| 指标 | 07:11 裸结束标签 | 09:06 完整 answer cue | 变化 |
| --- | ---: | ---: | ---: |
| `</think>` 后空 final | **8/48** | **0/48** | -8 |
| `1 token + decode_ms=0 + stop=248044` | **8** | **0** | -8 |
| 原始 Judge True | 37/48 | 43/48 | +6 |
| 严格正确（空 final 强制计错） | **34/48** | **43/48** | **+9** |
| 客户端平均 E2E | 89.9622 s | 86.6701 s | -3.2920 s / -3.66% |
| 客户端 P50 / P95 | 98.3255 / 124.8521 s | 95.5488 / 124.7047 s | -2.82% / -0.12% |
| response completion token | 64,351 | 64,506 | +155 / +0.24% |
| 更快请求 | — | 28/48 | — |

严格正确的请求级转移为 32 条保持正确、11 条错误转正确、2 条正确转错误、3 条保持错误。
11 条改善为 `NOFINAL_004/015/020/026/028/029/033/034/036/040/043`；退化为
`NOFINAL_003/006`，保持错误为 `NOFINAL_005/039/044`。所有 8 条旧空 final 在新轮均由 Judge
判为正确，新 final 为 232～345 token、均有正 decode 时间且以普通 `stop` 结束。

旧失败 8 条的新轮平均 E2E 比旧轮增加 5.8453 s，属于从“立即空停”恢复为正常回答后的必要
成本；其余 40 条平均缩短 5.1195 s。整体没有出现 answer cue 的可见时延代价，但由于两轮
thinking/probe 轨迹不同且缺少 manifest，这一结果不能证明 23-token cue 本身带来加速。

### 10.3 服务内工作量闭合

| phase | 07:11 calls / wall / token | 09:06 calls / wall / token | wall 变化 |
| --- | ---: | ---: | ---: |
| `decision` | 48 / 0.981 s / 0 | 48 / 0.964 s / 0 | -0.016 s |
| `initial_think` | 48 / 2,612.418 s / 41,130 | 48 / 2,507.634 s / 41,333 | -104.783 s |
| `iterative_think` | 39 / 777.354 s / 11,907 | 47 / 807.962 s / 12,663 | +30.608 s |
| `confidence_probe` | 76 / 274.049 s / 3,510 | 81 / 261.143 s / 3,490 | -12.907 s |
| `final_answer` | 48 / 647.695 s / 11,366 | 48 / 576.942 s / 10,567 | -70.753 s |
| **合计** | **259 / 4,312.497 s / 67,913** | **272 / 4,154.645 s / 68,053** | **-157.851 s / -3.66%** |

09:06 的 visible thinking 为 53,996 token、hidden probe 为 3,490、final 为 10,567；相对旧轮
分别变化 +959、-20、-799，总服务内生成量只增加 140 / 0.21%。客户端总 E2E 与 phase wall
的差为 5.5212 s / 0.133%，账本闭合。这里的 phase 变化包含随机生成长度和接受轮次变化，不能
拆成 answer cue 的独立因果耗时。

### 10.4 accepted trial 复用是否值得做

**可以作为下一项优化，但它不是简单把当前 trial 文本输出。** 当前实现让 hidden probe 使用
独立 `TRIAL_PROMPT`，只收集置信度，接受后丢弃并启动 fresh-final。09:06 的 43 个 accepted
probe 共生成 2,075 token，其中 41 个因达到 50-token 上限而以 `length` 结束，只有 2 个自然
终止。因此：

- 可复用 token 的理论上限是 2,075，占本轮服务内总生成量 3.05%；按本轮 final decode 的
  粗平均速度外推，对应约 104.7 s / 2.52% 客户端 E2E 的**乐观上限**，不是实测收益；
- 41/43 仍需续写 final，通常不能省掉子请求数量，也不能假定省掉第二次 prefill；真正收益
  主要来自避免把 accepted trial 重新采样一遍；
- trial 是基于不同 prompt 生成且多为截断片段，直接拼接会使 token 的条件概率上下文、结构
  标记和客户端 `finish_reason` 不再等价，不能上线。

可接受的设计应是事务式 speculative prefix：

1. 从未修改的主请求状态复制 child，在 child 上追加与最终提交**完全相同**的 answer cue；
2. 生成并缓存 trial 的原始 chunk 序列、`text`、`token_ids`、logprobs 和终止信息，再计算置信度；
3. 拒绝时丢弃 child，主状态没有追加 `</think>`，可以继续 reasoning；
4. 接受时原子提交同一 answer cue，向客户端只输出一次 `</think>`，随后按原顺序把 trial
   作为 `final_answer` 前缀回放；
5. trial 若自然终止则直接完成；若因 `length` 截断，把其**精确 token IDs**加入主 prompt 后
   续写 final，并清除 trial 的中间 `finish_reason`，最终只暴露一次真正终止原因。

这一路径同时解决“高置信试答和最终答案是两次独立采样”的语义错配，也保留 reject 后继续思考
的能力。禁止在计算置信度前直接修改主状态；否则一旦 reject 就无法恢复原生 reasoning 轨迹。

### 10.5 实现约束与验收门

trial 复用必须保持仓库定义的原生 parser-facing 等价：

- 缓存并回放原始 chunk，而不是从 `full_text` 重新 tokenize；`text`、`token_ids`、logprobs 和
  增量顺序必须对齐；
- `<tool_call>` 等跨 speculative batch 的结构 token 仍须走 `final_answer` 的结构修复，不能
  丢失、重复或泄漏到 `content`；
- trial 请求强制的 `logprobs=1` 未必满足外部 API 的 `logprobs/top_logprobs` 契约；不能满足
  时必须关闭可见复用，或让 trial 采样覆盖最终响应所需字段；
- telemetry 至少新增 `trial_generated_tokens`、`trial_reused_tokens`、
  `trial_discarded_tokens`、`trial_reuse`、`final_continuation`，accepted trial 从 hidden 账本
  移入 visible final 且只计一次；
- 单测覆盖 reject 后继续思考、accept+terminal、accept+length 后续写、取消/错误、流式/非流式、
  tool call、结构标记跨 batch 和最终停止原因。

上线判断使用 cue+fresh-final 作为对照：定向 48 条连续 3 轮空 final 为 0；633 条全集不新增
hard failure、质量满足既定门槛；流式/非流式均只有一个 `</think>`，工具参数 JSON 完整且
`finish_reason` 正确；最后再以请求级 paired E2E 和包含 hidden probe 的总工作量判断复用是否
值得保留。
