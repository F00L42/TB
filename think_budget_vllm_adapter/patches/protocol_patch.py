"""
vLLM v0.20.2 — protocol.py 补丁说明 (Budget Controller 版)

文件: vllm/entrypoints/openai/chat_completion/protocol.py

在 ChatCompletionRequest 类的 extra-params 区段末尾
(repetition_detection 字段之后, # --8<-- [end:chat-completion-extra-params] 之前)
添加以下 Think Budget 请求字段。

注意: 如果已应用 deer_think_budget/patches/protocol_patch.py 中的字段,
则只需在此基础上添加 Budget 决策链专属字段 (budget_* 前缀)。
"""

# ─── Think Budget 基础参数 (与 deer_think_budget patches 一致) ───
enable_think_budget: bool | None = Field(
    default=False,
    description="ThinkBudget功能总开关 — 启用后使用Think Budget算法替代原生推理。"
)

tb_check_length: int | None = Field(
    default=None,
    description="ThinkBudget: 初始思考token数 (首次API调用请求的思考量)。"
)

tb_think_stride: int | None = Field(
    default=None,
    description="ThinkBudget: 每轮思考增量token数。"
)

tb_num_thinking: int | None = Field(
    default=None,
    description="ThinkBudget: 最大迭代轮次 (budget循环最多跑几轮)。"
)

tb_check_answer_len: int | None = Field(
    default=None,
    description="ThinkBudget: trial answer长度 (每轮induced后请求多少token生成答案)。"
)

tb_prob_th: float | None = Field(
    default=None,
    description="ThinkBudget: 置信度阈值 (decision_score>=此值且think充足时接受答案)。"
)

tb_min_think_tokens: int | None = Field(
    default=None,
    description="ThinkBudget: 最低思考token数 (actual_len不足则不提前接受, 软门控)。"
)

tb_is_code: bool | None = Field(
    default=None,
    description="ThinkBudget: prompt任务是否为code类型 (影响trial answer行为)。"
)

tb_enable_value_min_confidence: bool | None = Field(
    default=None,
    description="ThinkBudget: Value Min Confidence开关 — 取答案bottom-k token概率均值。"
)

tb_value_min_bottom_k: int | None = Field(
    default=None,
    description="ThinkBudget: 取最低K个token的logprob求均值。"
)

tb_enable_clean_context: bool | None = Field(
    default=None,
    description="ThinkBudget: Clean Context开关 — 继续思考时丢弃induced部分节省token。"
)

tb_enable_soft_truncation: bool | None = Field(
    default=None,
    description="ThinkBudget: Soft Truncation开关 — 置信度达标后在自然断点截断。"
)

tb_consecutive_same_stop: int | None = Field(
    default=None,
    description="ThinkBudget: 连续相同答案早停阈值 — 连续N轮答案一致则提前接受, 0=禁用。"
)

# ─── Budget 决策链专属参数 (新增) ───
budget_token_limit: int | None = Field(
    default=0,
    description="Budget决策链: 固定思考token数 (Step 0.5覆盖, >0时跳过任务类型/难度/历史决策)。"
)

budget_disabled: bool | None = Field(
    default=False,
    description="Budget决策链: 总开关禁用 (Step 0, 等价于 BUDGET_ENABLED=False)。"
)

budget_confidence_threshold: float | None = Field(
    default=None,
    description="Budget决策链: 覆盖置信度阈值 (Step Final, 优先级最高)。"
)

budget_task_type_override: str | None = Field(
    default=None,
    description="Budget决策链: 手动指定任务类型 (跳过自动判断, 如 'math', 'code', 'fault_diagnosis')。"
)
