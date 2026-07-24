"""添加到 vLLM 0.23.0 ``ChatCompletionRequest`` 的 Think Budget 字段。

对外只开放总开关与六个 ``fixed_*`` 人工固定输入。动态预算、历史/难度
策略、会话控制及旧版 ``tb_*`` 别名均为服务端配置。按本轮裁决保留 vLLM
原生 ``extra="allow"``，不在协议基类上收紧未知字段；控制器仍会显式拒绝
已知的旧 Think Budget 字段，避免混合版本部署悄悄恢复旧算法语义。
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
