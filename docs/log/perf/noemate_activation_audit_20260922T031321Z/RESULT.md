# Noemate direct750 激活诊断：可以进入 off 独立验证

后续：[884a00 off冒烟与小集结果](../noemate_guardrail_main_20260922T070000Z/RESULT.md) 已完成。off输出长度控制与319加速得到支持，但178的final遗漏、399数值反例及短请求E2E非劣证据不足，下一步转定向复验；以下保留本轮phase的历史裁决。

## 1. 结论与阶段门

**实测：本轮机制诊断通过，可以继续下一步；还不能结束实验并宣称稳定加速、短请求不变慢或质量不下降。**

- 12 个请求全部与服务事件对应，7 次实际 `budget_final`，5 次 `native_complete`。
- 7 次接管均使用 check=hard budget=750，在服务计数 **751–753 token** 触发；abort 后接收尾部为 0。均直接进入 `budget_exhausted` final，没有 probe。
- 5 次自然完成保持一个 child、abort=0、没有 final 重启。其中 line21/365 共4次是预选短请求；line399第一次也在阈值前自然结束。
- 12 次都有非空正文或工具调用，单次 terminal；自动协议/工具 schema 检查均通过。但 **line353第二次发生 final 截断**，不能称全部答案完整。
- `model_work_complete=false` 的7次是 abort 路径的保守账本标记，不是请求失败；本轮不能给出完整模型工作量或精确算力节省。

这已满足上一轮“至少一个真实 budget_final 且有完整 final，短路径没有额外 child”的阶段门。下一步保持750，做小集合 off 配对；无需继续完整池扫阈值，也无需先扩大到端到端 Agent 实验。

## 2. 输入与实验身份

- [客户端 manifest](direct750/manifest.json)：6例×2次，只有 DTB，stream=true，seed=20260918，原请求 sampling/max_tokens；请求顺序为逐例顺序发送，无 Vanilla 对照、无独立预热。
- [client_commit.txt](client_commit.txt)：`9c502bd0fc5630e0052056679b064d14e3213eca`；[工作区 patch](client_worktree.patch) 仅将 OBSERVABILITY off 改为 phase。仓库 `fd60e41..9c502bd` 的 dtb/ 与 replay 实现无差异。
- [start.sh](start.sh)：模型路径 `NetGPT-Qwen3.6-27B-40K-R27.1.5-MTP`，TP4/DP1，APC on，qwen3 reasoning/qwen3_coder tool parser，端口9001。不在本地执行该启动脚本。
- 用户确认未提及的环境沿用前轮；本轮 phase 由真实事件及配置 patch 共同支持。缺独立服务进程源码身份、已安装依赖/上游 commit 快照，不能把客户端 SHA 当作完整部署证明。
- [serverlog](serverlog) 是用户 grep 后输出，只有38行事件，不是完整 server.log。足够核对这12次的预期 phase/summary；不能证明全服务没有丢事件告警、错误或其他请求。
- 输入池归一化 LF SHA256：`1bf3eadc296597227d9d0bb7dde902041a255b5862bc6b63d74676fbf6560be6`。集合来自上一轮冻结选择，不是随机全流量或独立质量测试集。

## 3. 归属与完整性

离线重组3256条SSE事件，逐请求核对请求 body SHA、事件序号、唯一 DONE、summary和paired_summary，全部一致。12个客户端 `server_request_id` 与日志 parent 完全对应，无额外 parent；每个parent恰有1条summary。

| 路径 | 请求 | decision | native_passthrough | final_answer | confidence_probe |
| --- | ---: | ---: | ---: | ---: | ---: |
| 自然完成 | 5 | 0 | 5 | 0 | 0 |
| 直接预算接管 | 7 | 7 | 7 | 7 | 0 |
| 合计 | 12 | 7 | 12 | 7 | 0 |

自然完成不解析完整预算，`budget_resolution=not_required_native`，没有 decision 是合法设计；不是缺失决策日志。接管的7次均为 `decision_branch=fixed`，预算750/1/4/256，confidence=.95；hard limit来源 `fixed_budget_tokens`，阈值来源 `fixed_check_length`。scene包括general/planning/code_generation，不改变这次固定预算，不作为场景匹配验收。

## 4. 请求级证据

长度为服务实际计数；E2E仅描述本轮，**不能和旧off时延计算压缩率**。

