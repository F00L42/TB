# DTB 在 Agent 多轮工具调用中的工程攻关复盘

**覆盖时间：2026 年 8 月 13 日至 9 月 4 日。** 本文汇总已有服务器实验、排障设计和当前源码，不新增模型实验。配套材料：[邮件正文](DTB_Agent集成验证_邮件正文.md)、[一页时间轴](DTB_Agent集成验证_时间轴.pptx)。

DTB 接入 Agent 的难点，在于把多次内部生成组织成一个对外连续、可解析的 assistant 回合。思考可能自然结束，也可能直接进入工具调用；投机解码可能一次提交多个 token；工具参数可能包含代码、JSON，甚至字面量 `</think>`。这些因素叠加后，任何边界处理偏差，都可能让 Agent 收到 XML 正文、残缺参数或错误的结束信号。

本次工作逐层解决了重复生成与 role 尾流、工具前缀丢失、JSON 收尾遗漏、最终答案误截断、字符串停止碰撞，以及思考正文尾段丢失等问题。**截至 9 月 4 日，在最新定向回放中，Vanilla/DTB 各 60 条响应的工具流协议异常均为 0，60 条 DTB 的公开思考正文全部保真。** 历史实验还在真实 MTP 环境验证过参数与闭合标签同批的故障修复，整个方案没有把关闭投机解码作为验收手段。[S1][S6]

这里的“观察不到问题”，指本轮覆盖范围内没有再观察到框架传输与解析故障。模型选错工具、缺必填参数、Agent 最终任务质量和时延收益分别验收，不能由协议通过直接推出。

## 1. 工程目标：内部允许多阶段，对外保持一个完整回合

Agent 会反复执行“模型生成工具调用、工具返回结果、模型继续推理”。一次工具调用损坏，可能触发重试、污染后续消息，或者让报告生成和评测流程无法继续。只检查 HTTP 200、是否出现任意 tool call，都不足以判断接入成功。

DTB 在内部引入 thinking、隐藏 confidence probe 和 final answer 等阶段。它需要维护以下输出契约：

| 契约 | 对 Agent 的实际意义 |
| --- | --- |
| 一个外部请求对应一个连续 assistant completion | 工具调用开始后，不再额外启动一个独立回答覆盖或拼接已有输出 |
| reasoning、content、tool_calls 正确分流 | 工具 XML 不进入普通正文，思考正文也不能被边界处理吞掉 |
| text 与 token IDs 的关系符合原生 parser 约定 | 续写 prompt 与解析器不能对“哪些结构已经生成”产生不同认识 |
| 保留最终源输出的正文、token 与终止字段绑定 | 最后一批参数不能因人工拆分 terminal 而失去收尾机会 |
| 同一完整工具序列允许 0～N 个调用 | 多工具合法，跨内部 child 重启第二个 completion 不合法 |
| 最终参数 JSON 完整，工具 index 连续，结束唯一 | Agent 能重组并执行工具，不会把截断误判为成功 |

“保真”也不意味着 text 必须包含所有 token 对应的特殊标记：原生 detokenizer 可以剥离 stop marker 文本，同时保留边界 token ID。DTB 和 parser 必须正确处理这种合法差异。后期思考丢字问题正发生在这里。[S1][S5]

## 2. 时间线：逐层消除故障，扩大测试后继续收敛

日期来自排障文档、Git 提交和实验 manifest。早期归档日期按原复盘记载；8 月 31 日以后按对应 run 的创建或分析日期。下表保留各自分母，**不把不同样例、不同检查范围的数字拟合成统一生产失败率曲线**。

