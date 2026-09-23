# Qwen reasoning / tool 边界契约

目标是原生 parser-facing 等价。流式必须同时保持 text 与 token_ids 的逻辑连续性；非流式保留完整响应 parser。
绑定实际 tokenizer、reasoning parser、tool parser 与 delegating parser，不用手写 token 值猜测另一模型。
内部 thinking 结束、tool 起始、合成闭合、空文本 delta、speculative batch 与 final child 边界均不能丢/重复结构标记。

生产控制器以 token 权威 prompt 续写，parser 负责对外协议。
终止 stop 的 marker 文本可能被 detokenizer 隐去，普通 reasoning 前缀仍必须交给 parser。
版本化 qwen3_reasoning_parser.patch 修复末尾 reasoning-end ID 且 marker 已剥离时的普通前缀丢失。
qwen3_coder_parser.patch 处理累积完整 function 参数及 token-only tool start；不凭空补齐未完成 JSON/XML。
变更范围、应用及负对照命令见 [patch 说明](../../patches/vllm/v0.23.0/README.md)。

本地测试在固定上游源码与依赖边界 stub 上执行：原生新增回归、未改路径、detokenizer 基类与 unified parser，
以及 tests/fixtures/parser_tail 的 60 条冻结链。只保留检查必要的真实文本/token IDs、工具 schema 和历史响应。
压缩是无损 gzip；投影过程与源文件 hash 见 [provenance](../../tests/fixtures/provenance.json)。
历史公开 SSE 中的 47 条尾部缺失仍作为负对照，不能因为重新解析通过就说历史服务器输出已被修复。

```bash
python tools/dtb/reasoning_tail_fidelity.py --apply-repo-patch --replay-dir tests/fixtures/parser_tail
```

目标 HTTP/模型/NPU 的验证仍为 pending_remote。服务器先跑[交付 replay](../../validation/replay/README.md)，
需要边界排障时只对新 request ID 白名单启用 stream_audit，比较 pre-parser 前缀与公开 reasoning/tool 输出。
保持生产 stream_interval 与 stop 配置，不用临时 include_stop_str_in_output 控制代替已交付补丁。