| pool行 | attempt | 服务思考token | 实际路径 | child数 | E2E秒 | 完成情况 | serverlog行 |
| ---: | ---: | ---: | --- | ---: | ---: | --- | --- |
| 21 | 1 | 84 | native_complete | 1 | 18.492 | tool_calls | 1–2 |
| 21 | 2 | 111 | native_complete | 1 | 4.338 | stop | 3–4 |
| 178 | 1 | 753 | budget_final | 2 | 25.391 | stop | 5–8 |
| 178 | 2 | 753 | budget_final | 2 | 13.307 | stop | 9–12 |
| 319 | 1 | 753 | budget_final | 2 | 35.048 | stop | 13–16 |
| 319 | 2 | 753 | budget_final | 2 | 24.134 | stop | 17–20 |
| 353 | 1 | 751 | budget_final | 2 | 16.568 | Rag工具调用 | 21–24 |
| 353 | 2 | 753 | budget_final | 2 | 27.025 | length，正文中断 | 25–28 |
| 365 | 1 | 175 | native_complete | 1 | 17.690 | stop | 29–30 |
| 365 | 2 | 48 | native_complete | 1 | 1.571 | stop | 31–32 |
| 399 | 1 | 698 | native_complete | 1 | 22.687 | stop | 33–34 |
| 399 | 2 | 753 | budget_final | 2 | 16.576 | stop | 35–38 |

7次接管：`activation_skipped_reason=null`、abort_count=1、`abort_terminal_seen=true`、`post_drain_branch=budget_final`、`final_answer_reason=budget_exhausted`；没有自然终止抢先、没有handoff safety failure。阈值超出1或3，drain超出0。此证据说明本轮接管及时，不把观测到的3-token最大超出提升为所有并发/负载下的硬保证。

本地重新编码reasoning与服务思考计数11/12相同；353第二次是754 vs753。文本重编码存在分词边界差，不以750以上一两个token判定失败。它也无法解释上一轮off最大1846的现象；本轮并未确定上一轮原因。

## 5. 账本与性能边界

| phase | calls | wall秒 | queue秒 | prefill秒 | decode秒 | 已记录generated |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| decision | 7 | 0.173 | — | — | — | 0 |
| native_passthrough | 12 | 156.893 | 0.000263 | 69.260 | 87.487 | 7025 |
| final_answer | 7 | 64.545 | 0.000081 | 11.838 | 52.123 | 4475 |

19个模型phase的queue/prefill/decode均有值（19/19）；decision这些字段不适用。phase wall合计221.611s，客户端E2E合计222.826s，差1.215s（0.545%），包含phase之外的服务、传输/解析等耗时，不能全归为控制器CPU。queue+prefill+decode与wall不强制相等。

服务summary记录思考6385、final5115、hidden probe 0，共11500。客户端completion11507；7次接管每次多1个合成reasoning boundary，占外部额度但不是模型生成。final phase的 `answer_limit = completion_limit - emitted_before` 全部闭合。answer cue属于重启prompt，不属于生成量；例如319的prompt从32885变33662，包含753前缀和24-token续写后缀。

**源码确定：** [phase_recorder.py](../../../../dtb/observability/phase_recorder.py) 的 `model_work_complete = not abort_terminal_seen` 使所有主动abort请求标记false。生成计数对应控制器收到的输出，不能证明abort竞态下引擎已经执行但未回传的工作为零；drained_tail=0也不能证明没有这类工作。故11500只能叫“已记录生成量”，不能叫完整引擎工作量。本轮零probe可由全部child归属及phase结构确认，不需用输出长度猜。

短请求同child完成是机制证据，不是零性能成本证明。首轮line21的prefill为15.537s、cached_prompt=0，第二轮prefill=.457s；本轮长短E2E差异明显受缓存/轨迹影响，不计算候选加速排名。

## 6. 输出完整性与语义

- 客户端错误0、自动协议异常0、工具schema/语义规则标记0、空final（正文和工具皆空）0。自动脚本的“12/12通过”不代表正确率100%。
- **353/a2**：final额度1294，正好生成1294并以length结束，正文停在“模块间UIPC”。这是确定的答案截断；原请求总额度2048已被正确遵守。暂不能归因为DTB新增退化，因为没有同轮Vanilla，前轮此例本就高频触顶。
- **353/a1**：返回Rag查询而不是直接告警清单。工具结构有效，但不能把短工具调用当作完成同等业务工作后的加速；下一轮必须分开记录动作类型，保留该请求。
- **399两次**：均返回总和338.001%、均值84.500%（84.50025%的三位小数），并标注原数据日期2026-07-30；这次有限算术检查通过，其中a2为实际预算接管。不能据两次推导750对所有语义安全。
- 319清单覆盖、178分类正确性及353行为仍需独立审阅。按用户此前范围，本轮不执行参考答案评分，因此最终报告只能给性能/可控性与有限语义检查结论，不能声称业务正确率不下降。

