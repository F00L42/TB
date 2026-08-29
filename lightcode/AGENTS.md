# ref/lightcode — LightCode 在线评测系统

## 概述

LightCode 是华为内部的云端 AICoding 评测平台。本目录包含与之交互的客户端脚本，用于提交评测任务、查询状态、收集结果。

评测流程：客户端将结构化任务参数（git 仓库、分支、需求描述、patch、ut_patch 等）通过 REST API 提交给 LightCode 平台；平台内部启动主 Agent（OpenCode），主 Agent 按照 `code-production-evaluation` skill 定义的工作流自主执行 9 个步骤（保存 settings → 克隆代码仓 → 分析 patch → 代码生成 → 编译 → 应用测试补丁 → 编译测试 → 运行 DT → 生成报告）。patch 分析、代码生成等步骤可通过 `task` 工具启动子 Agent，因此一次 evaluation task 不是单一模型会话，而是主 AgentSession 与 0-N 个子 AgentSession 的树。

## 架构定位：黑盒 Agent 执行环境

LightCode 对调用者而言是一个**完全黑盒的 Agent 执行环境**：

- 调用者只能通过 REST API 提交评测请求、轮询状态、下载结果
- Agent 内部的工作流、工具调用、多轮推理对调用者不可控
- 唯一可配置的参数是 `template_id`（选择评测模板）和并发数/轮询间隔等客户端参数

## 核心限制

### 1. 无法配置模型请求参数

提交评测请求的 API body 是固定结构（`taskInfo` + `template_id` + `env`），**不能**向模型请求注入任何 HTTP body 参数，例如：

- `enable_think_budget` / `enable_deer` — 思维链压缩开关
- `deer_initial_thought_limit` — 思维预算上限
- `chat_template_kwargs` — 聊天模板参数
- `temperature` / `top_p` / `max_tokens` — 采样参数

这意味着**无法通过 LightCode 做 DTB/CoT 压缩参数的对比实验**。

### 2. 无法观察每轮实际请求

Agent 执行过程中的每一步 LLM 调用对调用者不可见：

- 看不到发送给模型的实际 prompt（系统提示 + 用户消息 + 工具调用结果）
- 看不到模型的原始响应（包括 reasoning/thinking token）
- 看不到每次 LLM 调用的 token 用量和延迟
- 只有等任务完成后，通过 session 日志才能回溯，且 session 日志只记录消息级别的内容，不含 HTTP 请求细节

下载后的主 session JSON 仍提供跨日志关联所需的官方拓扑：顶层 `sessionId` 是主 AgentSession；每个 `parts[type=tool, tool=task]` 的 `state.input.prompt` 是子 Agent 首个 user prompt，`state.metadata` 包含父/子 session ID，`state.time` 包含子 Agent 起止时间。它们可以与 proxy 完整日志的首个 user prompt 精确关联，但不能替代 proxy 的逐请求时延和 token 记录。

### 3. 单条评测资源消耗大

从 session 日志分析，**单条评测**消耗：

| 指标 | 数值 |
|---|---|
| 耗时 | ~18 分钟 |
| LLM input tokens | ~5.6M（每步带完整历史上下文） |
| LLM output tokens | ~22K |
| 交互轮次 | ~59 条消息 |
| 工具调用 | ~50+ 次 |

20 条用例串行评测约需 6 小时，不适合做参数扫描。

### 4. 正确性评判由平台闭环完成

- 正确性标准是**编译通过 + DT 通过率**，不是 LLM Judge
- 调用者无法自定义评判逻辑或替换评判模型
- 评测报告在平台内部生成，调用者只能下载最终结果

### 5. 代码仓依赖

每条用例需要访问内部 git 仓库（codehub-y.huawei.com / codehub-dg-g.huawei.com），克隆代码 + checkout base_commit，必须在华为内网环境执行。

## 文件说明

| 文件 | 用途 |
|---|---|
| `concurrent_eval.py` | 并发提交评测任务 + 轮询状态，输出 `evaluation_results.json` |
| `run_eval.py` | 串行提交评测任务（旧版，不轮询） |
| `query_eval.py` | 查询任务状态并更新 Excel |
| `collect.py` | 下载 session 日志，解析评测报告，生成 Excel 汇总 |
| `config.yaml` | API 地址、认证信息、并发数等配置 |
| `patch20/` | 测试用例 JSON 文件目录 |
| `session_logs_*/evaluation_results.json` | 用例文件、LightCode `task_id`、状态、session 下载地址及创建/开始/结束时间 |

## 评测身份与 proxy 关联契约

必须区分以下四层身份：

```text
EvaluationTask(task_id)
└── AgentSession(main/subagent, LightCode sessionId)
    └── ProxySessionSegment(proxy_session_id)
        └── Request(request_id)
```

- `task_id` 由创建任务 API 返回，是一次评测及最终报告的唯一主键。
- 主 AgentSession 来自下载 session JSON 顶层；子 AgentSession 来自主 session 的 `task` 工具元数据。
- ProxySessionSegment 是透传代理按消息历史单调性划分的记录片段。一个官方 AgentSession 可以对应多个 proxy segment，不能假设一对一。
- Request 是一次 chat completion。性能数据最终按 task/agent 聚合，但原始记录必须保留到 request 粒度。

不得为了关联而把 task ID 或追踪文本追加到 `description`、patch、子 Agent prompt 等被测输入，否则会改变模型行为并污染质量/时延比较。当前采用无侵入式离线连接：proxy 对每个请求的首个 user prompt 计算 SHA-256；任务完成后，对主 session 首条 user 消息和 `task.state.input.prompt` 使用相同算法做精确等值关联。Agent 身份只来自精确命中的请求；title generation 等无直接命中的辅助请求，仅在其 proxy segment 中所有 agent 锚点属于同一 evaluation task 时关联到 task，并标记为 `auxiliary`。

完成 session 下载后运行：

```bash
python tools/dtb/correlate_lightcode_proxy.py \
  <PROXY_RESULTS_DIR> <LIGHTCODE_SESSION_LOG_DIR> <OUTPUT_DIR>
```

`SUMMARY.md` 给出 task 级概览，`tasks/<task_id>.md` 展示主/子 Agent 拓扑和逐请求时间线；`agent_map.jsonl`、`requests.jsonl` 和 `unmatched.json` 用于机器审计。关联器不使用时间窗口或模糊文本作为静默兜底。

正式 DTB/Vanilla 对比继续配置单并发；`concurrent_eval.py` 必须在 `evaluation_results.json` 中保存 `create_time`、`start_time`、`end_time`，使 LightCode 平台 wall time 与各 Agent 的 LLM E2E 汇总可以并列阅读。平台 wall time 包含环境构建、工具执行、编译和测试，不等于 LLM E2E 之和。
