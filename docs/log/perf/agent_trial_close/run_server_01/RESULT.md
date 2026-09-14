# Trial 显式闭合实验：326aff3 结果分析

分析日期：2026-09-10。对照定义：A=`open` 的 DTB，B=`closed` 的 DTB；各部署另有自己的 Vanilla。原始自动报告保留在各子目录，不覆盖其原始裁决。

## 1. 结论与当前决策

**追加 `</think>\n\n` 是值得保留并继续验证的候选：它明显改变了 probe 的停止和接受行为，尤其解决了标题请求中大量无效试答的问题。但本轮不能宣布生产可用或真实 Agent 端到端优化已达成。**

- **实测：**首 probe 接受率从 **3/39（7.69%）→24/37（64.86%）**；最终经 confidence 接受的请求从 **12→33**；probe 总次数 **82→55**，隐藏 token **3,026→1,008**，probe wall **89.737→41.269 秒（−54.01%）**。
- **实测：**32 个标题回放的 DTB 请求总时延 **619.073→446.642 秒（−27.85%）**，平均每请求省 **5.389 秒**；闭合版相对本部署 Vanilla 为 **+25.45%** 收益。标题也属于收益，不应排除。
- **实测差异、非纯因果收益：**全部 52 个 DTB 请求总时延 **1,233.642→776.768 秒（−37.03%）**。其中 **46.54% 的总差值来自输入第 2 行**，而 B 的该行四次全部原生旁路，不能把这部分当成 close trial 的直接收益。
- **可用性未通过：**B 第 7 行第 3 次在第二个 probe 接受后，fresh-final 返回未声明的工具名 `rest\n</parameter` 和空 arguments。这是实际协议故障，不是可忽略的语义差异。A 另有自动工具漏检的标题 `</think>` 泄漏和重复输出；A 的 Vanilla 还有一条空标题。两侧都存在问题不等于 B 的新故障可接受，也不能据此证明新增标记就是根因。
- **下一步：**暂不同时调低 gate 或 trial 长度；优先对上述 fresh-final 故障定向取证，并做反向顺序 B→A 的同配置复验。协议问题收口后，把 closed trial 作为候选进入 phase-off 真实 Agent 全量实验。

## 2. 实验身份与证据边界

输入和执行方案见 [trial_close_experiment/README.md](../../agent_run06/trial_close_experiment/README.md)。唯一行为变量为原 trial cue 末尾追加 `</think>\n\n`；不改变 fresh-final、不复用 trial。

| 项 | 已核实内容 |
| --- | --- |
| 结果提交 | `326aff3`，本目录 open/closed 的 summary、chunks、manifest 和服务器 phase 分片 |
| 样本 | 同一 13 条完整 HTTP 上下文，8 个标题输入、5 个业务输入；每输入 4 次，每部署 V/DTB 各 52 次，合计 208 次 |
| 输入 SHA-256 | `dffd854e4e98add09eb1ad700f1cb4126df76e865476b732312e5972f3f4d5e4` |
| 实际请求配对 | 重建每条请求并验证 chunks 中 body hash；208 条验证通过。去掉唯一 request_id 后，104 个跨部署同 line/attempt/variant 的请求体 hash 一致 |
| 请求参数 | 流式、单并发；budget=20,000、T=850、stride=500、N=5、min=1、gate=.93；trial 上限 50 |
| 实际预算 | A 39、B 37 个介入请求均为 fixed，四维预算/阈值/来源一致；旁路不解析预算是预期行为 |
| 实际 policy | A 全部 phase 为 `dtb-algorithm-v4`；B 为 `dtb-algorithm-v4-trial-close-exp1` |
| 额外 prompt token | 首 probe 的 `prompt_tokens − native_prompt_tokens − native_generated_tokens`：A 全部为 21，B 全部为 23，符合追加后缀增加 2 token 的实测表现 |
| 可见运行时指纹 | 全部 208 条 SSE usage 的 fingerprint 都是 `vllm-0.23.0-tp8-f063d18e` |
| 观测模式 | 有完整 phase 与 request summary，按 phase-audit 处理，不作为 off 正式排名 |

