# Agent 多轮调用场景 DTB 参数优化实验方案

> **2026-08-29 日志契约更新**：本文中单体 `full_log.jsonl` 和“一个 proxy session 对应一个任务”的描述只适用于历史 run。新实验使用 `full_log/proxy_session_<id>/part-*.jsonl` 有界分片，并在下载 LightCode session 后通过逐请求 root user prompt 指纹生成 task/主子 agent 关联报告。规范接口、完整性规则和命令以 `proxy/AGENTS.md` 第 4.4-4.5 节为准。

## 1. 目标与场景定义

### 1.1 目标

在 Agent 辅助研发场景（如 CodeAgent/OpenCode 自主完成代码生成、编译、测试循环）中，通过 DTB 压缩思维链长度，实现**端到端任务完成时间**的降低，同时不使任务成功率显著退化。

端到端收益公式：

```text
净时延收益
= Σ(每轮减少的 thinking token × TPOT) - Σ(每轮 probe 额外成本 + controller 开销)
```

多轮场景的特殊性：单条任务涉及 ~60 次 LLM 调用，thinking token 占 output 的绝大部分；即使单轮压缩收益小，累积效应显著。但同时，工具调用轮次的 thinking 被过度压缩可能导致工具参数错误，其影响会级联到后续所有轮次。

### 1.2 场景约束

| 约束 | 来源 | 影响 |
| --- | --- | --- |
| LightCode 是黑盒环境，不能注入 DTB 参数或观察每轮请求 | `lightcode/AGENTS.md` | 不能用 LightCode 做 DTB 参数对比实验 |
| 单条 LightCode 评测消耗 ~5.3M input token、~22K output token、~18 分钟 | 实测 `token_usage.jsonl` | 不适合参数扫描 |
| 正确性由编译 + DT 通过率闭环评判 | `lightcode/AGENTS.md` | 不能自定义评判逻辑 |
| vLLM 服务端已部署 DTB patch，支持 `enable_think_budget` 等 body 参数 | `start.sh` + serving patch | 可通过 HTTP body 注入 DTB 参数 |
| 服务端 telemetry 默认 `off`，需改 `budget_config.yaml` 并重启 | `dtb/config/budget_config.yaml` | 账本 run 需独立部署，不能与正式性能排名混口径 |
| lightcode 支持单并发执行 | 用户描述 | 串行评测，控制变量简单 |
| 推理服务无状态，每轮请求独立 | 架构事实 | 代理只需固定参数注入，无需轮次感知逻辑 |

### 1.3 可行路径

绕过 LightCode 黑盒限制的方法：**在本地部署 Agent + 透传代理**。

```text
[Agent (OpenCode/CodeAgent)]
    |
    v
[透传代理 :9000]     ← 注入 DTB 固定参数 + 记录每轮交互
    |
    v
[vLLM API :9001]     ← DTB patch + telemetry
    |
    v
[server.log]          ← 服务端 phase 账本
```

透传代理基于 `qwen_transparent.py` 扩展，位于 Agent 和 vLLM 之间。Agent 的 OpenAI 兼容端点指向代理而非直连 vLLM。

### 1.4 实测基线参考

基于 `results/qwen/local-task29/local-apiserver/dtb/run-01/` 的实际运行数据：

| 指标 | 数值 |
| --- | --- |
| 非空请求总数 | 64 |
| 总 input token | 5,274,273 |
| 总 output token | 22,111 |
| 总 reasoning token（文本长度） | 18,760 字符 |
| 初始轮次（无 tool_result） | 2，平均 reasoning 184 字符 |
| 工具返回轮次 | 62，平均 reasoning 297 字符 |
| 含 tool_calls 的响应 | 62/64 |
| reasoning 长度范围 | 23–2,447 字符 |

LightCode 评测结果（`evaluation_report_20260825153859.json`）：
- DT 通过率 93.18%（41/44），评级"优秀"
- 3 个 error 均为遗漏问题（i18n 配置 + null 防护），非逻辑错误

关键观察：Agent 场景中 62/64 轮都是工具调用轮次，reasoning 文本长度集中在 23–300 字符，仅少数轮次超过 1000 字符。这意味着：
- 单轮 thinking token 本身就不长，DTB 的主要收益不是大幅削减 thinking，而是通过更早的 confidence accepted 减少不必要的 iterative think 轮次和 probe 成本
- 工具调用占比极高，参数选择必须保证工具调用格式不被破坏