| 时间 / 证据节点 | 具体故障与观测 | 定位和处理 | 当时验证结果 |
| --- | --- | --- | --- |
| 8/13，`run_after_fix` | 100 条 DTB 中，旧检查器标出 98 条协议异常；48 条 XML 泄漏并带 role 尾流，50 条 tool delta 后继续 content，10 条 JSON 不完整，标签可能重叠 | 审计发现 thinking 已进入工具调用后仍继续 probe / fresh final；原“补回 XML 前缀”方案 0 次命中 | 另 2 条所谓 pass 也存在跨 child 重复生成，旧检查器有漏报；建立单 completion 验收 [S1 §2.1] |
| 8/14，`run_after_terminal_fix` | 修复终止语义后，仍有 13 条缺 `<tool_call>\n<`，9 条参数缺 `}` | 统一显式思考结束和隐式工具开始边界，进入同一逻辑回合的 continuation final | 78/100 完整通过；role 尾流、tool 后 content、terminal 后 chunk 均为 0 [S1 §2.1] |
| 8/14，`run_after_handoff_fix` | string stop 已截断 text，但 speculative batch 的后缀 IDs 进入下一 child prompt，导致续写跳过工具前缀 | 对停止边界做 text/token IDs 对齐，去掉未公开的后缀 IDs | XML 泄漏 13/100 降至 0/100；100 次对齐全部命中。总体 88/100 通过，仍有 12 条缺 `}` [S1 §2.1] |
| 8/17～19，原生 parser 反事实与 patch 回放 | 完整 XML 因分片不同，输出 JSON 相差最后一个 `}` | 先恢复 final source terminal 原样透传，再修原生 parser 的 parameter-close 同批处理 | 180 对回放中，170 条含工具的 DTB 响应共 265 个调用，目标结构异常为 0；至少 49 条实际命中同批收尾 [S1 §9.4] |
| 8/24，`d659f33` | 历史长回答在 final 默认 2,048 token 被截断，外部额度尚未耗尽 | 删除独立 final cap，按外部 completion 剩余额度分配 final | 当前源码已移除 cap；`run_11_fixed_v5` 的 240 条额度字段闭合，但未覆盖旧长回答样例，不能宣称该专项服务器验收完成 [S7] |
| 8/29～31，Agent run-03/04 与 `run_server_01` | run-03 新见 5 个缺 `}`；10 条上下文、300 条 DTB 回放又出现 133 条非法 JSON | 重新开放可靠性门；拆分“原始 XML 被 stop 截断”和“完整 XML 的 parser 分片缺陷” | 新证据证明不能沿用 8 月阶段性通过结论覆盖扩大后的场景 [S1 §9.1][S2] |
| 9/1，`run_server_02` | final 中引用 `</think>` 被错误当作思考结束；通用 burst 解析也需补齐 | final 恢复原请求 sampling stop；工具 parser 在函数完整后生成 canonical JSON，连续处理多个调用 | 非法 JSON 133/300 降至 0/300；144 个 final 含 442 次字面 marker，错误 stop 命中为 0。仍有 DTB 1 次缺 terminal、Vanilla 2 次空参数，保留未关闭项 [S3] |
| 9/1 定责，9/3 实施，`run_server_03` / `cc9bfbe` | 定向 60 条 DTB 中 2 条发生 SSE orchestration error；普通 token 拼出 `</think>` 与字符串停止碰撞 | thinking 原子迁移为结构 token stop，工具 parser 同步接受空文本 tool-start ID | 后续 token-stop 回放 60 条 DTB 无旧编排异常，但新审计发现 47 条公开 reasoning 丢失正文尾段 [S4][S5] |
| 9/4，token-stop 首轮与 `replay2` | terminal end ID 与正常正文同 chunk，原生 reasoning parser 丢掉整个正文片段 | 独立 reasoning patch 保留末尾 end ID 之前的正文，维持 include-stop 默认 False | reasoning 丢字由旧轮 47/60 降至新轮 0/60；新轮 48 个混合 chunk 的 344 字符全部保留；两侧共 120 条工具协议检查通过 [S5][S6] |

第一阶段的主样例经历“98 条被检出且另有 2 条漏报、22 条异常、12 条异常”的逐层收敛，随后在扩大样例与原生 parser 修复后关闭当时的目标故障。第二阶段又通过真实 Agent 任务暴露更深边界问题，最终完成最新定向验收。这样的记录保留了工程过程中的反复，也说明每次“通过”对应的范围。[S1–S6]

## 3. 难题一：工具调用已经开始，控制器为什么还在“思考”

### 现象

客户端先收到泄漏到 `content` 的 `function=bash>... </tool_call>`，随后出现 `\nuser\n<t` 一类冗余 role 尾流；有时已经收到 `tool_calls` delta，后面又继续输出正文。同一外部响应中还可能出现两个来自不同内部生成的工具调用。[S1 §2.1]

### 根因

旧状态机只对 `</think>` 做思考结束处理。Qwen 原生协议还允许 `<tool_call>` 隐式结束思考并开始工具调用。忽略这个边界后，控制器把已经进入工具阶段的生成继续当作 thinking，清除内部终止信号，再进入 confidence probe、迭代思考和 fresh final answer。