限制：上传的是回放 manifest，**不是完整部署 manifest**。实际 vLLM/Ascend commit、PyTorch/torch-npu/CANN、模型权重 hash、YAML/已应用 patch hash、默认 sampling、cache 初始状态等没有完整归档；fingerprint 不能替代这些信息。未显式设置 seed，相同 attempt 只是重复编号，不是同一随机轨迹。`alternating` 交替的是每部署内 V/DTB，不是跨部署 A/B。A 开始时间 07:08 UTC，B 为 08:44 UTC，时序效应未被反向块控制。

因此本报告可以确认日志覆盖、实际分支及实测行为差异；不能将跨块全部 E2E 差异归因给新增两个 token，也不能声称 holdout 泛化或全任务正确率提升。没有原始 trial 文本、逐 token logprob 或完整 parser 前流，**不能直接证明 A 的 trial 一直在思考、B 的 trial 全部在回答**。

## 3. 完整性与归属审计

| 项 | A/open | B/closed |
| --- | ---: | ---: |
| 客户端请求 / HTTP 200 | 104 / 104 | 104 / 104 |
| 唯一 DTB parent / request summary | 52 / 52 | 52 / 52 |
| phase 事件 / 模型 child | 282 / 243 | 203 / 166 |
| 原生同 child 完成 | 13 | 15 |
| abort 后进入 probe | 39 | 37 |
| decision / 已解析有效预算 | 39 / 39 | 37 / 37 |
| summary 标记 model_work_complete=false | 39 | 37 |

分片 334=282+52 行、255=203+52 行均为目标事件；每个事件归属到 DTB 白名单，无串入 Vanilla 或其他请求。每 parent 的 generated token 总和、child 数和 summary 闭合；emitted token 与客户端 completion 全部一致。没有独立 writer dropped-event 计数，但本次已上传的逐请求账本闭合，没有发现事件缺口。

同编号两次均介入 33 对、均旁路 9 对、A介入/B旁路 6 对、A旁路/B介入 4 对。**新增 trial cue 在首次介入之后才被模型看到，不能解释首次介入前的轨迹差异。**

## 4. 对 probe 算法的实际影响

### 4.1 总体与标题/业务分解

| 指标 | A/open | B/closed |
| --- | ---: | ---: |
| probe 总次数 | 82 | 55 |
| 介入后最终 confidence 接受率 | 12/39（30.77%） | 33/37（89.19%） |
| 首 probe 接受率 | 3/39（7.69%） | 24/37（64.86%） |
| 首 probe 平均得分 | .7573 | .8944 |
| 全部 probe 平均得分 | .7741 | .9014 |
| 自然 stop / length | 34 / 48 | 43 / 12 |
| hidden token | 3,026 | 1,008 |
| iterative_think 次数 / token | 70 / 28,900 | 22 / 10,389 |
| final 原因：accepted / model-think-end / budget | 12 / 26 / 1 | 33 / 3 / 1 |

所有 probe 采用 `average_fallback`，没有 confidence unavailable 的 fail-open。当前分数是 sampled token 概率的算术平均，**不是答案正确的概率，也不是工具调用协议合法率**。源码见 [confidence.py](../../../../../dtb/reasoning/confidence.py)、[generation_states.py](../../../../../dtb/reasoning/generation_states.py)。平均分变化还混入了轮次组成变化，首轮指标比全轮均值更可比，但仍非固定 P/H 的受控对比。

| 分组 | A/open | B/closed | 解读 |
| --- | --- | --- | --- |
| 标题 | 31 个介入请求、63 次 probe、10 次接受；首轮 3/31 | 30 个介入请求、45 次 probe、28 次接受；首轮 20/30 | 对短最终答案的 gate 判别明显改善 |
| 标题 probe 自然 stop | 34/63 | 43/45 | 50-token 打满从 29 次降到 2 次 |
| 标题 hidden token / wall | 2,076 / 63.334 s | 508 / 29.760 s | hidden token −75.53%，wall −53.01% |
| 业务 | 8 个介入请求、19 次 probe、2 次接受；首轮 0/8 | 7 个介入请求、10 次 probe、5 次接受；首轮 4/7 | 数量小、上下文和执行动作波动更大 |
| 业务 probe | 全部 19 次 length=50 | 全部 10 次 length=50 | 尚未观察到完整业务试答自然结束；接受的是前 50 token 的分数 |

