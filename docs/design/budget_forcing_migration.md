# BudgetForcing 思维链预算机制迁移设计文档

## 1. 设计目的

### 1.1 背景

DEER 思维链压缩模块（`deer/`）已集成在 vLLM Server 进程中，通过状态机驱动\"思考→预回答→置信度校验→提前退出\"的迭代过程。当前 DEER 的置信度计算仅使用简单的几何平均概率（`_calculate_average_max_prob_from_logprobs`），缺乏对结构化输出（JSON）中 Value token 与结构 token 的区分，且没有预算优先级链、难度估计、历史轨迹预算等高级机制。

`/opt/p00906181/260707/think_budget` 项目（BudgetForcing）实现了更精细的思维链预算控制，包含：
- **P3.1 Value Min Confidence**：过滤结构 token，仅对 Value token 取 Bottom-K 均值
- **P3.3 Clean Context**：继续推理时丢弃诱导语，不累积试探答案
- **预算优先级链**：固定预算 > 任务类型预算 > 难度估计 > 历史轨迹预算
- **安全门**：超时回退
- **软截断**：在自然断点处闭合思考标签
- **双通道/完整链置信度**：推理区 + 答案区 AND 门
- **难度估计器**：14 维特征融合 → 分段预算
- **历史轨迹预算**：冷/暖/热启动模式
- **任务类型分类**：自动推断任务类型并应用对应预算
- **DEER Trial 评估**：多轮答案稳定性检测

### 1.2 目标

将 BudgetForcing 的**全部算法逻辑**迁移到 DEER 的 `CoTController` 接口中，在 vLLM 在线服务中实现该特性。

**核心原则**：覆盖所有算法实现，复用集成 vLLM 的接口。

这意味着：
- DEER 原有的算法逻辑（`_calculate_average_max_prob_from_logprobs`、`DeerConfig` 中的 DEER 特有字段、`_build_deer_config` 方法）**全部被 BudgetForcing 算法替代**
- DEER 的 vLLM 集成接口（`CoTController.handle_request()` 签名、`RequestStateContext` 作为状态间上下文、`AsyncReasoningOrchestrator` 编排器、`_build_managed_generator` 生产者-消费者模式、`result_queue` 输出通道）**全部保留**
- 状态机结构（4 状态）保留，但每个状态的内部逻辑**完全重写**为 BudgetForcing 算法

**设计起点**：当前 `deer/` 模块的代码状态（`deer/context.py`, `deer/state.py`, `deer/cot_controller.py`），以及 `think_budget` 项目的算法实现。

**设计范围**：
- 迁移 BudgetForcing 的全部算法逻辑（置信度计算、预算优先级链、早停决策、安全门、软截断、Clean Context、难度估计、历史预算、任务类型分类、DEER Trial）
- 保留 DEER 的 vLLM 集成接口（`CoTController.handle_request()` 签名、生产者-消费者模式、流式/非流式响应格式化）

**不涉及**：
- 评测/评分逻辑（`naie/scoring/`, `naie/evaluation/`）
- API 服务器（`naie/api_server.py`）
- 入口脚本（`run_*.py`）

### 1.3 设计约束

- 必须保持与 vLLM 的 `engine_client.generate()` 异步接口兼容
- 必须保持 `RequestStateContext` 作为状态间传递的上下文
- 必须保持 `CoTController.handle_request()` 的入口签名不变
- 置信度计算必须使用 vLLM 的 `SampleLogprobs` 格式（而非 think_budget 的 API 返回格式）
- 所有状态通过 `context.stream_output()` 将推理结果放入 `result_queue`，由 `_build_managed_generator` 统一 yield

---

## 2. 设计逻辑

### 2.1 概念空间

| 新概念 | 定义 | 由哪些已有概念构成 |
|--------|------|-------------------|
| **BudgetConfig** | 预算优先级链的最终输出参数集，**完全替代 DeerConfig** | 固定预算 → 任务类型 → 难度 → 历史 → 默认 |
| **ValueMinConfidence** | 过滤结构 token 后 Bottom-K Value token 的置信度 | is_structural_token + extract_value_logprobs + bottom-k mean |
| **DecisionScore** | 综合决策评分（概率域 [0,1]） | ValueMinConfidence / 双通道 / 完整链 AND 门 |
| **SafetyGate** | 超时回退保护 | 时间门控 + answer_list 最佳选择 |
| **SoftTruncation** | 思考被截断时在自然断点闭合标签 | 自然断点检测 + think tag 闭合 |
| **BudgetPriorityChain** | 预算参数的多级优先级计算 | BUDGET_ENABLED → FIXED_BUDGET_TOKEN → fixed_budget_tokens → 任务类型 → 难度 → 历史 → fixed_* 子参数 |
| **CleanContext** | 继续推理时丢弃诱导语，不累积试探答案 | make_induced_prompt + apply_clean_context |
| **ThinkTagPaired** | 判断 thinking 和 /thinking 标签是否配对 | is_think_tag_paired + reopen_think_tag |