因此，多出的 role 和 tool 后 content 是状态机越过逻辑回合边界的结果，不能靠删除某段输出文本解决。多个合法工具调用与多个独立 completion 拼接，也必须区分。

### 解决与证据

引入 `ReasoningBoundary.THINK_END` / `TOOL_CALL`，模型命中边界后直接续写同一个逻辑 completion；对重复 terminal、terminal 后输出等行为显式报错。随后进一步删除“非空终止正文 + 人工空 terminal”的拆分，保留最终源输出的原对象及终止字段。

**实测：** 第一轮终止修复后，100 条 DTB 中 role 尾流、tool 后 content、terminal 后 chunk 均为 0。8 月后续 80 条带遥测 smoke 的单 completion、单 final source terminal、原样透传与无 synthetic terminal 全部通过。**边界：** 早期这些 run 没有实际命中直接 `<tool_call>` 结束 thinking 的分支；这一点直到最新 `replay2` 才补到一次真实 token-only 覆盖。[S1 §8][S6 §4]

## 4. 难题二：投机解码下，文本和 token IDs 对“已经输出什么”意见不一致

### 现象与根因

13 条 XML 泄漏失败中，final child 都从 `function=bash>` 开始，固定缺少 `<tool_call>\n<`。vLLM string stop 在合批输出中可以截断解码文本，却保留同批停止点之后的 token IDs。DTB 把这些后缀 IDs 追加到 continuation prompt，于是模型认为工具前缀已经生成；公开文本流中却从未出现这一前缀。[S1 §2.2]

下面是逻辑示意，不是重新生成的实验记录：

```text
本批 token 序列：思考正文  </think>  <tool_call>  \n  <
本批可见 text： 思考正文  </think>
旧续写 prompt：包含未公开的 <tool_call>\n<
下一 child：    从 function=bash> 开始
解析结果：      工具前缀缺失，XML 被当作普通 content
```

### 两步解决

历史修复在控制器内对齐 stop boundary，只允许“原 IDs = 转发 IDs + 被裁后缀 IDs”，避免把未公开后缀送入下一阶段。**实测：** 100 次全部命中对齐，XML 泄漏降为 0。[S1 §2.1]

9 月的流式重构进一步把 thinking 的两个结构边界改为 `stop_token_ids`。在目标协议绑定校验后，使用 `248069` 表示 think end、`248058` 表示 tool start；这些是当前 Qwen3.6 部署配置，不能当作跨模型常量。vLLM scheduler 在 accepted batch 内逐 token 检查停止点，从引擎侧避免同批后缀进入输出。普通 token 拼出的同名字面量继续作为正文处理。[S5][S10]

这使 DTB 能兼容投机解码的批量输出，而无需依赖逐 token 分片或关闭 MTP。最新回放沿用既有环境，但未重新记录完整 MTP 启动身份，不能把该轮写成一份新的 MTP 性能实验。[S1 §3.5.2][S6 §2]

## 5. 难题三：XML 完整，为什么 JSON 仍然少一个右括号

### 把控制器与原生 parser 的责任分开

在前缀问题消失后，100 次中仍有 12 次 arguments 缺最后一个 `}`。失败样本的源 XML 包含完整 `</parameter></function></tool_call>`。同一份 955 字符的模型输出，仅改变最后参数和闭合标签的分批方式，公共 arguments 就分别为 460 和 461 字符，差的正是 parser 负责生成的 `}`。[S1 §2.1]

排查先纠正 DTB 自己的终止绑定：原生输入的最后一个非空 terminal，不能拆成“未终止正文”和“人工空 terminal”。随后将相同 text/token IDs 送入未修改的原生 parser 做离线反事实，确认恢复 DTB 契约后，parser 仍然对合法分片敏感。

### 原生 parser 的 early return 漏过同批收尾

旧 parser 处理完参数片段便返回，没有继续消费当前批次已经存在的 `</function>`。如果下一批没有新的非空文本，闭合 JSON 的分支就永远没有执行机会。人工空 delta 也无效，因为空文本路径提前返回。

8 月的最小 patch 将 parameter-close 同批处理补齐。**实测：** 170 条含工具的 DTB 响应共 265 个调用没有目标结构异常，至少 49 条确实命中同批收尾。其中多工具样例的 19 条响应各生成 6 个连续 `edit` 调用，验证了一个 completion 中的多工具连续解析。[S1 §9.4]