## 2. 模块设计

### 2.1 透传代理 `agent_dtb_proxy.py`

基于 `qwen_transparent.py` 扩展，新增以下能力：

#### 2.1.1 任务会话跟踪

每个 Agent 任务（如一条 LightCode 评测）对应一个 session。Agent 多轮调用共享同一 session。

```text
Session
  session_id           # 唯一标识
  task_description     # 任务描述（来自首个用户消息，截取前 200 字符）
  dtb_config           # 本 session 使用的 DTB 参数组
  turns: list[Turn]    # 每轮交互记录
  started_at           # 首轮请求时间
  finished_at          # 末轮响应时间
```

Session 的生命周期：由 messages 数组的单调性决定。同一 session 内，每轮请求的 `messages` 数组长度严格递增（Agent 在历史消息后追加新内容）；当新请求的 `len(messages) ≤ 上一轮 len(messages)` 时，标志新 session 开始、旧 session 结束。见 4.3 节详细定义。

#### 2.1.2 每轮交互记录

```text
Turn
  turn_index           # 轮次序号（0-based）
  request_id           # 请求唯一 ID（自生成 UUID）
  post_time            # 请求发出时间
  receive_time         # 响应完成时间
  e2e_ms               # receive_time - post_time

  # 输入侧
  n_messages           # messages 数组长度
  n_tool_results       # messages 中 role=tool 的数量
  input_prompt_tokens  # prompt_tokens（来自 usage）
  turn_type            # 轮次类型：initial / tool_result / followup

  # DTB 参数
  injected_dtb_params  # 实际注入的 DTB 参数

  # 输出侧
  output_completion_tokens   # completion_tokens
  output_reasoning_tokens    # reasoning_tokens（来自 usage）
  output_tool_tokens         # tool_tokens（若有）
  output_content_length      # content 文本长度
  output_reasoning_length    # reasoning 文本长度
  has_tool_calls             # 响应是否包含 tool_calls
  stop_reason                # stop_reason

  # 思考比例
  thinking_ratio        # reasoning_length / (reasoning_length + content_length)
```

注意：实测数据显示当前 `usage` 中 `reasoning_tokens` 为 0（vLLM 不在 usage 中返回 reasoning token），因此 thinking_ratio 使用文本长度比而非 token 比。

#### 2.1.3 DTB 参数注入

代理只使用**固定参数模式**：启动时在 `APPEND_BODY` 中配置 DTB 参数，所有请求注入相同参数。

推理服务是无状态的，每轮请求独立。实验方法是通过多轮实验观察不同参数组的效果，找到最优参数后，将其配置到 `budget_config.yaml` 的 SCENES 中作为新的 Agent 场景预算。后续请求只需 `enable_think_budget=true` 即可自动命中。

```python
# 示例：扫描 dtb_fixed_moderate 组
APPEND_BODY = {
    "model": "qwen3.6-27b",
    "enable_think_budget": True,
    "fixed_budget_tokens": 20000,
    "fixed_check_length": 400,
    "fixed_think_stride": 200,
    "fixed_num_thinking": 4,
    "fixed_min_think_tokens": 400,
    "fixed_prob_th": 0.93,
}
```

每组实验只需修改 `APPEND_BODY` 并重启代理，无需动态切换。分析基于每轮实验输出的文件。

#### 2.1.4 会话统计与交互记录输出

每个 session 结束后，代理输出两类文件：

**A. 结构化汇总**（`session_<id>.json` + `session_summary.jsonl`）

```text
SessionSummary
  session_id
  dtb_config_name         # 使用的参数组名（来自配置文件）
  total_turns             # 总轮次
  total_e2e_ms            # 总端到端时间
  total_prompt_tokens     # 总输入 token
  total_completion_tokens # 总输出 token
  total_reasoning_tokens  # 总思考 token
  total_tool_tokens       # 总工具 token
  avg_thinking_ratio      # 平均 thinking_ratio
  tool_call_turns         # 含工具调用的轮次数
  tool_result_turns       # 含工具返回的轮次数
  turns: list[Turn]       # 完整轮次明细
```