### 2.2 架构概览

```
CoTController.handle_request()
  │
  ├── 1. _build_budget_config()          ← 新增：预算优先级链，完全替代 _build_deer_config()
  │     从 request 参数 + 任务类型 + 难度 + 历史 计算最终预算参数
  │     输出 BudgetConfig（替代 DeerConfig）
  │
  ├── 2. 创建 RequestStateContext         ← 携带 BudgetConfig（不再使用 DeerConfig）
  │
  ├── 3. AsyncReasoningOrchestrator.run() ← 状态机主循环（完全重写）
  │     │
  │     ├── _InitialThinkState            ← 重写：使用 check_length，软截断，安全门
  │     ├── _IterativeThinkState          ← 重写：think_stride + min_think_tokens 门控
  │     │                                      + reopen_think_tag + Clean Context
  │     │                                      + 动态 budget 调整
  │     ├── _ConfidenceCheckState         ← 重写：ValueMinConfidence / 双通道 / 完整链
  │     │     └── compute_decision_score()
  │     │           ├── extract_value_logprobs()
  │     │           ├── cal_value_min_confidence()
  │     │           ├── cal_reasoning_zone_confidence()
  │     │           └── make_accept_decision()
  │     └── _AnswerState                  ← 重写：answer_list 最佳选择 + 软截断 + 安全门
  │
  └── 4. 格式化响应（复用 DEER 的 stream/full generator 调用）
```

### 2.3 状态机改造

DEER 原有的 4 状态状态机结构保留，但每个状态的内部逻辑**完全重写**为 BudgetForcing 算法。

#### 2.3.1 `_InitialThinkState` — 首次长程思考

**原 DEER 逻辑**：使用 `initial_thought_limit` 作为 max_tokens，设置 `stop=[\" response\"]`。

**BudgetForcing 逻辑**：
- 使用 `BudgetConfig.check_length` 作为 max_tokens
- 支持 `stop_condition`：`'length'`（按 token 数截断）或 `'thinking_words'`（按停止词截断）
- 思考被截断时执行软截断（`truncate_thinking_at_natural_breakpoint`）
- 安全门检查
- 记录 `reasoning_logprobs` / `reasoning_tokens` 供双通道使用

**状态转换条件**：
- `stop_reason == \" response\"` → `_AnswerState`（思考自然结束）
- 否则 → `_IterativeThinkState`

#### 2.3.2 `_IterativeThinkState` — 迭代短程思考

**原 DEER 逻辑**：使用 `think_stride` 作为 max_tokens，检查 `trial_count` 上限。

**BudgetForcing 逻辑**：
- 检查 `trial_count >= num_thinking` → 达到迭代上限，直接回答
- 检查 `max_tokens_temp <= 0` → token 预算耗尽
- 检查 `max_api_calls > 0 and api_call_count >= max_api_calls` → API 调用上限
- 安全门检查
- **Think tag 配对逻辑**：
  - 如果 `is_think_tag_paired(prompt)` 且 `stop_reason == 'stop'`：
    - 如果 `actual_len < min_think_tokens`：`reopen_think_tag()` 强制继续思考
    - 否则：思考已完整，`induced_prompt = prompt`（无需诱导）
  - 如果 think tag 未配对且 `stop_reason == 'stop'` 且 `actual_len < min_think_tokens`：强制继续思考
  - 否则：`induced_prompt = make_induced_prompt(prompt)`
- 思考被截断时执行软截断
- 记录 `reasoning_logprobs` / `reasoning_tokens` 供双通道使用

**状态转换条件**：
- `trial_count >= num_thinking` → `_AnswerState`
- `stop_reason == \" response\"` → `_AnswerState`
- 否则 → `_ConfidenceCheckState`

#### 2.3.3 `_ConfidenceCheckState` — 预回答与置信度校验

**原 DEER 逻辑**：生成 trial answer，计算几何平均概率，与 `prob_th` 比较。

**BudgetForcing 逻辑**：
- 使用 `induced_prompt`（来自上一状态）生成 trial answer
- 使用 `check_answer_len` 作为 max_tokens，设置 `logprobs=1`
- 计算 `confidence = cal_confidence(ans_logprobs)` 和 `perplexity = cal_perplexity(ans_logprobs)`
- 安全门检查
- 调用 `compute_dual_channel_score()` 计算 `decision_score`（支持 4 种模式）：
  1. **完整链 AND 门**（`enable_full_chain_confidence`）：`min(推理区 Min-10%, 答案区 Bottom-K)`
  2. **双通道**（`enable_dual_channel`）：`min(推理区 Min-10%, 答案区 Bottom-K)`
  3. **单通道 P3.1**（`enable_value_min_confidence`）：答案区 Bottom-K Value token 均值
  4. **默认**：平均 exp(logprobs)