这批 180 条 DTB 中另外 10 条没有结构化工具调用：8 条有 raw 证据支持模型未生成工具，2 条因缺 raw 保留未分类，不能写成“180/180 全部无问题”。另有合法 JSON 的 schema 偏离，也不属于缺 `}` 的同一指标。[S1 §8]

### 扩展到完整函数与连续多工具的分片处理

8 月 29 日 Agent 新样本再次出现缺 `}`，必须重新审查工具开始、函数头、参数和调用切换处的提前返回。当前版本在函数 header 完整时先发送工具名，待真实 `</function>` 出现后，复用完整 XML 函数解析生成 canonical JSON arguments，一次 callback 可以返回多个连续 index 的工具增量。[S2][S9]

这个方案保留 SSE 和工具名早发，**将参数发送推迟到函数完整闭合**，以获得分片稳定性；它没有为不完整 XML 猜测补尾。新增 burst 形状由确定性 fixture 和分区测试覆盖，不能把 `run_server_02` 的零非法 JSON 当作所有新增 burst 分支都被服务器命中的证明，该轮目标 burst 命中数为 0。[S3 §1]

## 6. 难题四：参数里的 `</think>` 为什么让整个工具调用中途结束

同样是 JSON 不完整，这一次模型原始 XML 就没有闭合。`run_server_01` 的 133 条 DTB 非法 JSON 中，132 条 raw XML 不完整；其中 131 条直接记录 final child 的 `source_stop_reason=</think>`，另 1 条 stop reason 缺失。参数正在描述处理思考标签的源码或报告，恰好包含字面 `</think>`。[S2]

旧 `configure_final_answer_stop()` 仍向最终答案追加思考阶段 stop。最终答案里的合法工具参数被提前截断，后面的 XML 根本没有生成，parser 无法补回。

修复让 final 从原请求重新构建 sampling params，不再追加 DTB 的 reasoning stop；用户自己的 stop 继续保留。**实测：** 下一轮 DTB 非法 JSON 从 133/300（44.3%）降至 0/300；144 个 final raw 中出现 442 次字面 marker，错误 final stop 命中为 0。这是直接覆盖触发条件的证据。[S3 §1]

随后 thinking 中又发现另一个字符串碰撞：普通 IDs `510,26003,...` 解码为 `</think>`，旧对齐器却要求一定能找到独立 special token `248069`，从而可能抛出异常。`run_server_03` 有 2/60 条 DTB 返回 `InternalServerError: DTB orchestration failed; see server logs`。**实测**是 SSE error；具体对齐 RuntimeError 的解释由源码与 token 轨迹高度支持，因为缺原始 traceback，按**假设**记录，未冒充日志直接证明。[S4 §5]

9 月 3 日以结构 token 为权威的原子重构同时修改停止配置、边界分类和 tool parser：空文本 `[248058]` 也必须建立工具状态，下一 child 从 `<function=...>` 继续时不能再次 reset 或泄漏 XML。后续回放未复现旧编排异常，最新还实际命中一次这种 token-only 工具边界。[S5][S6 §4]

## 7. 难题五：工具调用全通过了，思考正文却悄悄少了一截

9 月 4 日 token-stop 首轮的 120 条工具流协议检查全部通过，60 条 DTB 的 raw/forwarded 也完全一致。但将客户端公开 `reasoning` 与 parser 前正文逐字符比对后，发现 **47/60 条丢字，共 268 字符、117 个非边界 token ID**。[S5]

故障条件是正常正文和末尾 `248069` 同 chunk：detokenizer 删除了 `</think>` 的文本，保留边界 ID；Qwen reasoning parser 看到“有 end ID、无 marker text”，直接 `return None`，连同批正常正文也丢弃了。控制器内的 raw/forwarded 校验无法覆盖 parser 后的损失。

新增独立 `qwen3_reasoning_parser.patch`：仅当 end ID 位于本批末尾时，将非空 `delta_text` 原样作为 reasoning 返回；不改变 include-stop 默认 False，不猜测 ID 位于中间时的文本切分。[S9]

最新回放中的真实样例：

```text
delta_text      = " different approach.\n"
delta_token_ids = [2086, 5313, 13, 198, 248069]
stop_reason     = 248069

旧 parser：公开 reasoning 只到 "Let me try a"
修复后：   "Let me try a different approach.\n"
```