B 标题 probe 45 次中 **41 次不超过 13 token**，但仍有两个打满 50 的异常尾部。观察与“显式关闭 thinking 后，更容易直接生成短答案”假设一致；缺 hidden 文本时不将其升级为直接语义证明。

### 4.2 典型轨迹与反例

1. **第 10 行：场景任务分布式锁标题。**A 四次均介入，共 9 次 probe、0 次接受，累计思考 6,835 token；B 四次均第一轮接受，置信度均值 .9890，仅 4×8=32 hidden token，累计思考 3,403 token。平均 E2E 22.035→10.971 秒。B 标题均为正常主题标题，这是 close 改善 gate/停止位置的清晰样例。
2. **第 11 行：知识检索标题。**A 7 次 probe 只接受 1 次，B 6 次接受 4 次；B 全部自然 stop，平均 E2E 17.969→14.019 秒。并非每一条都会第一轮接受，仍存在较低分短答案。
3. **第 8 行第 3 次：仍会亏损的标题尾部。**B 在 5 个 probe 上得分 `.862981/.901893/.871516/.807290/.723476`，长度 `8/18/8/8/50`，全拒绝；累计思考 3,353 token，E2E 48.536 秒，同一输入另外三次仅约 10.5–10.8 秒。该输入均值反而从 A 的 17.094 秒变成 B 的 20.146 秒。这说明显式闭合不是精准停止的充分条件；“短完整候选反复被拒绝”的残余问题值得另行校准，不能同时改动本轮结论。
4. **第 9 行：结构化信息提取。**B 三次介入均第一轮接受（均分 .9938、每次 50 token），另一次旁路；四次都输出 JSON 代码块。A 只有两次直接输出 JSON，另外两次选择 write/read 工具。B 的行为更集中，但尚未逐字段校验参考答案；仅从响应类型不能宣布任务正确。

### 4.3 源码解释与成本

`child_prompt(TRIAL_PROMPT)` 复制主 prompt ID 列表，仅在隐藏 child 后追加后缀，不把 trial 内容或边界写回主 H。`reasoning_ended=True` 原本就传给引擎，它不替模型输入插入 `</think>`；这次额外改变的是**模型实际看到的 token 前缀**。

接受后仍用 `GUIDED_FINAL_PROMPT_V1` 新建 final child；原 trial cue 和 final cue **仍不完全相同**。因此本实验既不是 trial reuse，也没有证明高分 trial 的结构/答案能够原样转移到 fresh-final。新增标记不应直接泄漏到外层；若发生异常，需检查最终 child 的原始输出和 parser-facing 拼接，不能仅根据客户端形状锁定某一层。

实测多两个 prompt token，并未增加 child 类型或多一次提交。probe 平均 prefill **460.3→483.7 ms**，没有观测到下降；不能把这 23.5 ms 的跨块差异归因给两个 token。总 prefill 降低主要因为 probe 变少。隐藏 probe 目前 **64.47% wall 是 prefill**，继续压 trial 长度的收益空间已收窄。

## 5. 请求时延与工作量

### 5.1 总体：保留失败样本，只描述实测差异

正收益为更快；每格均使用固定 52/32/20 个请求，不静默删除错误。

| 请求集合 | A Vanilla 总秒 | A DTB 总秒 | A 对 V 收益 | B Vanilla 总秒 | B DTB 总秒 | B 对 V 收益 | B 对 A DTB 降幅 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 全部 52 | 920.087 | 1,233.642 | −34.08% | 1,131.080 | 776.768 | +31.33% | 37.03% |
| 标题 32 | 572.432 | 619.073 | −8.15% | 599.133 | 446.642 | +25.45% | 27.85% |
| 业务 20 | 347.655 | 614.569 | −76.78% | 531.946 | 330.126 | +37.94% | 46.28% |

DTB P50 **20.182→10.977 秒**，P95 **68.837→31.300 秒**；同编号 B 比 A 更快 **40/52**，标题 **26/32**；13 输入均值有 **11/13** 改善。13 输入等权相对降幅 **21.06%**，不等于 9 个真实 Agent 任务等权收益。每输入四次时，请求加权平均秒差与输入等权平均秒差相同。

