# run_10_no_final：无最终答案定向重测

## 0. 结论

本轮把 [`quant_post_train_no_final_48.csv`](../../../debug/quant_post_train_no_final_48.csv)
中的 48 条历史“`</think>` 后无最终答案”请求，在量化增训权重上重新执行了一次自动 DTB。

- **实测——故障仍然存在。** 以“最后一个 `</think>` 后没有非空字符”为唯一判定，
  本轮复现 **8/48（16.67%）**，40/48 恢复为非空答案。因为这 48 条本来就是从历史失败中
  定向抽取的集合，16.67% 只能解释为**历史失败集的单次重现率**，不能外推为正常流量故障率。
- **实测——8 条是与历史相同的终止签名。** 每条 fresh-final 都只生成 1 个 token，
  `decode_ms=0`，`stop_reason=248044`，客户端只收到控制器发出的 `</think>`。这 8 个原请求
  在 run_08/run_09 中同样是 `generated_tokens=1 / decode_ms=0 / stop_reason=248044`，所以不是
  Judge 波动，而是同一协议级错误再次发生。
- **实测——恢复的预算耗尽诱导语没有覆盖失败路径。** 47/48 请求通过
  `confidence_accepted` 进入 final，只有 1 条为 `budget_exhausted`；8 条失败全部属于前者。
  当前 telemetry 显示，唯一预算耗尽请求 `NOFINAL_009` 正常输出了 112-token final，但一个样本
  不足以评价预算耗尽分支的稳定性。
- **源码确定——当前两个 final 原因使用不同后缀。** 当前工作区中，
  `budget_exhausted` 使用“诱导语 + `</think>`”，`confidence_accepted` 仍只追加 `</think>`；
  fresh-final 只要收到一个 vLLM output 就被记为 `success`，没有检查可见 final 是否非空。
- **判断——修复尚未通过 hard-failure 门。** 8 条空答案必须全部计错；其中
  `NOFINAL_026/028/034` 被 Judge 错判为 `True`。把这 3 条强制覆盖为错误后，Judge 正确数由
  37/48 降为 **34/48**。本轮是故障定向集，不用于评价总体语义质量，但足以判定“无新增
  hard failure”不通过。

最直接的下一步不是继续调预算，而是让 `confidence_accepted` 也获得明确 answer cue，并增加
fresh-final 非空校验与一次有界重试；随后在同一 48 条上做至少 2～3 次重复，要求
`protocol_no_final_answer=0`，再恢复全集参数/YAML 实验。

## 1. 输入、实验身份与证据边界

权威输入与产物：

- 定向输入：[`quant_post_train_no_final_48.csv`](../../../debug/quant_post_train_no_final_48.csv)，
  由 run_08 旧 DTB 的 25 条和 run_09 fixed_v4 的 23 条历史失败组成；
- 客户端汇总：[`test_result/_SUMMARY.csv`](test_result/_SUMMARY.csv)；
- 客户端明细：
  [`dtb_quant_post_train_no_final_48_2026-08-13_07-11-48.csv`](test_result/dtb_quant_post_train_no_final_48_2026-08-13_07-11-48.csv)；
- 绑定审计：[`extraction_manifest.json`](extraction_manifest.json)、
  [`request_index.csv`](request_index.csv)；
- telemetry：`extracted/dtb_quant_post_train_no_final_48_2026-08-13_07-11-48/` 下的
  `requests.jsonl`、`controller_phases.jsonl` 和 `phase_summary.csv`。

根据用户说明，本轮仍使用 W8A8 量化、特定场景思维链压缩语料增训的权重，并保留
fresh-final、恢复预算耗尽诱导语。目录没有上传 `manifest.json` 和原始 `server.log`，因此无法
独立核对 checkpoint、adapter/server commit、实际 tokenizer 特殊 token 表、sampling/seed、
并发、cache 和服务器代码 SHA。已提取产物足以审计本轮 48 条的请求归属和算法轨迹，但本文
不会把恢复率的变化全部归因于某一项代码改动。

本轮没有 Vanilla 或旧实现的同轮对照，因此不做 E2E 收益排名；历史 run 只用于核对这 8 条
是否复现了相同终止签名。

## 2. 完整性与确定性协议检查

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

## 3. 实际路由、配置和 final 原因

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

## 4. 复现明细

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

## 5. 8 条失败的算法轨迹

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

## 6. 发生机制

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

## 7. Phase 账本

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

## 8. 下一步实验与验收

### 8.1 最小修复顺序

1. **让两个 final 原因都带 answer cue。** 保留 fresh-final，但不要让
   `confidence_accepted` 只追加裸 `</think>`；先复用已恢复的诱导语，或使用与 probe 一致、
   更短的 `Final Answer` cue 做 A/B。
2. **增加非空不变量。** 累积 fresh-final 的可见文本；若为空、仅有空白或仅由停止 token
   结束，则标记 `protocol_no_final_answer`，不得写 `completion_status=success`。
3. **增加一次有界重试。** 第一次为空时追加更强 answer cue 再 fresh-generate 一次；仍为空
   才触发可审计 fallback。不要优先用 `ignore_eos` 强行续写，因为它可能造成特殊 token 泄漏
   或无界生成。
4. **不要直接复用 50-token probe 作为最终答案。** probe 有独立提示且可能被 50-token 上限
   截断；除非单独证明质量，否则只能用于置信度或异常兜底实验。

### 8.2 48 条定向回归矩阵

| 变体 | 目的 |
| --- | --- |
| A：本轮逻辑复跑 | 估计当前随机重现波动 |
| B：两个 final 原因统一 answer cue | 验证裸 `</think>` 是否为主要触发条件 |
| C：B + 非空校验 + 一次重试 | 验证协议不变量能否真正闭合 |

每个变体保持同一模型、sampling/seed、并发和 cache 条件；B/C 至少重复 2～3 次。优先验收：

1. 每轮 48/48 都有非空 final，`protocol_no_final_answer=0`；
2. 不再由 Judge 决定空答案是否有效；协议失败先强制计错；
3. telemetry 能区分首次成功、重试成功和 fallback，并记录首 token/停止 token 的解码；
4. B/C 通过后，再回到 633 条全集检查质量非劣、hard failure 和 E2E；在此之前不继续搜索
   fixed 预算，也不收口量化增训 YAML。

## 9. 证据强度与变更记录

- 48 条绑定、8 条复现、scene/预算、probe 置信度、final 原因、`248044` 终止、Judge 误判和
  phase 账本为**实测**；
- 两个 final 原因的后缀选择、fresh-final 非空检查缺失为**当前工作区源码确定**；
- “量化增训使裸 `</think>` 后立即终止的概率升高”为**待实验假设**；应由统一 answer cue
  的 A/B 和目标 tokenizer 打点证伪或确认；
- 由于缺少 manifest，服务器代码与当前工作区逐 SHA 一致、`248044` 的具体 token 文本均未
  得到独立证明。

| 日期 | 变更 |
| --- | --- |
| 2026-08-13 | 初版：完成 48 条历史空 final 定向重测审计，确认 8 条以相同 one-token stop 签名复现，并定位到未恢复诱导语的 `confidence_accepted` fresh-final 路径。 |
