# 验证与分析工具

默认从仓库根目录运行，输出放 artifacts/（Git 忽略）。当前可执行验收入口见 [RESULT](../RESULT.md)。

| 工具 | 输入 / 用途 |
| --- | --- |
| dtb/check_static.py | 固定子模块、patch、源码/文档与无模型测试；--benchmark 增加旧/新局部 CPU 比较 |
| dtb/validate_release.py | 7 条合成请求，自动/固定/probe，stream/JSON，并发/配对耗时与精确答案门 |
| dtb/audit_release_phase.py | 新 replay 目录 + phase 服务日志，路由/账本与真实 probe 覆盖 |
| dtb/replay_tool_calls.py | 外部 JSONL 工具用例的细粒度 replay；CLI 默认固定参数不是自动路由！ |
| dtb/analyze_tool_call_run.py | replay summary 与 DTB_STREAM_AUDIT 对账 |
| dtb/extract_tool_call_replay_cases.py | 读取旧 full-log 格式的离线导入器，不依赖旧代理或 LightCode |
| dtb/reasoning_tail_fidelity.py / parser_partition_equivalence.py | 固定上游 parser 无模型门禁 |
| dtb/g0_experiment.py、g0_runtime.py、g0_support.py | 实际 tokenizer/decoder 介入验证，Python mock 只验证编排 |
| dtb/check_probe.py | 单个 probe replay summary 与 phase 日志覆盖检查；不接受含 native-auto 的混合集 |
| dtb/audit_system_routes.py / benchmark_system_routes.py | 外部采集的离线路由审计 / 合成路由 CPU 基准 |
| extract_dtb_phase_metrics.py | 含 _SUMMARY.csv、结果 CSV 和 server.log 的单 run 目录；不直接读取新矩阵 JSONL |
| construct_fixed_candidate.py | 外部 Vanilla CSV + tokenizer 生成候选种子，非性能验收 |
| validate_dtb_intervention_detokenizer.py | 可选服务器真实 decoder 门禁 |

LightCode 专属关联、配对构造器、旧采集 writer 和图表实验流程已经移除。
通用透传采集与当前格式离线阅读使用 [vllm_gateway](../vllm_gateway/README.md)。