| 输入行 / 原场景角色 | A DTB 平均秒 | B DTB 平均秒 | probe 次数 A→B | confidence 接受请求 A→B |
| --- | ---: | ---: | ---: | ---: |
| 1 / 2 标题 | 21.410 | 12.418 | 9→5 | 2→4 |
| 2 / 2 子 agent | 72.352 | 19.196 | 2→0 | 0→0 |
| 3 / 3 标题 | 17.830 | 15.773 | 7→7 | 0→4 |
| 4 / 3 主 agent | 12.631 | 9.543 | 2→0 | 0→0 |
| 5 / 26 标题 | 17.780 | 13.752 | 7→5 | 1→2 |
| 6 / 26 子 agent 3767… | 37.687 | 20.206 | 12→4 | 1→0 |
| 7 / 26 子 agent 8176… | 4.968 | 9.343 | 0→3 | 0→2（含故障） |
| 8 / 28 标题 | 17.094 | 20.146 | 7→8 | 2→3 |
| 9 / 28 子 agent | 26.004 | 24.243 | 3→3 | 1→3 |
| 10 / 29 标题 | 22.035 | 10.971 | 9→4 | 0→4 |
| 11 / 34 标题 | 17.969 | 14.019 | 7→6 | 1→4 |
| 12 / 35 标题 | 16.699 | 9.425 | 6→3 | 2→3 |
| 13 / 37 标题 | 23.951 | 15.156 | 11→7 | 2→4 |

**为什么不能将总体 37.03% 当作 close 的因果收益：**

- Vanilla 自己也从 920.087→1,131.080 秒（+22.93%），completion 从 85,693→107,584（+25.55%）。主要波动在业务，不能只解释为服务器变慢。
- 第 2 行 A 多数响应直接生成多个完整文件；B 前三次先 grep API，仅最后一次写文件，且四次均未介入。该行贡献 **212.624 秒**，占全部 A/B 差值 **46.54%**。工具选择和正文长短改变，不等于完成同样工作更快；新增 trial 在这些 B 请求中没有运行。
- 排除第 2 行四对后的 48 对仍有 **25.87%** A/B 观测降幅，说明收益信号并非全来自它；但仍有业务轨迹差异和协议问题，不由此完成因果或质量裁决。
- 排除两条人工识别的 DTB 协议异常对应配对（第 12 行第 2 次、第 7 行第 3 次）后，50 对 A/B 观测降幅为 **38.68%**。这是敏感性分析，不是“删掉故障后通过验收”；也没有把所有业务语义判为正确。
- 标题的 Vanilla 总时延只漂移 +4.66%，且 B 全部给出可读标题，故其收益证据相对业务更集中、更有说服力。

本轮是 13 条孤立请求回放，不执行返回工具、不继续 agent，也没有任务 start/end 或编译评测。标题请求的节省可以贡献真实端到端收益，但需要在实际任务时序上确认是否位于关键路径；不能把本轮回放秒数直接套到 9 任务。

### 5.2 phase 时延账本

下表均为秒；全部模型 phase 的 queue/prefill/decode 字段均已知，decision 不适用这些模型指标。

| phase | A wall | B wall | A prefill | B prefill | A decode | B decode |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| native_passthrough | 693.402 | 533.245 | 19.010 | 18.937 | 673.685 | 513.658 |
| confidence_probe | 89.737 | 41.269 | 37.741 | 26.605 | 49.313 | 13.082 |
| iterative_think | 343.924 | 127.341 | 26.747 | 8.429 | 313.461 | 117.672 |
| final_answer | 98.136 | 66.880 | 15.464 | 14.420 | 80.826 | 50.240 |
| decision | 3.513 | 3.186 | — | — | — | — |
| 合计 wall | 1,228.712 | 771.920 | | | | |
| 客户端 E2E | 1,233.642 | 776.768 | | | | |
| 客户端减 phase | 4.931（0.40%） | 4.848（0.62%） | | | | |

模型 queue 合计 A **3.077 ms**、B **2.345 ms**，不是主要开销。probe 的实测省时 **48.468 秒**，其中 prefill 减少 **11.137 秒**、decode 减少 **36.231 秒**；iterative_think 另减少 **216.584 秒**。这些是账本差值，不能把所有后者视作同前缀反事实收益。native phase 还包含旁路的最终正文，不能全部叫作思考时间。

### 5.3 token 账本