- 连续相同答案检测（`check_consecutive_same`）
- DEER trial 评估（`TrialAnswerEvaluator`）
- 调用 `make_accept_decision()` 做早停决策
- 将试探答案加入 `answer_list`

**状态转换条件**：
- `should_accept == True` → `_AnswerState`
- 否则 → `_IterativeThinkState`（继续思考）

#### 2.3.4 `_AnswerState` — 最终答案生成

**原 DEER 逻辑**：根据上一轮停止原因构造 prompt 后缀，使用剩余 max_model_len 生成。

**BudgetForcing 逻辑**：
- 从 `answer_list` 中选 `decision_score` 最高的答案（`max(answer_list, key=lambda x: x[5])`）
- 如果答案已完整（`stop_reason == 'stop'` 且 think tag 已配对），直接返回
- 否则使用 `_UNLIMITED_ANSWER_TOKENS` 生成完整答案
- 软截断：在自然断点处闭合思考标签
- 如果 `answer_list` 为空（安全门回退场景），关闭 think 标签返回已有 prompt

### 2.4 预算优先级链

在 `CoTController` 中新增 `_build_budget_config()` 方法，**完全替代**原有的 `_build_deer_config()` 方法。实现 BudgetForcing 的预算优先级链：

```
优先级 0: BUDGET_ENABLED = False → 关闭所有预算控制，自由生成
优先级 1: FIXED_BUDGET_TOKEN > 0 → 覆盖 fixed_budget_tokens
优先级 2: fixed_budget_tokens > 0 → 固定预算覆盖所有动态计算
优先级 3: task_type_override 非空 → 强制使用指定任务类型预算
优先级 4: enable_task_type_budget → 按分类结果设置预算
优先级 5: enable_difficulty_estimator → 14 维特征融合 → 分段预算
优先级 6: enable_history_budget → 冷/暖/热启动模式
优先级 7: CONFIG 默认值
优先级 8: fixed_* 子参数覆盖（fixed_num_thinking, fixed_think_stride 等）
优先级 9: CONFIDENCE_THRESHOLD 覆盖 prob_th（如果未被 fixed_prob_th 覆盖）
         PERCENTILE_STEP 覆盖 history_stride_percentile
```

输出为 `BudgetConfig` 数据类，包含 BudgetForcing 的全部参数：

```python
@dataclass
class BudgetConfig:
    \"\"\"预算优先级链的最终输出参数集 — 完全替代 DeerConfig\"\"\"
    # 核心循环参数
    check_length: int = 200
    think_stride: int = 50
    num_thinking: int = 3
    min_think_tokens: int = 0
    check_answer_len: int = 20
    prob_th: float = 0.95
    consecutive_same_stop: int = 999

    # P3.1 Value Min Confidence
    enable_value_min_confidence: bool = False
    value_min_bottom_k: int = 3

    # P3.3 Clean Context
    enable_clean_context: bool = True

    # 安全门
    enable_safety_gate: bool = True
    baseline_time_limit: float = 35.0

    # 软截断
    enable_soft_truncation: bool = True

    # 双通道
    enable_dual_channel: bool = False
    reasoning_min_percentile: float = 0.1

    # 完整链
    enable_full_chain_confidence: bool = False
    full_chain_min_percentile: float = 0.1

    # 任务类型
    enable_task_type_budget: bool = True
    default_task_type: str = 'general'
    custom_task_types_path: Optional[str] = None
    task_type_override: str = ''

    # 难度估计
    enable_difficulty_estimator: bool = False
    difficulty_check_length_base: int = 150
    difficulty_check_length_max: int = 4000
    difficulty_min_think_base: int = 100
    difficulty_min_think_max: int = 4000
    difficulty_stride_base: int = 50
    difficulty_stride_max: int = 600
    difficulty_num_thinking_base: int = 3
    difficulty_num_thinking_max: int = 15
    difficulty_answer_len_base: int = 500
    difficulty_answer_len_max: int = 4000

    # 历史轨迹预算
    enable_history_budget: bool = True
    history_budget_multiplier: float = 1.2
    history_low_success_boost: float = 1.3
    history_stride_percentile: float = 0.75
    new_task_unlimited: bool = True
    auto_cluster_threshold: int = 5
    history_warmup_count: int = 3
    history_warmup_strategy: str = 'moderate'

    # 人工可控超参
    fixed_budget_tokens: int = 0
    fixed_num_thinking: int = 0
    fixed_think_stride: int = 0
    fixed_check_length: int = 0
    fixed_prob_th: float = 0.0
    fixed_min_think_tokens: int = 0

    # 统一预算配置
    BUDGET_ENABLED: bool = True
    FIXED_BUDGET_TOKEN: int = 0
    CONFIDENCE_THRESHOLD: float = 0.95
    PERCENTILE_STEP: float = 0.75

    # DEER Trial
    enable_deer_trial: bool = False
    deer_trial_stable_rounds: int = 2
    deer_trial_answer_len: int = 30
    deer_confidence_boost: float = 0.05

    # 历史统计（外部传入）
    history_avg_tokens: float = 0.0
    history_failure_rate: float = 0.0
    history_category_avg_tokens: float = 0.0
    history_category_failure_rate: float = 0.0
    history_count: int = 0

    # 路由参数
    max_api_calls: int = 0
    reference: str = ''

    # 任务类型/场景信息（供下游记录）
    task_type: str = 'general'
    scene_type: str = 'general'
```

