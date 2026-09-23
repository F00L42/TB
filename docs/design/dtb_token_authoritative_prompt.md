# Token 权威续写与所有权

当前 `DtbRequestContext` 使用 token IDs 作为模型续写的权威输入；不维护随每块输出增长的全文镜像。
分类优先复用原始 messages/user 或 serving 提供的可选文本，不为了分类无条件 decode 全部 prompt。
system_prefix 直接使用原始身份，不走 legacy_text 全文分类。

原生 observation 阶段允许借用原始 prompt；首次修改前 own_prompt()，每个 child 通过
engine_input()/child_prompt() 获得稳定 token 快照。不要把它们改成共享可变列表，尤其要保护并发 child/原生输入。
真实模型输入、parser-facing delta、响应 usage 是不同边界，不可用 decode 后的字符串重新 tokenize 替换已有 IDs。
边界保留规则见 [Qwen 协议](dtb_reasoning_boundary_token_stop.md)。

源码：`integration/vllm/request_adapter.py`、`reasoning/request_context.py`、`reasoning/deferred_observation.py`。
回归：test_dtb_token_prompt、test_dtb_prompt_copy、test_dtb_intervention。
这些本地测试不能证明真实 tokenizer/引擎/取消行为；服务器应保留版本/导入路径/源码 hash 后再验证。
