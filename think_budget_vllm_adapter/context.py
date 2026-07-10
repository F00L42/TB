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
    check_length: int = 512         # 初始思考token数（覆盖 initial_thought_limit）
    num_thinking: int = 10          # 最大思考轮次（覆盖 trial_answers）
    think_stride_budget: int = 128  # budget决定的步长（覆盖 think_stride）
    min_think_tokens: int = 100     # 最低思考token门限
    check_answer_len: int = 50      # 试探答案长度（覆盖 trial_answer_length）
    prob_th_budget: float = 0.95    # budget决定的置信度阈值（覆盖 prob_th）

    # ─── Budget 开关 ───
    enable_confidence: bool = True       # 是否启用增强置信度评估
    enable_consecutive_same_stop: bool = False  # 连续相同答案早停
    enable_difficulty_estimator: bool = False  # 难度估计器
    enable_task_type_budget: bool = True  # 任务类型预算
    enable_history_budget: bool = True    # 历史轨迹预算
    enable_deer_trial: bool = False       # DEER trial评估

    # ─── DEER trial 参数 ───
    deer_trial_stable_rounds: int = 2
    deer_trial_answer_len: int = 30
    deer_confidence_boost: float = 0.05

    # ─── 任务类型信息（由决策链填入）───
    task_type: str = 'general'
    scene_type: str = 'general'
    difficulty_info: Optional[Dict] = None
    history_info: Optional[Dict] = None
    complexity_info: Optional[Dict] = None

    # ─── 人工可控超参 ───
    fixed_budget_tokens: int = 0
    BUDGET_ENABLED: bool = True


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
    reasoning_logprobs_flat: list = field(default_factory=list)   # 最近一轮推理的扁平logprobs
    reasoning_tokens_text: list = field(default_factory=list)     # 最近一轮推理的token文本

    # ─── Trial Evaluator ───
    trial_evaluator: Any = None   # TrialAnswerEvaluator 实例（延迟初始化）

    # 注意: budget_start_time, answer_list, prev_answer_text, consecutive_same_count
    # 已在 RequestStateContext 中定义，无需重复声明

    def update_budget_params(self, budget_params: Dict[str, Any]):
        """用 budget 决策链的结果更新 config 参数。

        将决策链计算的参数映射到 BudgetConfig / ThinkBudgetConfig 的字段，
        使 deer 状态类通过 context.config 读取到正确的参数。
        """
        cfg = self.config

        # 映射 budget 参数到 DeerConfig 兼容字段
        if 'check_length' in budget_params:
            cfg.initial_thought_limit = budget_params['check_length']
            cfg.check_length = budget_params['check_length']

        if 'think_stride' in budget_params:
            cfg.think_stride = budget_params['think_stride']
            cfg.think_stride_budget = budget_params['think_stride']

        if 'num_thinking' in budget_params:
            cfg.trial_answers = budget_params['num_thinking']
            cfg.num_thinking = budget_params['num_thinking']

        if 'min_think_tokens' in budget_params:
            cfg.min_think_tokens = budget_params['min_think_tokens']

        if 'check_answer_len' in budget_params:
            cfg.trial_answer_length = budget_params['check_answer_len']
            cfg.check_answer_len = budget_params['check_answer_len']

        if 'prob_th' in budget_params:
            cfg.prob_th = budget_params['prob_th']
            cfg.prob_th_budget = budget_params['prob_th']

        # 任务类型信息
        if 'task_type' in budget_params:
            cfg.task_type = budget_params['task_type']
        if 'scene_type' in budget_params:
            cfg.scene_type = budget_params['scene_type']

        # 开关参数
        for key in ['enable_confidence', 'enable_value_min_confidence',
                     'enable_dual_channel', 'enable_full_chain_confidence',
                     'enable_consecutive_same_stop', 'enable_clean_context',
                     'enable_safety_gate', 'enable_difficulty_estimator',
                     'enable_task_type_budget', 'enable_history_budget',
                     'enable_deer_trial', 'enable_soft_truncation',
                     'consecutive_same_stop', 'value_min_bottom_k',
                     'reasoning_min_percentile', 'full_chain_min_percentile',
                     'baseline_time_limit', 'deer_trial_stable_rounds',
                     'deer_trial_answer_len', 'deer_confidence_boost',
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
                'deer_trial_answer_len': self.config.deer_trial_answer_len,
                'deer_confidence_boost': self.config.deer_confidence_boost,
            })
            self.trial_evaluator.reset()
