# FinalAnswerState 等价重构建议

> 状态更新（2026-09-22）：`codex/dtb-static-refactor` 已按本设计完成局部方法提取，
> 基于 f342e127 原实现先运行 41 项新增行为测试，再验证重构结果。
> 当前方法为 `_prepare_boundary`、`_resolve_limits`、`_consume_stream`；phase 写出保留在
> `handle`，真实 generate 参数也在主流程可见。下文为原始设计及历史验证记录，示意接口名
> 不作为当前 API。完整交付证据见[静态优化记录](dtb_static_cpu_refactor.md)。

## 1. 设计目的与范围

设计基线：2026-09-10，仓库 HEAD `326aff3ab0a9803a9324b1eac274a695ffebc959`。评审对象为 `dtb/reasoning/generation_states.py::_FinalAnswerState.handle`，当时约 180 行。设计起草时尚未修改实现；当前状态见页首。

目标是使维护者能够直接看懂 final 的输入构造、额度和结束语义，降低后续修改 cue/parser 时误改协议的风险；不是减少模型调用，也不以本轮重构修复 close 实验故障。

保持既有设计：

- confidence accepted / budget exhausted 仍使用当前 guided answer-cue，仍 fresh-final，不复用 trial；trial-close 实验是独立变量。
- 自然 think-end / tool-call boundary 不添加 cue，不重复结构标记。
- P/H 保持 token-ID 续写；流式按绑定 token 协议闭合，非流式保留 legacy 方式。
- 共用一次 completion 额度、原生上下文上限和 sampling/engine kwargs；不增加 max_tokens 硬帽。
- 顺序 await、queue 背压、流增量和正常/error terminal 行为不变；异常与取消继续由既有生命周期处理。
- phase/stream-audit schema、字段含义、快照时刻及 off 不创建 recorder/writer 的约束不变。

## 2. 检视结论：职责混杂，不是分支都多余

| 当前位置（generation_states.py） | 职责 | 建议 |
| --- | --- | --- |
| 682–706 | 校验 final 原因、补发边界、闭合主 prompt、区分 stream/non-stream | 抽出一次性的 final 边界准备 |
| 708–732 | 上下文/外部 completion/待发边界三者的额度关系 | 抽出只读额度计算，冻结 telemetry 快照 |
| 734–756 | sampling、child ID、engine prompt/kwargs、提交 | 保留在编排层，便于审计真实 generate 参数 |
| 757–818 | 增量转发、补发延迟边界、计数、正常/error terminal、异常尾流 | 抽出 final 专用流消费；不做跨所有状态的通用生成器框架 |
| 820–852 | 最终 terminal 透传、成功记账和 phase 记录 | 保留明确成功收尾，phase 参数列表可单独提取 |

当前函数同时处理“执行为什么结束”“模型输入是否闭合”“parser 是否已看到边界”“流有没有合法 terminal”四种不同问题。嵌套分支看起来相似，但不能互相替代。

### 2.1 特别不能合并的两个边界状态

`request_state.reasoning_state` 表示主续写 prompt 已经闭合；`context.reasoning_boundary_emitted` 表示对外已发送边界。

在没有任何历史 chunk 的 budget/safety 提前结束路径，`emit_reasoning_boundary_once()` 因 `last_chunk is None` 返回 False；随后 `close_reasoning()` 仍完成 prompt 闭合。于是存在合法状态：

```text
prompt CLOSED，外部 boundary 尚未 emitted
```

这时必须预留 `pending_boundary_tokens`，等 final 第一个输出可作为 RequestOutput 模板后补发边界。当前循环中的检查不是重复工作；禁止简化成“进入 final 前必然已发出边界”或直接把 pending 固定为 0。

证据：`parser_boundary.py::emit_reasoning_boundary_once`；`tests/test_dtb_state_machine.py::test_final_reserves_boundary_when_no_visible_chunk_exists`。该场景要求 final child 仅有一条“正文+terminal”输出时，也先发边界，且只计数一次。

### 2.2 final 原因与既有边界策略

| 原因 | 正常入口状态 | 对主 prompt 的操作 |
| --- | --- | --- |
| CONFIDENCE_ACCEPTED / BUDGET_EXHAUSTED | OPEN | cue + think-end + separator |
| MODEL_THINK_END | CLOSED，边界已透传 | 不修改 |
| MODEL_TOOL_CALL | CLOSED，tool-call 起始已透传 | 不修改，续写工具结构 |