| 指标 | A | B |
| --- | ---: | ---: |
| 可见 thinking | 67,889 | 47,993 |
| hidden probe | 3,026 | 1,008 |
| final（含原生旁路正文） | 41,126 | 17,741 |
| 已记录模型生成合计 | 112,041 | 66,742 |
| 对外 completion | 109,028 | 65,768 |
| 可见 thinking+final | 109,015 | 65,734 |
| emitted 减可见生成（控制器结构边界） | 13 | 34 |

phase generated 总和逐请求与 summary 完全一致；completion 不包含 hidden probe，并额外计入控制器结构边界。A/B 的 39/37 个 abort 请求 `model_work_complete=false`，因此模型生成合计应叫**已记录工作量下界**，不是硬件真实总工作量精确值；不能拿它宣称精确的 40.43% 总算力压缩。观察到可见思考减少 29.31%，但 final 长度也大幅变化，需结合动作语义。

## 6. 可用性与人工审计

### 6.1 B：真实的坏工具调用，发生在 accepted fresh-final

请求：`chatcmpl-dtbtool-l7-a3-dtb-trial-closed-b`，原始输入为 case26 子 agent 的 RestCaller API 检查上下文。

证据： [closed/server_filtered_part_01](closed/server_filtered_part_01) 第 114–120 行；[closed/chunks.jsonl](closed/chunks.jsonl) 第 20144–20165 行；[closed/summary.jsonl](closed/summary.jsonl) 中同 request ID。

路径：

```text
原生生成 851 token → abort，drained_tail=0
probe 1：50 token，.730739，拒绝
iterative_think：500 token
probe 2：50 token，.983006，接受
fresh-final：33 token，stop → 外层 finish_reason=tool_calls
```

外层正文先表示要重新读取文件，随后输出 `<Thinking Explorer>`；第 20162 行第一次且唯一一次 tool delta 已经是：

```json
{"function":{"name":"rest\n</parameter","arguments":""}}
```

因此不是回放器把多个正确工具片段拼错：**SSE 本身就给出非法工具名和空 arguments**。请求的工具 schema 中没有这个名称；客户端会遇到未知工具/无效 JSON，实际任务可能停止或进入错误恢复。HTTP 200、单 terminal、DTB summary `success` 只证明传输/控制器返回完成，不证明下游调用有效。

**已确定：**错误在接受后的 final 路径可见，不是旁路；probe 2 为 length 截断，.983006 只衡量前 50 token，不能为全工具调用背书。没有观测到整段 prompt/H 回显。

**尚未确定：**模型本来生成了坏 XML、final 的 parser-facing delta/token 拼接有缺陷，还是 parser 将原始合法结构解析错误。当前上传日志只有 phase 和 parser 后 SSE；缺失 raw final/text/token IDs，不能裁决。新 cue 不会直接透传 trial，所以不能仅凭 `<Thinking Explorer>` 推断它来自隐藏试答。关闭 trial 改变了停止时机，可能暴露既有 fresh-final 风险，也可能新增高分误停样本；这两种解释都需要取证。

### 6.2 A：原版也有自动审计遗漏，不能称为 52/52 全面正确

- **A/DTB 第 12 行第 2 次：**标题正文为 `IncidentManage判空与事务同步代码评测\n</think>\n\nIncidentManage判空与事务同步代码评测`。在 [open/chunks.jsonl](open/chunks.jsonl) 第 38798 行，服务明确把 `</think>` 放进 `content`；不是分析器展示错误。该请求 probe=.957096 接受，final 生成 24 token，见服务器第 279–283 行。标题不应出现边界字面量且重复输出，应作为边界泄漏/输出契约异常处理。没有 token ID，不能进一步断言该字面量一定来自结构 token 248069。
- **A/Vanilla 第 10 行第 2 次：**173 completion token 后自然 stop，只留下截断在 `concurre` 的 reasoning，content 为空，无 tool call。不是最大 32,000 token 截断。已确认未交付标题，但模型/停止/parser 根因尚不明确。

自动工具只检查部分 XML/role/tool 规则，没有完整的 title 正文、think-end 泄漏和必需最终答案检查；`expected_tool_names=[]` 也不会验证返回工具名属于声明工具集合。这解释了其 A 报告 `latency ranking valid=True` 与人工质量门不同。**本次仅补分析侧审计，不修改原始报告或生产 parser。**

