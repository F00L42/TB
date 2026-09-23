# 性能实验与证据契约

当前交付不携带历史实验大数据。新 run 写 artifacts/perf/<run_id>，小型验证写 artifacts/replay/<run_id>。
历史 13 场景问题保留于 tests/fixtures/scenes，作为路由回归；问题本身不是质量参考答案或性能结果。

1. 确认实验身份：模型/量化/后训练修订、服务与 DTB commit、policy/YAML/patch hash、observability、
   sampling/seed、并发、到达节奏、cache。模型变更必须重新测 Vanilla。
2. 核对请求归属：CSV run 使用 tools/extract_dtb_phase_metrics.py 单目录提取，以具体结果的非空 request_id 为白名单。
   必需输入是根 server.log、唯一 _SUMMARY.csv 与引用的结果 CSV；不伪造未上传的大日志。
   JSONL 交付 replay 使用自身 summary.jsonl 与 phase 审计工具，不冒充 CSV run。
3. 审计完整性：请求数、唯一 ID、日志覆盖、实际 route/budget 分支、reject、配置来源与签名。
   native 合法无 phase；off DTB 没有 phase 也是设计行为。缺归属、重复 route、配置不符只能排障。
4. 精确配对：以规范化数据集与 QA_ID/case_id 配对，还要验证问题/答案或数据 SHA；缺失/重复/不同样本集合禁止静默排除。
   先报告逐请求差值与更快请求数，再看逐场景、等权宏平均、请求加权、P50/P95。
5. 质量独立：Judge 仅字面量 True 算正确，invalid 单列。人工覆盖需样本/结论/理由。
   客户端错误、Judge 错误、hard failure 分开统计；样本 replay 可用精确答案门，不能外推一般 Agent 成功率。
6. 工作量闭合：visible thinking、hidden probe、final token 分列；phase wall/queue/prefill/decode 与 E2E 对账。
   response completion 不包含 hidden probe；同一请求的不同 child ID 不证明 prefix cache 失效。
7. RESULT 写结论、身份与证据边界、完整性、实际分支/配置、质量转移、配对差值、phase 账本、异常与下一步。
   标明实测（本地/服务器）、源码确定、假设，不把局部 CPU 基准当 E2E。

正式排名只用 off，phase/stream_audit 独立运行。MTP 保持目标环境默认。需要更换并发或 cache 时另开身份一致的对照。
最小服务器包包含 manifest、客户端 summary/响应、server request IDs、RESULT；phase 包另有 server.log、提取清单及账本。
验收候选应在**同一目标数据质量不退化**前提下比较 E2E；一次更快、小型合成集或 token 变少都不够。

候选构造辅助 tools/construct_fixed_candidate.py 接受外部 Vanilla CSV 和实际 tokenizer；只提供候选种子，不能替代上述验收。
历史 section 6.2 的统计口径以工具输出 manifest 为准，不再依赖被删除的实验目录。