### 2.5 置信度计算模块

新增 `deer/confidence.py`，封装 BudgetForcing 的置信度计算逻辑：

```
deer/confidence.py
├── is_structural_token()              ← 从 _struct_token_core.py 移植
├── set_task_key_names()               ← 任务键名注入
├── extract_value_logprobs()           ← 过滤结构 token
├── cal_value_min_confidence()         ← P3.1 核心
├── cal_reasoning_zone_confidence()    ← 推理区 Min-Percentile
├── compute_dual_channel_score()       ← 统一决策评分入口（4 种模式）
├── make_accept_decision()             ← 早停决策
├── cal_confidence()                   ← 平均 exp 概率
├── cal_perplexity()                   ← 困惑度
├── cal_full_chain_confidence()        ← 完整链 AND 门
├── extract_answer_for_sc()            ← 答案提取（连续相同检测用）
├── check_consecutive_same()           ← 连续相同答案检测
└── _extract_logprobs_from_vllm()      ← vLLM SampleLogprobs 适配
```

**vLLM 适配要点**：
- think_budget 的 `logprobs` 是 `list[float]`（纯数值列表）
- vLLM 的 `logprobs` 是 `SampleLogprobs`（`list[dict[int, Logprob]]`）
- 需要在 `deer/confidence.py` 中增加 `_extract_logprobs_from_vllm()` 适配函数

### 2.6 安全门

新增 `deer/safety_gate.py`，从 `safety_gate.py` 移植：
- `safety_gate_check()`：软门 + 硬门两级超时检测
- `build_safety_fallback()`：从 `answer_list` 选最佳答案回退

### 2.7 软截断

新增 `deer/truncation.py`，从 `budget_forcing.py` 移植：
- `truncate_thinking_at_natural_breakpoint()`：在自然断点处闭合思考标签
- `_NATURAL_BREAK_CHARS`：自然断点字符集

### 2.8 上下文控制

新增 `deer/context_control.py`，从 `context_control.py` 移植：
- `make_induced_prompt()`：生成诱导提示，闭合 think tag 引导模型输出答案
- `apply_clean_context()`：继续推理时丢弃诱导语，不累积试探答案

### 2.9 标签工具

新增 `deer/tag_utils.py`，从 `confidence.py` 移植标签相关工具函数：
- `is_think_tag_paired()`：判断 thinking 与 /thinking 是否配对
- `get_think_closing_tag()`：返回标准闭标签
- `reopen_think_tag()`：移除闭标签重新打开思考

### 2.10 预算辅助模块（按需移植）

以下模块在 `_build_budget_config()` 中被调用，需要按需移植：

| 模块 | 来源 | 用途 |
|------|------|------|
| `deer/scene_classifier.py` | `naie/optimizations/scene_classifier.py` | 场景分类 |
| `deer/task_type_budget.py` | `naie/optimizations/task_type_budget.py` | 任务类型预算 |
| `deer/difficulty_estimator.py` | `naie/optimizations/difficulty_estimator.py` | 难度估计 |
| `deer/history_budget.py` | `naie/optimizations/history_budget.py` | 历史轨迹预算 |
| `deer/complexity_analyzer.py` | `naie/optimizations/complexity_analyzer.py` | 复杂度分析 |
| `deer/trial_evaluator.py` | `naie/optimizations/trial_evaluator.py` | DEER Trial 评估 |

---

## 3. 核心数据结构

### 3.1 `BudgetConfig`（新增，完全替代 `DeerConfig`）

`DeerConfig` 被废弃，`BudgetConfig` 作为唯一的配置数据类。详见 2.4 节。

### 3.2 `RequestStateContext` 重写

`RequestStateContext` 保留其作为状态间上下文传递的角色，但字段完全重写：