**B. 人类可读交互记录**（`interaction_log.md`）

除原始 JSONL 格式外，代理额外输出一份 Markdown 格式的交互记录，将每轮的 reasoning、content、tool_calls 各自拼接为完整字符串，便于人工审阅。

```markdown
# Session <session_id>

- DTB Config: dtb_moderate
- Duration: 15m 32s
- Turns: 64

---

## Turn 0 | 15:20:17 | e2e=5.1s | type=initial | prompt=46820 tokens

### Reasoning (119 chars)
The user wants me to use the `code-production-evaluation` skill to evaluate
a test case. Let me load this skill first.

### Content
(empty)

### Tool Calls
1. **skill**({"name": "code-production-evaluation"})

---

## Turn 1 | 15:20:32 | e2e=5.0s | type=tool_result | prompt=50762 tokens

### Reasoning (282 chars)
I need to clone the repository first and check the test cases...

### Content

I'll start by cloning the repository and examining the test cases.

### Tool Calls
1. **todowrite**({"todos": [{"content": "步骤0：保存Settings文件", ...}]})

---

## Turn 63 | 15:38:42 | e2e=3.2s | type=tool_result | prompt=117079 tokens

### Reasoning (216 chars)
Now I need to update the generateReportTemplate method since I moved the
`template` variable extraction before the try block...

### Content

Now I need to fix the `generateReportTemplate` try block and add the name
check to `modifyReportTemplate`:

### Tool Calls
1. **edit**({"filePath": ".../IntelligentTemplateDelegateImpl.java", ...})
2. **edit**({"filePath": ".../CheckErrorCode.java", ...})

---
```

格式规则：

| 字段 | 拼接方法 | 空值处理 |
| --- | --- | --- |
| Reasoning | 流式 delta.reasoning 逐块拼接为完整字符串 | 输出 `(empty)` |
| Content | 流式 delta.content 逐块拼接为完整字符串 | 输出 `(empty)` |
| Tool Calls | 从流式 delta.tool_calls 重建为结构化对象：按 index 分组合并 arguments，再格式化为 `name(arguments)` | 无 tool_calls 时不输出该节 |

**Tool Calls 重建**：`qwen_transparent.py` 现有的 `accumulated_tool_call` 是 `json.dumps(delta["tool_calls"])` 的逐块拼接，多个 JSON 数组直接相连（如 `[{...}][{...}]`），无法直接解析。代理需实现重建逻辑：

```python
def reconstruct_tool_calls(accumulated_str: str) -> list[dict]:
    """从流式 delta 拼接的 tool_calls 字符串重建结构化对象。
    
    输入格式: '[{"id":"call_...","function":{"name":"bash","arguments":""}}]'
              '[{"index":0,"function":{"arguments":"{"}}]'
              '[{"index":0,"function":{"arguments":"\"command\": ..."}}]'
              ...
    
    输出: [{"id": "call_...", "name": "bash",
            "arguments": {"command": "...", "timeout": 120000}}]
    """
    # 1. 在 ][ 边界插入逗号，外包数组，解析为 delta 列表
    fixed = accumulated_str.replace('][', '],[')
    deltas = json.loads('[' + fixed + ']')
    
    # 2. 按 index 分组合并
    tool_calls = {}
    for delta in deltas:
        idx = delta.get('index', 0)
        if idx not in tool_calls:
            tool_calls[idx] = {'id': '', 'name': '', 'arguments': ''}
        if delta.get('id'):
            tool_calls[idx]['id'] = delta['id']
        fn = delta.get('function', {})
        if fn.get('name'):
            tool_calls[idx]['name'] = fn['name']
        if fn.get('arguments'):
            tool_calls[idx]['arguments'] += fn['arguments']
    
    # 3. 尝试 JSON.parse 每个 arguments 字符串
    for tc in tool_calls.values():
        try:
            tc['arguments'] = json.loads(tc['arguments'])
        except json.JSONDecodeError:
            pass  # 保留原始字符串
    
    return [tc for _, tc in sorted(tool_calls.items())]
```

**Tool Result 输入**：本轮请求中 messages 里 `role=tool` 的内容也需要记录在交互日志中，以便完整还原每轮的输入上下文。从 `full_log.jsonl` 的 `request_body.messages` 中提取，按 tool_call_id 与上一轮的 tool_calls 对应。