**实测：** `replay2` 的 60 条 DTB 公开 reasoning 共 8,907 字符，全部与 parser 前正文一致；48 次混合 chunk 的 344 字符、124 个非边界 IDs 对应正文全部保留。旧 parser 重放本轮相同输入，恰好丢失这 48 个尾段；修复版完整链与公共输出检查 84/84 通过。[S6 §3]

两次是重新采样，旧轮丢失 268 字符、新轮保留 344 字符，不代表逐请求找回原来的 268 字符。反事实验证使用同一份新轮输入，才提供直接的 parser 因果证据。

## 8. 贯穿全过程的另一项成果：把“失败”变成可定位、可复算的证据

排障能力本身也经历了升级：

1. **从真实多轮上下文抽取首个失败点。** 保存 request body、工具定义和消息历史，避免用已经污染的后续历史混淆原因；同一上下文交替回放 Vanilla/DTB，使用唯一请求 ID 关联日志。[S8]
2. **同时保留 parser 前与 HTTP 后证据。** `stream_audit` 记录 child phase、sequence、raw/forwarded text、token IDs 和 terminal；客户端保存原始 SSE，检查 reasoning、content、工具参数与结束信号。[S1][S6]
3. **把错误分层。** XML 完整但 JSON 损坏归 parser；XML 已被 stop 截断归生成阶段配置；合法 JSON 缺必填字段归语义层；模型没有选择工具不能自动记为 parser 失败。[S2–S6]
4. **补足检查器盲区。** 早期“出现任意工具即 pass”漏掉跨 child 重复生成；后期 HTTP 200 掩盖了合法 SSE JSON 内的顶层 error；再后期 raw/forwarded 相同仍漏掉 parser 后 reasoning 丢字。验收终点最终延伸到公开响应。[S1][S4][S5]
5. **保留部署身份与分支覆盖。** 固定 vLLM SHA、parser import path、实际源码和 patch hash；把“补丁文件存在”“服务加载正确文件”“真实命中故障分支”分别核对。历史缺身份或 raw 的样本继续标注未定责。[S3][S9]

本次汇总重新运行了 `replay2/audit_evidence.py`，从已有归档复算 120 个请求 body hash、SSE/summary、60 条 DTB 的 15,379 条 audit 归属与连续性，结果与已有报告一致。这是本地归档复算，不是重新运行模型或 NPU。

## 9. 当前实现边界与最终验收结论

| 层次 | 当前工程成果 | 证据边界 |
| --- | --- | --- |
| DTB 状态机与流式交接 | 双 reasoning boundary、同一 completion continuation、最终源 terminal 原样透传、错误显式传播 | 历史多轮 replay 与最新定向回放支持；仍需更广状态覆盖 |
| thinking 停止 | 结构 token stop，配置与 tokenizer/parser 绑定校验，避免普通字面 marker 碰撞 | 最新 60 次整数停止，其中 59 次 think end、1 次 tool start |
| 工具 parser | token-only tool start、完整函数生成 canonical JSON、连续多调用处理 | 最新 120 条无工具流协议异常；参数增量改为函数闭合后发送 |
| reasoning parser | 保留 stripped terminal ID 同批的正常正文 | 最新 60/60 保真，48 次旧触发条件全部通过 |
| completion 额度 | 已删除额外 2,048 final cap，以外部剩余额度与剩余上下文约束 final | 当前源码确定；历史长回答和真实 length 结束语义未由本轮关闭 |
| 观测与部署 | 生产默认 off；白名单 stream audit；三份版本化 patch、复算与回归工具 | 新轮仍需补部署 reasoning hash 等身份信息 |

当前配套交付是 DTB 实现以及依次应用的 `dtb_serving.patch`、`qwen3_coder_parser.patch`、`qwen3_reasoning_parser.patch`，固定面向 vLLM `v0.23.0`。不能只部署其中一个组件，却把整条协议链的兼容性视为自然成立。[S9]

最终可以用于汇报的结论是：**经过分层定位、状态机与原生解析器协同修复，以及真实多轮上下文反复回放，DTB 在已覆盖的 Agent 流式工具调用场景中实现了思考、工具调用和投机合批边界的兼容，最新回放未再观察到工具传输协议异常或思考正文丢失。**

仍须明确以下范围：