### 6.3 质量结论

| 组别 | 自动协议错误 | 人工补充 |
| --- | ---: | --- |
| A Vanilla | 0/52 | 1/32 标题为空，任务输出不合格 |
| A DTB | 0/52 | 1/32 标题泄漏边界并重复；不是全面协议通过 |
| B Vanilla | 0/52 | 32/32 标题非空、单行、≤50 字符，未见明显主题偏离 |
| B DTB | 1/52 | 32/32 标题非空、单行、≤50 字符，未见明显主题偏离；另有真实坏工具调用 |

业务 20 条没有真实执行工具/编译/测试或逐字段 Judge；不能把自动 `semantic deviation=0` 解读为业务正确率 100%。代码请求存在“写文件/检查 API/只汇报完成”的行为差异。A/B 都有异常，只能否定“问题一定由 close 新引入”的简单判断，**不能相互抵消不同的错误类型或作为生产通过依据**。

## 7. 下一轮实验方向

1. **冻结 closed trial 候选，先补故障取证。**保持 gate=.93、trial=50、T850 及其余参数不动。优先复用输入第 7 行，在 A/B 下重复回放，采集目标 parent 的 raw final child 和每个 parser-facing delta 的 text/token IDs，以及 parser 后 SSE；不只依赖 `--enable-log-outputs`。顺带覆盖 A 第 12 行和 Vanilla 第 10 行，区分协议问题与空输出问题。
2. **固定流回归裁决 parser。**捕获坏输出后，将同一原始 token/text 序列、delta 切分和初始 reasoning/parser 状态送入同版 parser。原始流已坏则定位模型/提示与停止路径；原始流合法而 parser 后坏则修 parser/拼接。仅拿 parser 后 SSE 重放不能完成这项裁决。若能捕获同一 P/H，再对旧/闭合 trial 做 fixed-prefix 比较，以去掉介入前轨迹随机性。
3. **反向 B→A 复验，重点报告标题。**继续原 13 输入，每输入至少 4 次，冻结部署身份/默认 sampling/cache 规则；每块保留 V/DTB，重新确认 suffix 23/21 与 policy。对第 2 行等旁路动作漂移单列，不计作 close 的直接收益。两种 gate 的实际质量都未判定前，不做新的阈值/长度大网格。
4. **成本后续优先级：先减少错失的接受点，再考虑 trial=10。**闭合版总 probe wall 41.269 秒，其中 prefill 26.605 秒。固定这 55 次历史调用和生成速率、假设前缀与后续轨迹不变，trial 从 50→10 最多裁掉本批已记录的 516 token，对应线性 decode 估算省 **6.790 秒（仅占 B E2E 0.87%）**；同时会截断 10 个本来能自然结束的 trial。20-token 则估算省 4.766 秒、截断 1 个自然 stop。它们不是新策略实测收益，得分与后续轮次都可能改变。现在不宜以“10 token 更便宜”替代完整候选质量审计。
5. **通过协议门后做真实 Agent phase-off。**closed trial 与 Vanilla 相同 9 场景、并发/采样/环境/日志规则，保留标题收益，评估相同正确任务的 task start/end 和关键路径。已知坏流未收口前，可继续隔离排障，不能宣布生产非劣或对外排名。

## 8. 复算与产物

```bash
python -X utf8 docs/log/perf/agent_trial_close/run_server_01/analyze.py
```

[analyze.py](analyze.py) 只读取本地证据并输出 [analysis.json](analysis.json)，不发送模型请求、不执行模型返回工具。复算包含请求体 hash、104 个规范化跨部署配对键、parent 覆盖、policy/预算断言、phase/summary/usage 对账、分角色/逐输入比较和人工审计候选。分片与 summary 的行号保存在 request_comparison 中。模型性能和故障取证仍需服务器执行，本机没有推理设备。

| 日期 | 变更 |
| --- | --- |
| 2026-09-10 | 分析 326aff3：闭合 trial 的接受率、标题收益与成本显著改善；剥离未触发请求的动作漂移，发现 B 坏工具调用和 A 标题边界泄漏/Vanilla 空标题，保持生产验收未通过并规划定向取证。 |