输出目录沿用 `qwen_transparent.py` 的结构：`results/{model}/{task}/{receiver}/{dtb_config}/run-{序号}/`，新增：
- `session_<id>.json` — 结构化 session 数据
- `session_summary.jsonl` — 逐 session 汇总（每行一个 JSON）
- `interaction_log.md` — 人类可读交互记录

已有的 `token_usage.jsonl`、`full_log.jsonl` 和 `chunks.jsonl` 保持不变，确保向后兼容。

### 2.2 分析管线

#### 2.2.1 客户端侧分析（代理日志）

从代理的 `session_summary.jsonl` 和各 `session_*.json` 提取：

```text
Per-Session Metrics:
  - total_e2e_ms          # 任务端到端时间
  - total_completion_tokens
  - total_reasoning_tokens
  - avg_thinking_ratio
  - tool_call_turns / tool_result_turns

Per-Turn Metrics:
  - e2e_ms per turn
  - reasoning_length per turn
  - thinking_ratio per turn
  - 按轮次类型（initial / tool_result）分组的 thinking_ratio 分布

Cross-Session Comparison:
  - Vanilla vs DTB 各参数组的逐任务 E2E 对比
  - 逐轮 reasoning_length 分布对比
  - 任务成功率对比（结合 LightCode 评测报告）
```

#### 2.2.2 服务端侧分析（复用 TB telemetry）

当 telemetry 模式设为 `phase` 时，服务端记录每个 DTB phase 的详细账本。实验结束后：

```bash
python tools/extract_dtb_phase_metrics.py docs/log/perf/<run_id>
```

提取器输出 `phase_summary.csv` 和 `requests.jsonl`，可与代理日志通过 `parent_request_id` 关联。

**关联方法**：代理为每个请求生成的 `request_id` 写入 `full_log.jsonl`。提取后的 `requests.jsonl` 中的 `parent_request_id` 与代理日志中的 `request_id` 做等值关联（通过请求时间窗口 + prompt_tokens 交叉校验）。

关联后可获得：
- 每轮的 `initial_think` / `iterative_think` / `confidence_probe` / `final_answer` 各 phase 的 wall_ms、queue_ms、prefill_ms、decode_ms
- hidden probe token 数量
- scene 分类结果和 decision_branch
- 真实模型工作量（visible_think + hidden_probe + final_answer）

#### 2.2.3 参数推荐与场景配置

实验目标是找到最优固定参数后，将其配置为 `budget_config.yaml` 中的新 scene，使后续请求只需 `enable_think_budget=true` 即可自动命中。推荐流程：

```text
Step 1: 质量门
  - DT 通过率不低于 Vanilla - 5%（Agent 场景容差大于单轮场景，
    因为单条任务含 ~60 轮调用，DTB 影响面更广）
  - 工具调用格式错误不高于 Vanilla
  - 不通过则排除该参数组

Step 2: 时延排序
  - 通过质量门的参数组，按端到端 E2E 排序
  - 优先选择 E2E 最低者

Step 3: 场景配置
  - 将推荐参数写入 budget_config.yaml 的 SCENES 中
  - 新增 scene 如 agent_code_production：
      indicators:
        required_all: []    # 不依赖关键词匹配
        required_any: []
        strong: []
        weak: []
      # 直接使用 enable_think_budget=true + scene 直达预算
  - 或复用已有 scene 的参数签名（如果推荐参数与某 scene 一致）

Step 4: 验证自动模式
  - 重启 vLLM，仅传入 enable_think_budget=true
  - 确认 scene 分类正确命中新配置的预算
  - 跑一轮验证任务确认效果
```

#### 2.2.4 与已有 13 场景结果的关系

Agent 多轮场景不是 13 场景中的任何单一场景，而是一个**混合场景**：

- 首轮（任务理解）→ 近似 `react_whitebox_overall_planning`
- 工具调用轮次 → 近似 `complaint_tool_use`
- 代码生成轮次 → 近似 `code_generation`
- 测试执行轮次 → 近似 `react_whitebox_single_step`