- 最新回放只有两条固定历史上下文、每变体各 30 次，共 60 对，不是重新执行完整 Agent 多轮任务。60 条 DTB 均为短思考，未触发 probe、迭代或强制截思考；非流式、长推理及取消等路径没有同批服务器全矩阵证据。
- 最新 Vanilla/DTB 分别还有 30/22 条语义偏离，其中必填参数缺失分别为 1/2 条。DTB 两条缺参已有 raw XML 证明模型本来就没生成字段，不能要求 parser 补造。协议异常为 0 不等于任务错误为 0。
- `run_server_02` 的两条 Vanilla 空参数缺 parser 前 raw，最新定向未复现也不能追溯性证明其根因；server-wide parser 全面无回归结论仍需要更广、身份完整的验证。
- 历史 Agent run-05 未达到“质量不退化且 E2E 降低”的目标。本次是可靠性攻关总结，不报告性能胜利；下一步应冻结版本，开展 9 场景端到端质量回归，并以同身份、telemetry-off 的 Vanilla/DTB 数据判断时延。[S11]

## 10. 证据索引

历史排障文档含多个时间点的“当前/待修复”描述，本文按日期解释它们，并以最新专项报告与源码说明现状。

| 编号 | 仓库证据 | 用途 |
| --- | --- | --- |
| S1 | [工具流排障与完整复盘](../debug/tool_call_streaming_debug_plan.md)，重点 §2.1、§8、§9.1、§9.4、§10 | 8/13～19 的 100 次主样例、180 对扩展回放、MTP 分片、后续重新开放验收；早期原始日志在 `debug_tool_call` 归档分支，服务器结果提交 `0b641fd`，复盘 HEAD `7789a43` |
| S2 | [run_server_01](../log/perf/agent_tool_call_run04_paired_replay/run_server_01/RESULT.md) | 133/300 非法 JSON 的 raw-incomplete / parser-complete 根因拆分 |
| S3 | [run_server_02](../log/perf/agent_tool_call_run04_paired_replay/run_server_02/RESULT.md) | 0/300 非法 JSON、442 次 literal marker、剩余 3 个协议异常及身份/分支覆盖限制 |
| S4 | [run_server_03](../log/perf/agent_tool_call_run04_paired_replay/run_server_03/RESULT.md) | 2/60 DTB 编排错误、split marker、SSE error 审计盲区 |
| S5 | [token-stop 首轮](../log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/RESULT.md) | token-stop 生效，47/60 reasoning 丢字，覆盖缺口 |
| S6 | [replay2 最终验收](../log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/replay2/RESULT.md)、[AUDIT.json](../log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/replay2/AUDIT.json)、[复算脚本](../log/perf/agent_tool_call_run04_paired_replay/run_server_04_token_stop/replay2/audit_evidence.py) | 120 条工具流通过、60 条 reasoning 保真、48 次触发/344 字符、语义分层 |
| S7 | [run_11_fixed_v5](../log/perf/run_11_fixed_v5/RESULT.md)、[当前 final 实现](../../dtb/reasoning/generation_states.py) | 外部统一 completion 额度、旧长回答用例尚未复验 |
| S8 | [Agent 多轮实验设计](../design/agent_multiturn_dtb_experiment_plan.md)、[run-02 首次污染请求抽取](../log/perf/agent_tool_call_run02_replay/RESULT.md)、[run-04 故障抽取](../log/perf/agent_tool_call_run04_paired_replay/RESULT.md) | 真实多轮任务与 recorded-prompt paired replay 的区别 |
| S9 | [部署说明](../../patches/vllm/v0.23.0/README.md)、[工具 parser patch](../../patches/vllm/v0.23.0/qwen3_coder_parser.patch)、[reasoning patch](../../patches/vllm/v0.23.0/qwen3_reasoning_parser.patch) | 当前三补丁交付、canonical JSON 与 reasoning tail 修复 |
| S10 | [token-stop 设计](../design/dtb_reasoning_boundary_token_stop.md)、[当前边界实现](../../dtb/reasoning/parser_boundary.py) | 结构 token 权威停止、空文本边界、请求 stop 保留、协议绑定 |
| S11 | [Agent run-05](../log/perf/agent_run05/RESULT.md)、[性能分析变更记录](../debug/deer_performance_analysis.md) | 区分框架可靠性、Agent 任务质量和时延；定位最新验证状态 |

