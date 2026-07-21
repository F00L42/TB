"""添加到 vLLM 0.23.0 ``ChatCompletionRequest`` 的 Think Budget 字段。

对外只开放总开关与六个 ``fixed_*`` 人工固定输入。动态预算、历史/难度
策略、会话控制及旧版 ``tb_*`` 别名均为服务端配置，不能出现在 HTTP 请求。
vLLM 的 ``OpenAIBaseModel`` 默认允许未知字段，因此必须同时在
``ChatCompletionRequest`` 类中加入 ``model_config = ConfigDict(extra="forbid")``，
并把 ``ConfigDict`` 加入 pydantic 导入；否则被删除的字段仍可能静默进入。
"""

enable_think_budget: bool | None = Field(
    default=False,
    description="Think Budget 总开关；为 true 时进入 Think Budget 状态机。",
)
fixed_budget_tokens: int = Field(
    default=0, ge=0,
    description="固定总思考预算；0 表示继续使用服务端动态计算。",
)
fixed_num_thinking: int = Field(
    default=0, ge=0,
    description="固定思考轮数；0 表示继续使用服务端动态计算。",
)
fixed_think_stride: int = Field(
    default=0, ge=0,
    description="固定每轮思考步长；0 表示继续使用服务端动态计算。",
)
fixed_check_length: int = Field(
    default=0, ge=0,
    description="固定首次思考长度；0 表示继续使用服务端动态计算。",
)
fixed_prob_th: float = Field(
    default=0.0, ge=0.0, le=1.0,
    description="固定置信度阈值；0 表示继续使用服务端动态计算。",
)
fixed_min_think_tokens: int = Field(
    default=0, ge=0,
    description="固定最低思考 token 数；0 表示继续使用服务端动态计算。",
)