保留当前非法状态检查及其先后顺序。表格是正常路径说明，不授权新增枚举穷尽断言或改变当前对异常组合的接受/拒绝行为。特别是 MODEL_TOOL_CALL 不能被统一关闭逻辑额外插入 `</think>`。

### 2.3 terminal 处理不是常规“读到 stop 就 break”

- 普通 delta：立即透传，不聚合正文。
- 正常 terminal：保留同一个 result，继续消费到 generator 耗尽，检查是否出现第二个 terminal 或 terminal 后输出；之后才原样发送 terminal。
- error terminal：原样发送错误并结束此状态，不作为正常 final 成功记账。
- 无任何输出 / 有输出但无 terminal：保留两种不同错误。
- Python 异常 / CancelledError：继续向外传播，由 `DtbStateMachine.run` 和 managed lifecycle 处理。

因此不能把正常 terminal 分支也改为立即 break/return，不能在 finally 中无条件发成功 terminal，也不能把正文和同 chunk 的 terminal 拆成两份。流式和非流式的 DELTA/CUMULATIVE 计数方式也需保留；不能顺手把非 DELTA 输出重新增量化。

## 3. 推荐分解：仍是一个状态，四个小职责

首先在 `generation_states.py` 内做私有方法提取，不拆成更多状态，不新增公开接口、配置或动态策略注册。已有 `request_context`/`parser_boundary` 能做的闭合、发边界、发终态继续复用，不另建实现。

建议的内部接口（示意，不是当前已存在函数）：

```python
async def _prepare_final_boundary(context, reason) -> None:
    """保持现有顺序，尝试对外发边界，再闭合主 prompt。
    允许本次准备造成 prompt 已闭合但外部边界尚待发送。
    """

def _resolve_final_limits(context) -> _FinalLimits:
    """在追加 cue/边界之后，计算 final max_tokens，保存提交前快照。
    不提交请求、不改变计数。
    """

async def _consume_final_stream(context, generator, *, output_kind, child_id):
    """即时透传非终态 delta，必要时补发延迟边界。
    正常返回 (terminal_result, generated_tokens)；已转发 error 返回 None；
    无输出、无 terminal、非法尾流抛错，取消不吞掉。
    不发送正常 terminal，不在这里写成功账本。
    """

def _record_final_phase(context, *, limits, output, generated_tokens,
                        child_id, prompt_tokens, reason,
                        phase_started, controller_cpu_ms) -> None:
    """仅迁移现有 final phase 字段；由调用方在 recorder 非空时调用。
    不重新读取已变化的额度来替代提交前快照。
    """
```

`_FinalLimits` 仅是局部、不可变的内部计算结果，保留 `emitted_before_final / pending_boundary_tokens / external_remaining / answer_limit`。不写入 request state，不建立第二份计数账本；它存在的理由是提交前快照在后续发送中会过期，不能到 telemetry 记录时重算。它不是新的预算策略。

主函数变为约 50–70 行的编排：校验 reason → 准备边界 → 计算额度 → 按原样构造/提交 generate → 消费流 → error 返回或正常终态提交 → 更新账本 → 条件记录 phase。构造 sampling/kwargs 仍在主流程可见，不将真实引擎接口藏进通用 runner。

## 4. 可以简化与不应顺带修改的地方

### 可做的局部简化

1. **用消费结果替代 `propagated_error` 跨段标志。**`_consume_final_stream` 转发 error 后返回 None，handle 设置 error 并返回；正常结果始终包含 terminal 与 token 数。不把 None 也用于无输出，无输出仍抛原错误。
2. **正常成功时使用 terminal_result 作为 phase output。**原代码保证 terminal 后不能有任何输出，所以成功时 `last_output is terminal_output`。辅助函数内仍需区分“完全无输出”与“有输出但无终态”，不能无条件删掉相应状态。
3. **统一主函数的阅读方向。**边界准备先处理 CLOSED 的校验并返回，再处理 OPEN；stream/non-stream 差异封装在该准备函数，不与额度计算交叉。
4. **把额度表达式和 telemetry 字段列表移出主流程。**减少视觉噪声而不改变公式；不为凑行数给每个简单赋值新增 helper。

### 暂不做的“优化”

- 不删除 `pending_boundary_tokens` 或循环内首次输出补发逻辑。
- 不改变 cue 文本、追加顺序、trial/final prompt 差异，不移除 answer-cue，不做 trial reuse。
- 不新增 generator `aclose()`、重试、abort 或正常 terminal 提前返回；这些会改变异常/资源生命周期，应另案验证。
- 不把条件 telemetry 改为生产路径也调用的通用 recorder，不新增每 chunk 的 snapshot/dataclass、encode/decode 或文本拷贝。
- 不为了减少 1 个 if 将 error 分支与正常 terminal 合并；两者发送时机和成功账本不同。
- 不在本轮调整“error terminal 已产生的模型 token 是否记入成功 final phase”的既有口径。若要改善失败账本，单独提出行为修改。