```python
@dataclass
class RequestStateContext:
    \"\"\"封装单个请求的完整上下文，在状态之间传递。\"\"\"
    # 外部依赖（保留）
    request: ChatCompletionRequest
    tokenizer: TokenizerLike
    serving_chat: OpenAIServingChat

    # 配置（BudgetConfig 替代 DeerConfig）
    config: BudgetConfig

    # 动态状态（保留）
    request_id: str
    engine_prompt: TokensInput
    result_queue: asyncio.Queue

    # 新增 BudgetForcing 动态状态
    answer_list: list = field(default_factory=list)       # 所有试探答案记录
    prev_answer_text: str = None                          # 上一轮答案文本
    consecutive_same_count: int = 0                       # 连续相同计数
    reasoning_logprobs: list = None                       # 最近一轮推理 logprobs
    reasoning_tokens: list = None                         # 最近一轮推理 tokens
    budget_start_time: float = field(default_factory=time.time)  # 预算开始时间
    api_call_count: int = 0                               # API 调用计数
    thinking_rounds: int = 0                              # 思考轮次计数
    actual_len: int = 0                                   # 实际 token 消耗
    output_len: int = 0                                   # 输出 token 计数
    max_tokens_temp: int = 0                              # 剩余 token 预算
    last_stop_reason: Optional[str] = None                # 上一轮停止原因
    induced_prompt: Optional[str] = None                  # 当前诱导提示
    complexity_info: Optional[dict] = None                # 复杂度信息
    trial_evaluator: Optional[object] = None              # DEER Trial 评估器

    @property
    def engine_client(self):
        return self.serving_chat.engine_client

    async def stream_output(self, result):
        await self.result_queue.put(result)

    def update_prompts(self, token_ids: list[int], res: RequestOutput):
        self.engine_prompt['prompt_token_ids'].extend(token_ids)
```

### 3.3 `DeerConfig` 废弃

`DeerConfig` 不再使用。其字段映射关系：

| DeerConfig 字段 | BudgetConfig 对应字段 | 说明 |
|-----------------|----------------------|------|
| `is_code` | 无直接对应 | BudgetForcing 通过任务类型分类自动判断 |
| `think_ratio` | 无 | BudgetForcing 使用固定预算参数 |
| `token_budget` | `fixed_budget_tokens` / `check_length * num_thinking` | 预算由优先级链计算 |
| `trial_answers` | `num_thinking` | 最大迭代次数 |
| `think_stride` | `think_stride` | 字段名相同 |
| `initial_thought_limit` | `check_length` | 首次思考长度 |
| `trial_answer_length` | `check_answer_len` | trial answer 最大长度 |
| `prob_th` | `prob_th` | 字段名相同 |
| `rep_exit` | 无 | BudgetForcing 使用更精细的早停决策 |

---

## 4. 接口定义

### 4.1 新增模块

#### `deer/confidence.py` — 置信度计算

```python
def is_structural_token(token: str) -> bool
    \"\"\"判断 token 是否为结构 token（不参与置信度计算）。\"\"\"

def set_task_key_names(key_names: set) -> None
    \"\"\"注入任务特有的 JSON 键名集合。\"\"\"

def extract_value_logprobs(logprobs: list, tokens: list) -> list[float]
    \"\"\"过滤结构 token logprob，只保留 Value token 的 logprob。\"\"\"

def cal_value_min_confidence(logprobs: list, tokens: list, bottom_k: int = 3) -> float
    \"\"\"Value 最低 K 个 token logprob 均值 — P3.1 核心。\"\"\"

def cal_reasoning_zone_confidence(logprobs: list, tokens: list, min_percentile: float = 0.1) -> float
    \"\"\"推理区置信度 — 取最差 min_percentile 的 Value Token logprob 均值。\"\"\"

def cal_confidence(logprobs: list) -> float
    \"\"\"平均 exp 概率 — 所有 token logprob 的 exp 均值。\"\"\"

def cal_perplexity(logprobs: list) -> float
    \"\"\"困惑度 — exp(平均负 logprob)。\"\"\"

def cal_full_chain_confidence(
    chain_logprobs, chain_tokens, ans_logprobs, ans_tokens,
    *, chain_min_percentile: float = 0.1, bottom_k: int = 3,
) -> tuple[float, float, float]:
    \"\"\"完整思维链+答案置信度 — 思维链Min-%(宽容) + 答案Bottom-K(零容忍), AND门取min。\"\"\"

def compute_dual_channel_score(
    reasoning_logprobs, reasoning_tokens,
    ans_logprobs, ans_tokens,
    confidence: float, perplexity: float,
    *,
    enable_full_chain_confidence: bool = False,
    full_chain_min_percentile: float = 0.1,
    enable_dual_channel: bool = False,
    reasoning_min_percentile: float = 0.1,
    enable_value_min_confidence: bool = False,
    value_min_bottom_k: int = 3,
) -> tuple[float, float, float, float]:
    \"\"\"统一决策评分入口。返回 (decision_score, value_min_raw, reasoning_score_raw, action_score_raw)。\"\"\"

def make_accept_decision(
    decision_score: float, prob_th: float,
    actual_len: int, min_think_tokens: int,
    is_paired: bool,
    consecutive_same_count: int, consecutive_same_stop: int,
    enable_value_min_confidence: bool, decision_score_raw: float,
    enable_full_chain_confidence: bool = False,
    reasoning_score_raw: float = 0.0,
    action_score_raw: float = 0.0,
    enable_dual_channel: bool = False,
    is_final: bool = False,
) -> tuple[bool, str]:
    \"\"\"早停决策。返回 (should_accept, accept_reason)。\"\"\"

def extract_answer_for_sc(content: str, close_tag: str = '') -> str
    \"\"\"从内容中提取用于 Self-Consistency 的答案片段。\"\"\"

def check_consecutive_same(current_answer_text, prev_answer_text,
                           consecutive_same_count, consecutive_same_stop=999) -> tuple
    \"\"\"连续相同答案检测。\"\"\"

def _extract_logprobs_from_vllm(vllm_logprobs: SampleLogprobs) -> tuple[list[float], list[str]]
    \"\"\"将 vLLM 的 SampleLogprobs 格式转换为 (logprobs_list, tokens_list)。\"\"\"
```