但推理服务是无状态的，DTB 对每个请求独立决策。因此：
- 最终配置是在 `budget_config.yaml` 中新增一个 `agent_code_production` scene，而非为每轮类型配置不同预算
- 该 scene 的预算参数由固定参数扫描实验确定，是对所有轮次的统一最优折中
- 如果后续发现特定轮次类型需要差异化，应在 DTB 服务端的 scene 分类逻辑中实现，而非在代理侧

## 3. 实验方案

### 3.1 阶段零：基线建立

**目的**：获取 Vanilla 基线和 `dtb_auto` 基线。

**步骤**：

1. 部署透传代理（不含 DTB 注入），确认 Agent 可正常通过代理完成任务
2. 选择 5–10 个典型辅助研发任务（编译 + DT 通过率可判定的）
3. 串行执行，每组参数跑一遍全部任务
4. 收集代理日志 + LightCode 评测报告

**参数组**：

| 组名 | APPEND_BODY | RESULTS_DIR |
| --- | --- | --- |
| `vanilla` | `{"model": "qwen3.6-27b"}` | `results/qwen/local-task29/local-apiserver/vanilla/run-01` |
| `dtb_auto` | `{"model": "qwen3.6-27b", "enable_think_budget": true}` | `results/qwen/local-task29/local-apiserver/dtb-auto/run-01` |

**关键观测**：

- Vanilla 下每轮 reasoning_length 的分布（哪些轮次 thinking 最长）
- `dtb_auto` 的 scene 分类覆盖情况（预期：多数 Agent 请求会被 reject 到 general）
- 13 场景中是否有可复用的预算签名
- `dtb_auto` 是否出现工具调用格式错误

### 3.2 阶段一：固定参数扫描

**目的**：在 Agent 多轮场景上搜索质量合格的最优固定预算。

**参数空间**：基于阶段零的 Vanilla reasoning_length 分布和 13 场景的已验证参数，设计 3–5 组固定参数。

实测基线参考：Vanilla reasoning 长度均值 ~297 字符（工具返回轮次），最大 2,447 字符。按 Qwen3 token 比约 1.5–2 token/字符，thinking token 约为 450–600 均值、3,700 最大值。

参数设计（参考 13 场景中类似规模的预算）：

| 组名 | budget_tokens | check_length | stride | num_thinking | min_think | prob_th | 参考场景 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `dtb_tight` | 850 | 500 | 250 | 2 | 500 | 0.93 | react_machine_data_analysis |
| `dtb_moderate` | 1,100 | 450 | 200 | 4 | 450 | 0.93 | react_whitebox_single_step |
| `dtb_loose` | 1,650 | 1,000 | 250 | 3 | 1,000 | 0.93 | complaint_tool_use |
| `dtb_planning` | 3,050 | 950 | 450 | 5 | 950 | 0.95 | netqad_e2e_planning |

**关键约束**：

- 每组参数必须通过质量门（DT 通过率 ≥ Vanilla - 5%）
- 工具调用格式错误不高于 Vanilla
- 若所有固定参数组都失败，说明 Agent 场景可能不适合统一压缩，需考虑更宽松的预算或放弃压缩

### 3.3 阶段二：场景配置与自动模式验证

**目的**：将最优参数配置到 `budget_config.yaml`，验证"开启即优化"。

**方法**：

1. 从阶段一选出质量门内 E2E 最优的参数组
2. 在 `budget_config.yaml` 的 SCENES 中新增 `agent_code_production`，写入推荐预算
3. 配置 indicators：由于 Agent 请求的特征（大量工具调用、长 context）与 13 个产品场景不同，需要设计专用 indicators 或使用 required_all 为空（由 `enable_think_budget=true` + scene_override 显式指定）
4. 重启 vLLM 服务
5. 仅传入 `enable_think_budget=true`，跑验证任务
6. 确认 scene 命中率和预算生效

**备选路径**：如果新增 scene 的 indicators 难以设计（Agent 请求的 messages 格式差异大），可考虑：
- 在请求中直接传入 `fixed_*` 参数（简单可靠，但需要 Agent 侧配合）
- 使用 scene_override 机制（请求中指定 scene 名，服务端直达预算）

### 3.4 阶段三：telemetry 联合分析与参数确认

**目的**：合并客户端和服务端数据，确认最终推荐参数的净收益。

**步骤**：

