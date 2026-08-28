# ref/lightcode — LightCode 在线评测系统

## 概述

LightCode 是华为内部的云端 AICoding 评测平台。本目录包含与之交互的客户端脚本，用于提交评测任务、查询状态、收集结果。

评测流程：客户端将结构化任务参数（git 仓库、分支、需求描述、patch、ut_patch 等）通过 REST API 提交给 LightCode 平台；平台内部启动一个 Agent（OpenCode），该 Agent 按照 `code-production-evaluation` skill 定义的工作流自主执行 9 个步骤（保存 settings → 克隆代码仓 → 分析 patch → 代码生成 → 编译 → 应用测试补丁 → 编译测试 → 运行 DT → 生成报告），全程无需人工干预。

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