#### `deer/safety_gate.py` — 安全门

```python
def safety_gate_check(elapsed: float, baseline_time_limit: float, enable: bool = True) -> bool
    \"\"\"安全门检查。返回 True 表示需要触发回退。\"\"\"

def build_safety_fallback(
    current_prompt: str, answer_list: list,
    output_len: int, actual_len: int,
    api_call_count: int, thinking_rounds: int,
    soft_truncation: bool = False,
) -> dict:
    \"\"\"构建安全门回退结果。\"\"\"
```

#### `deer/truncation.py` — 软截断

```python
def truncate_thinking_at_natural_breakpoint(content: str) -> str
    \"\"\"在推理过程的自然断点处截断推理，绝不截断答案。\"\"\"
```

#### `deer/context_control.py` — 上下文控制

```python
def make_induced_prompt(base_prompt: str) -> str
    \"\"\"生成诱导提示 — 闭合 think tag 引导模型输出答案。\"\"\"

def apply_clean_context(enable_clean_context: bool, prompt: str, induced_prompt: str,
                        o: str, output_len: int, token_num: int) -> tuple[str, int]
    \"\"\"继续推理时丢弃诱导语，不累积试探答案。\"\"\"
```

#### `deer/tag_utils.py` — 标签工具

```python
def is_think_tag_paired(text: str) -> bool
    \"\"\"判断 thinking 与 /thinking 是否配对出现。\"\"\"

def get_think_closing_tag(text: str) -> str
    \"\"\"返回标准的 think 闭标签。\"\"\"

def reopen_think_tag(text: str) -> str
    \"\"\"从文本中移除 /thinking 闭标签及其之后的内容，使思考标签重新打开。\"\"\"
```

### 4.2 修改的模块

#### `deer/context.py` — 重写

- `DeerConfig` → 废弃，被 `BudgetConfig` 替代
- `RequestStateContext` → 字段完全重写（见 3.2 节）

#### `deer/state.py` — 完全重写

- `_calculate_average_max_prob_from_logprobs()` → 废弃，被 `compute_dual_channel_score()` 替代
- `_InitialThinkState.handle()` → 完全重写为 BudgetForcing 首次思考逻辑
- `_IterativeThinkState.handle()` → 完全重写为 BudgetForcing 迭代思考逻辑（含 think tag 配对、min_think_tokens 门控、动态 budget 调整）
- `_ConfidenceCheckState.handle()` → 完全重写为 BudgetForcing 置信度校验逻辑
- `_AnswerState.handle()` → 完全重写为 BudgetForcing 最终答案生成逻辑（含 answer_list 最佳选择）
- `_BaseState.consume_output()` → 保留，但 `_ConfidenceCheckState` 使用自己的 `consume_output`（收集 logprobs）

#### `deer/cot_controller.py` — 重写

- `_build_deer_config()` → 废弃，被 `_build_budget_config()` 替代
- `handle_request()` → 使用 `BudgetConfig` 替代 `DeerConfig`

### 4.3 接口关系总结

```
用户请求 (HTTP JSON)
  │  enable_deer=True, deer_* 参数
  ▼
CoTController.handle_request()
  │
  ├── _build_budget_config()         ← 预算优先级链 → BudgetConfig（替代 DeerConfig）
  ├── 创建 RequestStateContext       ← 携带 BudgetConfig
  ├── AsyncReasoningOrchestrator.run()
  │     └── 状态机循环（完全重写）
  │           ├── deer/confidence.py      ← 置信度计算
  │           ├── deer/safety_gate.py     ← 超时回退
  │           ├── deer/truncation.py      ← 软截断
  │           ├── deer/context_control.py ← 诱导提示 + Clean Context
  │           └── deer/tag_utils.py       ← 标签配对判断
  └── 格式化响应 (stream/full)       ← 复用 DEER 的调用方式
```

---

## 5. 迁移步骤

### 阶段一：基础工具模块

1. **创建 `deer/tag_utils.py`**
   - 从 `naie/models/confidence.py` 移植 `is_think_tag_paired()`, `get_think_closing_tag()`, `reopen_think_tag()`
   - 定义 `_THINK_OPEN_TAG = ' thinking'`, `_THINK_CLOSE_TAG = ' response'`