1. 在最优参数组上运行 telemetry=phase 的账本 run
2. 用 `extract_dtb_phase_metrics.py` 提取 phase 数据
3. 关联代理日志与 phase 数据
4. 完成模型工作量闭合：visible_think + hidden_probe + final_answer
5. 计算净时延收益
6. 写入 `RESULT.md`，遵循 AGENTS.md 的证据规则

## 4. 透传代理实现规格

### 4.1 文件布局

新增一个文件 `agent_dtb_proxy.py`，与 `qwen_transparent.py` 并列放在仓库根目录：

```text
/opt/p00906181/
  qwen_transparent.py      # 原有透传代理（保持不变）
  agent_dtb_proxy.py       # 增强透传代理（新增）
  analysis/
    analyze_sessions.py    # 客户端侧分析脚本
    correlate_telemetry.py # 关联服务端 telemetry
    recommend_params.py    # 参数推荐逻辑
```

### 4.2 代理核心改动（相对 qwen_transparent.py）

| 功能 | qwen_transparent.py 现有 | agent_dtb_proxy.py 改动 |
| --- | --- | --- |
| 请求转发 | ✅ | ✅ 保持 |
| APPEND_BODY 合并 | ✅ 静态配置 | ✅ 保持静态配置，实验间手动修改并重启 |
| token_usage.jsonl | ✅ 逐请求 | ✅ 保持 |
| full_log.jsonl | ✅ 逐请求 | ✅ 保持，增加 request_id 字段 |
| chunks.jsonl | ✅ 逐请求 | ✅ 保持（可按需关闭） |
| 会话跟踪 | ❌ | ✅ 新增：按 n_messages 单调性自动识别 session 边界 |
| 人类可读交互记录 | ❌ | ✅ 新增：interaction_log.md，reasoning/content/tool_calls 各自拼接为完整字符串 |
| Tool Calls 重建 | ❌ | ✅ 新增：从流式 delta 拼接重建为结构化对象 |
| 轮次分类 | ❌ | ✅ 新增：根据 messages 判断轮次类型 |
| session 汇总输出 | ❌ | ✅ 新增：session_*.json + session_summary.jsonl |
| 请求 ID 生成 | ❌ | ✅ 新增：生成 UUID 并记录 |

不新增管理 API，不新增运行时参数切换。代理的职责明确：透传 + 记录 + 统计。实验参数通过配置文件控制，分析基于输出文件。

### 4.3 Session 识别策略

Agent 多轮调用的 HTTP 请求之间没有显式的 session 关联。代理通过 **messages 数组单调性** 识别 session 边界，该方法基于实测数据验证。

#### 4.3.1 核心规则：`n_messages` 单调递增

同一 session 内，Agent 每轮请求将上一轮的 assistant response 和 tool result 追加到 messages 数组后发送，因此 `len(messages)` 在 session 内严格递增。

**Session 边界判定**：当新请求的 `len(messages) ≤ 上一个请求的 len(messages)` 时，标志新 session 开始。

实测验证（`results/qwen/local-task29/local-apiserver/dtb/run-01/`，64 个请求）：

| 检测方法 | 检测结果 | 可靠性 |
| --- | --- | --- |
| `n_messages` 非递增 | 检测到 2 个 session（21 轮 + 43 轮） | ✅ 确定性，无阈值 |
| 时间间隙（5 分钟） | session 内最大间隙 122.3s，边界间隙仅 27.1s | ❌ 无法区分 |
| 首条用户消息 hash | 同一 session 内 first_user_len 可能不同 | ⚠️ 仅辅助 |

#### 4.3.2 Session 完成信号

- **确定信号**：新 session 开始时，旧 session 隐式关闭
- **辅助信号**：session 最后一个请求的 `has_tool_calls=False`，表示 Agent 不再调用工具，任务完成
- **兜底信号**：代理关闭时，所有活跃 session 强制关闭

#### 4.3.3 Agent 重启对话的特殊情况

实测发现 Agent 可能中途重启对话（旧 session 末尾 `has_tool_calls=True`，新 session 使用不同的消息结构），此时两个 session 属于同一任务。代理按独立 session 分别记录，在分析阶段由 `analyze_sessions.py` 通过时间窗口和任务标识（如 LightCode task_id）合并为同一逻辑任务。

