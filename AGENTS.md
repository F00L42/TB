# DTB 交付仓库工程指南

先读 [README](README.md) 和 [RESULT](RESULT.md)；修改 DTB 时继续读 [dtb/AGENTS.md](dtb/AGENTS.md)。
接手路线见[维护导航](docs/maintenance/README.md)；升级 vLLM 先读[接口与 patch 迁移手册](docs/maintenance/vllm_upgrade.md)。
本分支交付 DTB；观察器 SDK 在 `codex/thinking-observer`，不要把两套 serving patch 直接叠加。
通用采集入口是 [vllm_gateway](vllm_gateway/AGENTS.md)。DEER 调试实现及接口已退回 Git 历史，不再作为 patch 依赖。
API 转发 `think_budget`、LightCode 接口和历史实验大数据已退出交付；不要从旧设计恢复它们。

## 算法与协议边界

首要目标是质量不退化下的净 E2E 收益：减少可见 token 的收益必须减去隐藏 probe、重复 prefill、
排队、置信度、预算决策、序列化和状态切换成本。响应 completion usage 不包含隐藏 probe。
静态优化不改变预算、cue、采样、child 顺序、取消、异常或最终答案。

- 只允许缩短思考；传给原生 reasoning/tool parser 的逻辑 token 序列、结构标记、增量顺序、结束和多轮语义必须等价。
  流式同时保留 text/token_ids 连续性，非流式继续用原生完整响应 parser。
  XML 泄漏、重复/丢失工具标记、参数 JSON 不完整、role 尾流、finish_reason 偏离均是 hard failure。
- 所有内部 generate 透传 priority、LoRA、trace headers、DP rank、parser 上下文；保持同一外部请求 DP 亲和性。
- disabled/native 必须回到原生单次生成；不能把阈值设大冒充原生。当前路由优先级见[部署手册](docs/design/dtb_system_routing_deployment.md)。
- 一个请求的多个状态顺序 await，不能计为同时占用所有 max_num_seqs。不同 child ID 也不证明 prefix KV 从零计算。
- vLLM 0.20.2 只作历史解释，目标为 0.23.0；MTP 保持目标环境默认，不作为常规性能变量。

## 并发与源码

服务级配置启动加载，变更需重启；可变 prompt、队列、ledger 属于单请求。不要省掉 child 快照或所有权复制。
缓存不得持有请求正文；关键词 LRU 有界，system 规则按配置一次编译。telemetry/history 关闭态保留短路。
生产代码在 `dtb/`，部署 patch 只有 `patches/vllm/v0.23.0/` 一份；不复制上游实现到包内。
固定子模块在 `third_party/`，用 `git submodule update --init --recursive` 初始化，日常分析优先本地 rg/git blame。
子模块只读；服务器应用 patch 应使用独立的部署 checkout。

## 验证与证据

本机无模型/推理设备，运行 `python tools/dtb/check_static.py`。本地 mock/冻结样本通过不能证明服务器质量或性能。
新服务器验收按 [replay 手册](validation/replay/README.md)；真实并发、质量、性能未执行时写 `pending_remote`。
Windows 非交互子进程显式使用 `stdin=subprocess.DEVNULL`，不要依赖继承 stdin 或 PTY 修复句柄。
不修改其他工作区的 staged/unstaged 内容。文档与结构化数据均 UTF-8。

区分三种证据：**实测**（注明本地或服务器及样本）、**源码确定**、**假设**（给可证伪实验）。
比较同模型/后训练版本、同问题与答案、seed/sampling、并发与到达节奏、cache 状态；先逐请求配对，再看总体分位数。
新模型不得沿用旧 Vanilla。完整记录 vLLM/Ascend commit、torch/torch-npu/CANN、模型修订与 YAML/patch hash。
Judge 字面量 True 才算自动正确，invalid 单列；客户端错误、Judge 错误、hard failure 分开。

## 运行产物与 telemetry

运行数据写 `artifacts/perf/<run_id>/` 或 `artifacts/replay/<run_id>/`，默认不进 Git。
每轮至少保存 manifest、请求/响应与 server request ID、客户端耗时与质量、RESULT；phase 轮还需 server.log 和账本。
CSV 提取接口保持单目录：`python tools/extract_dtb_phase_metrics.py artifacts/perf/<run_id>`，
仅用于含 `_SUMMARY.csv` 和其引用的结果 CSV 的 run；JSONL replay 使用专用路由/phase 审计。
详细规则见[实验契约](docs/design/think_budget_generalization_plan.md)。

- `off`：生产和正式排名默认，无 recorder/writer，无 DTB 成功事件。
- `phase`：独立归属/账本实验，写 DTB_ROUTE、DTB_PHASE schema 3、DTB_REQUEST_SUMMARY；native 无 DTB phase 合法。
- `stream_audit`：仅显式 request ID 白名单，可能含正文和 token ID，不与 off 性能混比。

按 parent_request_id 审计 decision、initial/iterative thinking、confidence_probe、final_answer、controller_cpu。
phase 记录 queue/prefill/decode、prompt/cached/generated token、visible/round/stop_reason。
final 另审计统一 completion 额度、已透传 token、待发边界、剩余额度和 answer_limit。
归属、配置来源、预算分支或样本配对不闭合时只能排障，不能排名；phase wall 与客户端 E2E 差额要解释。
修改实现/默认/结论后同步当前文档与 runbook 变更记录；测试 fixture 不得因缺少旧日志而静默 skip。
