"""增强版请求上下文 — 扩展 deer_think_budget 的 ThinkBudgetConfig/RequestStateContext，
新增 budget 决策参数和运行时状态。

核心设计:
  - BudgetConfig 扩展 ThinkBudgetConfig，增加 budget 决策链所需的全部超参
  - BudgetRequestContext 扩展 RequestStateContext，增加 budget 运行时状态
  - 与 deer 状态机完全兼容：状态类通过 context.config 读取参数

从 think_budget/context.py 迁移，调整导入路径。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Dict, TYPE_CHECKING
from dataclasses import dataclass, field

from think_budget_vllm_adapter.deer_think_budget.context import ThinkBudgetConfig, RequestStateContext
from think_budget_vllm_adapter.telemetry import TimingCollector


# Trial answers are hidden probes used only for confidence estimation.  Keep
# their work bounded independently of task/history budgets; the final answer
# has its own unrestricted request in ``_AnswerState``.
TRIAL_ANSWER_LENGTH = 50

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


@dataclass
class BudgetConfig(ThinkBudgetConfig):
    """扩展 ThinkBudgetConfig — 增加 budget 决策链所需的全部超参。

    这些参数由 budget_controller.py 中的决策链计算得出，
    然后通过 BudgetRequestContext.config 传递给 deer 状态类。

    状态类（如 _IterativeThinkState）通过 context.config 读取参数，
    因此只需确保 BudgetConfig 包含正确的参数值即可。

    继承关系:
      ThinkBudgetConfig (DEER 参数 + Think Budget 基础参数)
        → BudgetConfig (+ budget 决策链参数 + 开关)
    """

    # ─── Budget 决策参数（由决策链计算后填入）───
    # 初始长度、轮数、步长、试探答案长度和置信度直接写入父类的
    # initial_thought_limit、trial_answers、think_stride、
    # trial_answer_length、prob_th。不要保留同义镜像字段，否则会出现
    # “观测值已更新、DEER 实际读取值未更新”的双数据源问题。
    min_think_tokens: int = 100     # 最低思考token门限

    # ─── Budget 开关 ───
    enable_consecutive_same_stop: bool = False  # 连续相同答案早停
    enable_difficulty_estimator: bool = False  # 难度估计器
    enable_task_type_budget: bool = True  # 任务类型预算
    enable_history_budget: bool = True    # 历史轨迹预算
    enable_deer_trial: bool = False       # DEER trial评估

    # ─── DEER trial 参数 ───
    deer_trial_stable_rounds: int = 2

    # ─── 任务类型信息（由决策链填入）───
    task_type: str = 'general'
    scene_type: str = 'general'
    difficulty_info: Optional[Dict] = None
    history_info: Optional[Dict] = None
    complexity_info: Optional[Dict] = None

    # ─── 人工可控超参 ───
    fixed_budget_tokens: int = 0
    fixed_num_thinking: int = 0
    fixed_think_stride: int = 0
    fixed_check_length: int = 0
    fixed_prob_th: float = 0.0
    fixed_min_think_tokens: int = 0
    BUDGET_ENABLED: bool = True

    # ─── 服务端执行边界（不允许请求覆盖） ───
    # 0 means no separate API-call cap; this is server configuration only.
    # trial_answers/token_budget remain the algorithm's dynamic controls.
    max_api_calls: int = 0
    final_answer_max_tokens: int = 2048
    emit_phase_metrics: bool = True
    verbose_stage_logging: bool = False

    # ─── 历史/难度模块的外置配置 ───
    history_budget_multiplier: float = 1.2
    history_low_success_boost: float = 1.3
    history_stride_percentile: float = 0.75
    history_warmup_count: int = 3
    history_warmup_strategy: str = 'moderate'
    custom_task_types_path: Optional[str] = None
    default_task_type: str = 'general'


@dataclass
class BudgetRequestContext(RequestStateContext):
    """增强版请求上下文 — 扩展 RequestStateContext，新增 budget 运行时状态。

    核心设计原则:
      - 与 RequestStateContext 接口兼容（所有字段 + 方法均保留）
      - 通过 config: BudgetConfig 将 budget 决策参数传递给 deer 状态类
      - 新增的 budget 运行时状态用于增强版置信度检查状态
      - 使用继承扩展（dataclass 字段有默认值，可安全继承）

    从 think_budget/context.py 迁移，调整导入路径。
    """
    # ─── Budget 运行时状态 ───
    # ─── Trial Evaluator ───
    trial_evaluator: Any = None   # TrialAnswerEvaluator 实例（延迟初始化）

    # ─── 运行时可观测性 / 会话元数据 ───
    model_name: Optional[str] = None
    session_id: Optional[str] = None
    external_overrides: set[str] = field(default_factory=set)
    timing: TimingCollector = field(default_factory=TimingCollector)
    orchestration_error: Optional[str] = None

    # 注意: budget_start_time, answer_list, prev_answer_text, consecutive_same_count
    # 已在 RequestStateContext 中定义，无需重复声明

    def update_budget_params(self, budget_params: Dict[str, Any]):
        """用 budget 决策链的结果更新 config 参数。

        将决策链计算的参数映射到 BudgetConfig / ThinkBudgetConfig 的字段，
        使 deer 状态类通过 context.config 读取到正确的参数。
        """
        cfg = self.config

        def set_if_computed(field_name: str, value: Any) -> None:
            """Decision-chain output may never replace a request-supplied field."""
            if field_name not in self.external_overrides:
                setattr(cfg, field_name, value)

        # 映射 budget 参数到 DeerConfig 兼容字段
        if 'check_length' in budget_params:
            set_if_computed('initial_thought_limit', budget_params['check_length'])

        if 'think_stride' in budget_params:
            set_if_computed('think_stride', budget_params['think_stride'])

        if 'num_thinking' in budget_params:
            set_if_computed('trial_answers', budget_params['num_thinking'])

        if 'min_think_tokens' in budget_params:
            set_if_computed('min_think_tokens', budget_params['min_think_tokens'])

        if 'check_answer_len' in budget_params:
            # Task and history modules still expose ``check_answer_len`` for
            # compatibility/telemetry, but a trial must never become a hidden
            # long generation.  The production invariant is exactly 50.
            set_if_computed('trial_answer_length', TRIAL_ANSWER_LENGTH)

        if 'prob_th' in budget_params:
            set_if_computed('prob_th', budget_params['prob_th'])

        if 'token_budget' in budget_params:
            set_if_computed('token_budget', budget_params['token_budget'])

        # 任务类型信息
        if 'task_type' in budget_params:
            set_if_computed('task_type', budget_params['task_type'])
        if 'scene_type' in budget_params:
            set_if_computed('scene_type', budget_params['scene_type'])

        # 开关参数
        for key in ['enable_value_min_confidence',
                      'enable_dual_channel', 'enable_full_chain_confidence',
                      'enable_consecutive_same_stop',
                      'enable_safety_gate', 'enable_difficulty_estimator',
                      'enable_task_type_budget', 'enable_history_budget',
                      'enable_deer_trial',
                      'consecutive_same_stop', 'value_min_bottom_k',
                      'reasoning_min_percentile', 'full_chain_min_percentile',
                      'baseline_time_limit', 'deer_trial_stable_rounds',
                     'difficulty_info', 'history_info', 'complexity_info',
                     'fixed_budget_tokens', 'BUDGET_ENABLED']:
            if key in budget_params:
                setattr(cfg, key, budget_params[key])

    def init_trial_evaluator(self):
        """延迟初始化 TrialAnswerEvaluator。"""
        if self.config.enable_deer_trial and self.trial_evaluator is None:
            from think_budget_vllm_adapter.optimizations.trial_evaluator import TrialAnswerEvaluator
            self.trial_evaluator = TrialAnswerEvaluator({
                'enable_deer_trial': True,
                'deer_trial_stable_rounds': self.config.deer_trial_stable_rounds,
            })
            self.trial_evaluator.reset()
