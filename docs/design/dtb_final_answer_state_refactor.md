# FinalAnswerState 当前职责

`dtb/reasoning/generation_states.py::_FinalAnswerState` 已进行等价方法提取，未改变状态机。
设计起点是原 handle 中交织的边界、额度、引擎提交、流消费和成功收尾；对应回归为
`tests/test_dtb_final_answer.py` 与 `tests/test_dtb_state_machine.py`。

| 方法 | 职责 |
| --- | --- |
| `_prepare_boundary` | 按既有顺序校验原因、尝试发边界、追加相同 cue/闭合标记 |
| `_resolve_limits` | prompt 更新后计算 context 与外部 completion 剩余额度，预留未发边界 |
| `_consume_stream` | 转发普通 delta、首块补发延迟边界；正常 terminal 等 EOF；保留异常尾流检查 |
| `handle` | 构造真实 sampling/child/engine kwargs，提交引擎，成功时发 terminal、更新账本、记录 phase |

`_FinalLimits` 是不可变的提交前额度快照，`DtbRequestState` 仍是唯一账本。
prompt CLOSED 与 parser 已收到 boundary 是两种状态：无历史 chunk 时先闭合 prompt，
首个 final 输出才可作模板补发边界，额度必须预留，不能假设进入 final 就已经发过边界。

CONFIDENCE_ACCEPTED/BUDGET_EXHAUSTED 保留 guided answer-cue；自然 think-end/tool 边界
不追加 cue、不重复结构 token。试答不复用为最终答案。
错误 terminal、异常和取消不包装成功；正常 terminal 在生成器耗尽后发送。
phase 时段仍从准备前到 terminal 发送后，controller CPU 截止于 generate 前。
实现与证据入口见 [静态优化](dtb_static_cpu_refactor.md)。