## 7. 下一轮：固定750，小集合形成结论

**阶段判断：允许继续；无需再重复本轮phase。** 有两个尚未闭合的独立命题：off状态仍然接管长请求，以及短请求在可接受容差内不变慢。

1. 切回实际 `OBSERVABILITY.mode=off` 并重启同一服务；保持模型/APC/采样和部署不变。先做下述smoke，优先检查319的DTB思考是否仍在约750附近。若再次出现明显超过阈值、例如超过800且并非文本分词微差，**暂停main，先排查off/phase路径与实际进程配置差异**；800只是本轮异常筛查线，不是产品预算上限。不要将phase生效自动移植为off生效。
2. 长集固定319/178/259/353，加399边界；每例预热后10对原始采样V/DTB，顺序交替。按案例报告E2E差、思考P50/P95/max、completion、完整final比例及动作变化；优先判断319是否重复获益，353截断是否改善。保留所有变慢、length和动作变化，不以事后筛选的干净子集代替整体。
3. 短集固定21/321/365/394：greedy Vanilla A/A每例20对测背景波动，再greedy V/DTB每例20对；最后原采样V/DTB每例20对检查实际行为。输出不同不能证明控制器必然导致变化，但也不能删除变长请求来证明不退化。同输出比较只作辅助。
4. 事先冻结工程门：建议每案例平均E2E额外耗时的单侧95%上界≤max(20ms,1% Vanilla均值)，首输出上界≤10ms；同时保留全样本P95与完整性/工具行为。此为实验提案的严格非劣容差，非用户已确认SLA；报告同时提供绝对差/区间，避免只给通过标签。若噪声导致区间跨门，应报告证据不足，增加独立block重复；不放宽门或称显著性不足即等效。greedy也不保证完全确定性。
5. 若本轮750实际接管但353仍不完整，先判断总completion额度与答案篇幅，不能假定提高到1024一定改善（它反而占用更多答案额度）。只有语义审阅显示750过早，再小集对照1024；调整max_tokens须另设双边相同额度实验，不能只给DTB更多额度。

上述验证能形成“本冻结样本集、当前部署、单并发”的长请求收益/思考上界与短请求性能结论。不能承诺有限实验必然得到正向结论，也不替代并发5或完整Agent任务验收；这些应在单请求结论成立后继续。完整算力节省还需补引擎abort工作核算，**不阻塞本轮客户端时延结论**。

### 服务器执行命令（本地未执行）

在仓库根目录操作。将YAML mode改回off并重启实际服务，保存实际启动命令、服务commit/dirty、YAML/patch SHA及vLLM/vLLM-Ascend/torch/torch-npu/CANN身份；脚本只检查磁盘配置，不能替代进程重启。

```bash
bash docs/log/perf/noemate_activation_audit_20260922T031321Z/run_off_validation.sh smoke
```

smoke共18请求（含6预热）；读取输出目录中319的两次DTB reasoning，按相同tokenizer计数，并检查全部final/错误；与本轮751–753的机制结果核对。确认没有重新出现off长思考异常后执行：

```bash
bash docs/log/perf/noemate_activation_audit_20260922T031321Z/run_off_validation.sh main
```

main共610请求（580测量、30预热），各例立即预热后测量；A/A按相邻attempt配成20对。两个阶段独立目录、不覆盖结果；命令失败时保留输出、排查后续跑，不能静默跳过。A/A与A/B分块执行，需记录时间/负载，不将跨block漂移解释为算法开销；出现漂移时增加交错block，而不是直接得出短请求退化。

回传每目录manifest、summary、paired_summary、chunks（可切片）、根部配置/身份/选择文件；原始输出用于人审353/319/178与工具动作。off不要求DTB成功phase日志。无需传回历史11GB代理日志。

## 8. 复算与产物

```bash
python docs/log/perf/noemate_activation_audit_20260922T031321Z/analyze_activation.py
```

脚本依赖本仓库前轮audit helper、replay输入池和本地tokenizer，仅做离线读取。生成 [audit_summary.json](audit_summary.json)、[request_audit.json](request_audit.json)、[extracted_events.jsonl](extracted_events.jsonl)，保存source SHA和原日志行号。原始日志/自动生成direct750/RESULT均未改写。本目录不是CSV提取器所需的_SUMMARY.csv布局，故按replay的真实request ID专门提取，没有伪造CSV或假称执行通用提取器。