2. **创建 `deer/truncation.py`**
   - 从 `naie/models/budget_forcing.py` 移植 `_truncate_thinking_at_natural_breakpoint()` 和 `_NATURAL_BREAK_CHARS`

3. **创建 `deer/context_control.py`**
   - 从 `naie/optimizations/context_control.py` 移植 `make_induced_prompt()` 和 `apply_clean_context()`

### 阶段二：置信度计算

4. **创建 `deer/confidence.py`**
   - 从 `naie/optimizations/_struct_token_core.py` 移植 `is_structural_token()`, `set_task_key_names()`, `extract_value_logprobs()`
   - 从 `naie/optimizations/token_filter.py` 移植 `cal_value_min_confidence()`, `cal_reasoning_zone_confidence()`
   - 从 `naie/models/confidence.py` 移植 `cal_confidence()`, `cal_perplexity()`, `cal_full_chain_confidence()`
   - 从 `naie/optimizations/confidence_gates.py` 移植 `compute_dual_channel_score()`
   - 从 `naie/optimizations/early_stopping.py` 移植 `extract_answer_for_sc()`, `check_consecutive_same()`, `make_accept_decision()`
   - 增加 `_extract_logprobs_from_vllm()` 适配 vLLM 的 `SampleLogprobs` 格式

### 阶段三：安全门

5. **创建 `deer/safety_gate.py`**
   - 从 `naie/optimizations/safety_gate.py` 移植 `safety_gate_check()` 和 `build_safety_fallback()`

### 阶段四：核心数据结构

6. **重写 `deer/context.py`**
   - 废弃 `DeerConfig`，新增 `BudgetConfig`（包含 BudgetForcing 全部参数）
   - 重写 `RequestStateContext`（字段完全重写为 BudgetForcing 所需状态）

### 阶段五：状态机重写

7. **完全重写 `deer/state.py`**
   - 删除 `_calculate_average_max_prob_from_logprobs()`
   - 重写 `_InitialThinkState.handle()`：使用 `check_length`，软截断，安全门
   - 重写 `_IterativeThinkState.handle()`：think tag 配对逻辑、min_think_tokens 门控、reopen_think_tag、Clean Context、动态 budget 调整
   - 重写 `_ConfidenceCheckState.handle()`：使用 `compute_dual_channel_score()` + `make_accept_decision()` + answer_list 记录
   - 重写 `_AnswerState.handle()`：answer_list 最佳选择 + 软截断 + 安全门

### 阶段六：入口重写

8. **重写 `deer/cot_controller.py`**
   - 删除 `_build_deer_config()`，新增 `_build_budget_config()`（实现预算优先级链）
   - `handle_request()` 中使用 `BudgetConfig` 替代 `DeerConfig`

### 阶段七：预算辅助模块（按需）

9. **按需移植预算辅助模块**
   - `deer/scene_classifier.py` — 场景分类
   - `deer/task_type_budget.py` — 任务类型预算
   - `deer/difficulty_estimator.py` — 难度估计
   - `deer/history_budget.py` — 历史轨迹预算
   - `deer/complexity_analyzer.py` — 复杂度分析
   - `deer/trial_evaluator.py` — DEER Trial 评估

### 阶段八：集成测试与调优

10. **编写单元测试**
    - 测试 `is_structural_token()` 的各类 token 判断
    - 测试 `cal_value_min_confidence()` 的数值计算
    - 测试 `make_accept_decision()` 的各类决策路径
    - 测试 `safety_gate_check()` 的超时逻辑
    - 测试 `truncate_thinking_at_natural_breakpoint()` 的截断逻辑

11. **集成测试**
    - 在 vLLM 环境中测试完整推理流程
    - 验证流式和非流式响应格式    - 验证预算优先级链各分支
---

## 6. 一致性校验

### 6.1 概念一致性

- **结构 token**：全文统一使用 `is_structural_token()` 判断，无重复实现
- **决策评分**：统一通过 `compute_dual_channel_score()` 入口，支持 4 种模式
- **早停决策**：统一通过 `make_accept_decision()`，无分散的判断逻辑
- **标签操作**：统一通过 `deer/tag_utils.py`，无分散的字符串操作
- **配置**：统一通过 `BudgetConfig`，无分散的参数传递

### 6.2 状态完备性