### 4.4 轮次类型判断

通过分析 `messages` 数组的内容判断当前轮次类型（仅用于记录，不影响参数注入）：

```python
def classify_turn(messages: list[dict]) -> str:
    """判断当前请求的轮次类型"""
    last_msg = messages[-1] if messages else {}

    # 首轮：无 tool role 的消息
    if not any(m.get("role") == "tool" for m in messages):
        return "initial"

    # 工具返回轮次：存在 tool role 的消息
    if any(m.get("role") == "tool" for m in messages):
        return "tool_result"

    return "other"
```

### 4.5 与服务端 telemetry 的关联

代理为每个请求生成的 `request_id`（UUID）写入 `full_log.jsonl` 的 `request_id` 字段。

关联脚本 `correlate_telemetry.py` 的输入：
- 代理日志目录（含 `session_*.json` 和 `full_log.jsonl`）
- 提取器输出目录（含 `requests.jsonl` 和 `phase_summary.csv`）

关联方法：
1. 从 `full_log.jsonl` 提取每轮的 `request_id`、`post_time`、`input_prompt_tokens`
2. 从提取器的 `requests.jsonl` 提取 `parent_request_id`、phase 时间和 token
3. 通过 `prompt_tokens` + 时间窗口交叉匹配（`full_log` 中的 `request_id` 不一定等于 vLLM 的 `parent_request_id`，需通过 prompt_tokens 和时间戳做模糊关联）

关联后输出：
- `correlated_sessions.json` — 每个 session 的每轮同时包含客户端和服务端数据
- `correlated_turns.csv` — 逐轮对比表

## 5. 实验执行 Runbook

### 5.1 环境准备

```bash
# 1. 确认 vLLM 服务已启动
curl http://localhost:9001/v1/models

# 2. 如需 telemetry，修改配置并重启
# 编辑 TB/dtb/config/budget_config.yaml:
#   OBSERVABILITY.mode: "phase"
# 重启 vLLM 服务

# 3. 启动透传代理
cd /opt/p00906181
python agent_dtb_proxy.py

# 4. 配置 Agent 使用代理作为 endpoint
# Agent 的 OpenAI base_url 指向 http://localhost:9000/v1
```

### 5.2 基线实验

```bash
# Vanilla 基线：修改 agent_dtb_proxy.py 的 APPEND_BODY
APPEND_BODY = {"model": "qwen3.6-27b"}
RESULTS_DIR = "results/qwen/local-task29/local-apiserver/vanilla/run-01"
# 重启代理，运行任务

# dtb_auto 基线
APPEND_BODY = {"model": "qwen3.6-27b", "enable_think_budget": True}
RESULTS_DIR = "results/qwen/local-task29/local-apiserver/dtb-auto/run-01"
# 重启代理，运行任务
```

### 5.3 参数扫描

```bash
# 每组参数修改 APPEND_BODY 和 RESULTS_DIR 后重启代理
# dtb_tight
APPEND_BODY = {
    "model": "qwen3.6-27b",
    "enable_think_budget": True,
    "fixed_budget_tokens": 850,
    "fixed_check_length": 500,
    "fixed_think_stride": 250,
    "fixed_num_thinking": 2,
    "fixed_min_think_tokens": 500,
    "fixed_prob_th": 0.93,
}
RESULTS_DIR = "results/qwen/local-task29/local-apiserver/dtb-tight/run-01"

# dtb_moderate / dtb_loose / dtb_planning 同理
```

### 5.4 分析

```bash
# 客户端侧分析
python analysis/analyze_sessions.py results/qwen/local-task29/local-apiserver/

# 服务端 telemetry 提取（如已开启）
python tools/extract_dtb_phase_metrics.py docs/log/perf/<run_id>

# 关联分析
python analysis/correlate_telemetry.py \
  --proxy-dir results/qwen/local-task29/local-apiserver/ \
  --telemetry-dir docs/log/perf/<run_id>/extracted/

# 参数推荐
python analysis/recommend_params.py \
  --analysis-dir results/qwen/local-task29/local-apiserver/analysis/
```

### 5.5 结果收集