## 5. 等价性验证计划

### 5.1 已验证的现状基线

2026-09-10 本地运行 `test_dtb_state_machine.py`、`test_dtb_token_prompt.py`、`test_dtb_intervention.py`、`test_dtb_telemetry.py`，**141 passed**；pytest cache 写入权限警告不影响用例执行。源码尚未重构，所以这不是候选实现验收。

已有覆盖：accepted fresh-final、第二轮 accepted、预算结束、自然 think-end/tool-call、统一外部额度、追加 cue 后的上下文额度、无历史 chunk 延迟边界、正文/terminal 同对象、stream/non-stream golden prompt IDs，以及 managed lifecycle 异常/取消。

### 5.2 实施前补足 characterization tests

重点是 final 专用流消费异常路径，而非只增加 happy-path 测试：

- 0 个 result、多个普通 result 后无 terminal；分别断言原异常信息与 completion_status。
- 单个含正文的 terminal、空正文 terminal、多个 delta 后 terminal；断言对象身份、text/token IDs/finish/stop、顺序和单终态。
- 正常 terminal 后第二个 terminal、正常 terminal 后普通 delta；断言报错且不能先将前一个正常 terminal 作为成功发出。
- error terminal（含正文/不含正文），以及此前已发普通 delta；断言错误原样透传、不补成功 terminal、不写成功 final 账本。
- final generator 自身抛错、消费者 queue.put 抛错、消费中取消、等待 generator EOF 时取消；既有异常传播和终态行为保持不变。
- final 的 DELTA/CUMULATIVE/FINAL_ONLY 计数；不能拿仅覆盖 trial/collection 的输出模式测试代替。
- 无历史 chunk、首个 final 输出同时带正文与 terminal/error；边界预留/透传顺序和外部额度保持一致。

对原版和重构版跑同一组 deterministic engine-double 输入，比较：

1. generate 次数、child ID 顺序、完整 prompt IDs、sampling/stop、priority/LoRA/trace/DP/reasoning kwargs；
2. 所有 parser-facing result 的 text、IDs、finish、stop 和顺序，必要处比较对象身份；
3. request state 账本、边界/终态计数、异常类型/消息；
4. phase/stream-audit 的非计时字段及事件顺序。时间数值不要求相等，但计时边界须保留：phase 从准备前到终态发送后，controller_cpu 不得改为包含完整 generate wall。

### 5.3 性能与服务器边界

这是可维护性重构，不预期直接降低 NPU prefill/decode 或解决 run_server_01 两条坏输出。每请求增加少量私有函数调用与一个额度快照对象；不增加 child/queue，不应在每 chunk 增加额外调度任务或正文复制。实际开销需 off 同环境测量，不能以本地无模型单测宣称性能相同。

先保持 close 原始流取证与重构修订分开：原始问题在旧修订取证，重构用固定流/测试双验证等价。若服务器需要补回归，只验证等价性，不同时搜索 gate/trial 参数，以免无法归因。

## 6. 实施顺序与一致性校验

建议两步：**先补 final 异常分支刻画测试，再机械提取职责并做局部状态简化。**第一步不改行为；第二步不引入新算法决策。是否最终把 final helper 移到单独文件，等函数边界稳定后再判断，不作为本轮必要目标。

校验结果：外部接口/状态机边数不变；边界底层能力复用；prompt-closed 与 boundary-emitted 不混用；额度仍由 request state 唯一计数；error 与普通 terminal 不混用；指标口径不借重构改变。现有 close 实验故障根因仍待取证，不能在重构完成时顺带标记修复。

## 7. 变更历史

| 日期 | 变更内容 | 原因 |
| --- | --- | --- |
| 2026-09-22 | 在 codex/dtb-static-refactor 完成三个私有方法提取，phase 写出留在 handle；41 项新增行为测试先在原实现通过，完整本地门禁通过 | 保持原算法与请求生命周期；详见静态优化交付记录 |
| 2026-09-10 | 基于 326aff3 提出 final handle 等价重构，识别延迟边界和 terminal 约束，确认 141 项现状测试通过 | 降低职责交叉和修改风险，不改变 cue/停止/额度/流协议 |