| 状态 | 输入事件 | 处理策略 |
|------|---------|---------|
| `_InitialThinkState` | `stop_reason == \" response\"` | → `_AnswerState` |
| `_InitialThinkState` | 其他停止原因 | → `_IterativeThinkState` |
| `_InitialThinkState` | 安全门触发 | → 回退结果，结束 |
| `_IterativeThinkState` | `trial_count >= num_thinking` | → `_AnswerState` |
| `_IterativeThinkState` | `stop_reason == \" response\"` | → `_AnswerState` |
| `_IterativeThinkState` | 其他停止原因 | → `_ConfidenceCheckState` |
| `_IterativeThinkState` | 安全门触发 | → 回退结果，结束 |
| `_IterativeThinkState` | think tag 已配对 + stop + 思考不足 | reopen_think_tag 继续思考 |
| `_IterativeThinkState` | think tag 未配对 + stop + 思考不足 | 强制继续思考 |
| `_ConfidenceCheckState` | `should_accept == True` | → `_AnswerState` |
| `_ConfidenceCheckState` | `should_accept == False` | → `_IterativeThinkState` |
| `_ConfidenceCheckState` | 安全门触发 | → 回退结果，结束 |
| `_AnswerState` | answer_list 非空 | 选最佳答案，生成完整答案 |
| `_AnswerState` | answer_list 为空 | 关闭 think 标签返回已有 prompt |
| `_AnswerState` | 正常完成 | → None（结束） |

### 6.3 接口完备性

| 功能 | 接口 | 来源 |
|------|------|------|
| 预算计算 | `_build_budget_config()` | `budget_forcing.py` 预算优先级链 |
| 置信度计算 | `compute_dual_channel_score()` | `confidence_gates.py` |
| 早停决策 | `make_accept_decision()` | `early_stopping.py` |
| 安全门 | `safety_gate_check()` + `build_safety_fallback()` | `safety_gate.py` |
| 软截断 | `truncate_thinking_at_natural_breakpoint()` | `budget_forcing.py` |
| 诱导提示 | `make_induced_prompt()` | `context_control.py` |
| Clean Context | `apply_clean_context()` | `context_control.py` |
| 标签操作 | `is_think_tag_paired()` / `reopen_think_tag()` | `confidence.py` |
| 连续相同检测 | `check_consecutive_same()` | `early_stopping.py` |
| 答案提取 | `extract_answer_for_sc()` | `early_stopping.py` |
| 结构 token 判断 | `is_structural_token()` | `_struct_token_core.py` |
| Value Min Confidence | `cal_value_min_confidence()` | `token_filter.py` |
| 推理区置信度 | `cal_reasoning_zone_confidence()` | `token_filter.py` |
| 完整链置信度 | `cal_full_chain_confidence()` | `confidence.py` |
| 平均置信度 | `cal_confidence()` | `confidence.py` |
| 困惑度 | `cal_perplexity()` | `confidence.py`

## 7. 变更历史

| 日期 | 变更内容 | 原因 |
|------|---------|------|
| 2026-07-09 | 初始版本 | BudgetForcing 算法迁移设计 |
| 2026-07-10 | fix:\n1. **`DeerConfig` should NOT be enhanced** — it should be replaced by `BudgetConfig`. The principle is \"覆盖所有算法实现，复用集成vLLM的接口\". The DEER config fields (`initial_thought_limit`, `token_budget`, `trial_answers`, `rep_exit`, `trial_answer_length`) are DEER-specific and should be replaced by BudgetForcing's parameters.\n2. **`_build_deer_config()` should be replaced by `_build_budget_config()`** — not \"enhanced\" alongside.\n3. **The state machine should be fully rewritten** — not \"enhanced\". The BudgetForcing algorithm has fundamentally different loop logic (think_tag_paired check, min_think_tokens gate, reopen_think_tag, induced_prompt, clean_context, answer_list tracking, dynamic budget adjustment).\n4. **Missing many BudgetForcing parameters** in the design: `stop_condition`, `max_api_calls`, `reference`, `enable_deer_trial`, `enable_task_type_budget`, `enable_difficulty_estimator`, `enable_history_budget`, all the difficulty/history sub-params, `BUDGET_ENABLED`, `FIXED_BUDGET_TOKEN`, `CONFIDENCE_THRESHOLD`, `PERCENTILE_STEP`.\n5. **Missing modules**: `complexity_analyzer`, `trial_evaluator`, `scene_classifier`, `task_type_budget`, `history_budget`, `difficulty_estimator`.\n6. **The `_InitialThinkState` logic is wrong** — BudgetForcing doesn't have a separate \"initial think\" with different parameters. It uses `check_length` for the first call, then enters the loop. The initial call and iterative calls use the same `_acall` function.\n7. **The `_AnswerState` logic is wrong** — BudgetForcing selects the best answer from `answer_list` by `decision_score`, not from `last_chunk`.\n8. **Missing the `_truncate_thinking_at_natural_breakpoint` import** and `_NATURAL_BREAK_CHARS`.\n9. **The `_ConfidenceCheckState` should also handle `is_think_tag_paired` logic** — when think tags are already paired, `induced_prompt = prompt` (no induction needed).\n10. **Missing the dynamic budget adjustment** in the iterative loop (lines 1168-1178 of budget_forcing.py). | 澄清BudgetForcing 算法迁移设计的原则是覆盖所有算法实现，但复用集成vllm的接口 |