每条任务完成后，需要同时收集：
- 代理侧日志（`session_*.json` + `token_usage.jsonl` + `full_log.jsonl`）
- LightCode 评测报告（从 `session_logs/` 下载，含 DT 通过率等质量指标）

评测报告路径格式：`lightcode/session_logs/task_<id>_r<retry>/logs/evaluation_report_<timestamp>.json`

关键质量指标从评测报告中提取：
- `dt_results.pass_rate` — DT 通过率
- `code_generation.status` — 代码生成状态
- `compilation.main_code.status` / `compilation.test_code.status` — 编译状态

## 6. 预期输出与交付物

### 6.1 每轮实验交付

| 交付物 | 路径 | 内容 |
| --- | --- | --- |
| 交互记录 | `results/<model>/<task>/<config>/interaction_log.md` | 人类可读的逐轮 reasoning/content/tool_calls 拼接 |
| 代理日志 | `results/<model>/<task>/<config>/session_*.json` | 结构化 session 数据 |
| Token 记录 | `results/<model>/<task>/<config>/token_usage.jsonl` | 逐请求 token 统计（沿用原有格式） |
| 完整日志 | `results/<model>/<task>/<config>/full_log.jsonl` | 请求/响应明细（沿用原有格式） |
| LightCode 报告 | `lightcode/session_logs/task_*/logs/evaluation_report_*.json` | 编译 + DT 结果 |
| 实验报告 | `results/<model>/<task>/<config>/RESULT.md` | 遵循 AGENTS.md 证据规则 |

### 6.2 RESULT.md 模板

```markdown
# <run_id>：Agent 多轮场景 DTB 参数扫描

## 0. 结论

## 1. 输入、口径与数据闭合
- 任务集：N 个辅助研发任务
- 参数组：vanilla / dtb_auto / dtb_tight / dtb_moderate / dtb_loose / dtb_planning
- 配对方式：同一任务、不同参数组

## 2. 逐参数组结果
| 参数组 | DT 通过率 | 平均 E2E | 总 reasoning | 总 completion | 工具调用错误 |

## 3. 逐轮分析
- 初始轮次 vs 工具返回轮次的 reasoning 分布
- 工具调用轮次的压缩安全性

## 4. 模型工作量与 phase 账本（如有 telemetry）
- visible_think / hidden_probe / final_answer token
- phase wall time 分布

## 5. 参数推荐与场景配置
- 推荐参数组及其理由
- budget_config.yaml 中的 scene 配置建议

## 6. 证据强度
```

## 7. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| Agent 无法配置自定义 endpoint | 需确认 Agent 支持配置 OpenAI base_url；否则需修改 Agent 代码或使用 iptables/环境变量重定向 |
| 多轮间 prefix cache 命中率影响 E2E | 每组参数前清理 cache 或确保 cache 状态一致；在分析中分离 prefill 和 decode |
| 工具调用格式对 thinking 压缩敏感 | 优先监控工具调用轮次的格式错误率；必要时使用更宽松的预算 |
| 代理引入额外时延 | 代理是纯内存转发，额外时延 < 1ms；在 session 记录中包含代理处理时间以确认 |
| session 识别错误 | `n_messages` 单调性是确定性信号，无需阈值调优；Agent 重启对话的场景由分析脚本按时间窗口合并 |
| telemetry=phase 的性能开销 | phase 模式不影响 E2E 排名；但不应把 phase run 和 off run 的 E2E 混合比较 |
| reasoning_tokens 在 usage 中为 0 | 使用 reasoning 文本长度替代 token 计数计算 thinking_ratio；telemetry 数据可提供精确的 visible_think token |
| LightCode 评测结果波动 | 同一任务可能因采样随机性产生不同代码；固定 temperature=0 减少波动 |

## 8. 与阶段一的关系

本方案是 `think_budget_generalization_plan.md` 的扩展，不是替代：

- 阶段一关注 13 个产品单轮场景的"开启即优化"
- 本方案关注 Agent 多轮场景的端到端加速
- 阶段一的 13 组预算参数可作为本方案阶段零的初始候选
- 本方案的推荐参数需要独立的质量门验证，不能继承阶段一的非劣结论
- 最终推荐参数将写入 `budget_config.yaml` 的 SCENES 中作为新的 Agent 场景，后续只需 `enable_think_budget=true` 即可自动命中
