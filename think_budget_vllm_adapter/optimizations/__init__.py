"""优化模块 — Budget 决策链所需的优化子模块。

模块分类:
  - 高级优化模块 (从 think_budget 完整迁移，无外部依赖):
    complexity_analyzer, difficulty_estimator, scene_classifier,
    task_type_budget, trial_evaluator

  - 兼容桥接模块 (从 deer_think_budget 重新暴露):
    early_stopping_compat, confidence_gates_compat,
    token_filter_compat, context_control_compat, safety_gate_compat
"""
from think_budget_vllm_adapter.optimizations.complexity_analyzer import estimate_complexity
from think_budget_vllm_adapter.optimizations.difficulty_estimator import (
    estimate_difficulty, compute_budget_from_difficulty,
)
from think_budget_vllm_adapter.optimizations.scene_classifier import classify_scene, get_scene_typical_tokens
from think_budget_vllm_adapter.optimizations.task_type_budget import (
    classify_task_type, get_task_type_budget, register_new_task_type,
    is_known_task_type, compute_num_thinking_from_budget,
    compute_stride_from_budget,
)
from think_budget_vllm_adapter.optimizations.trial_evaluator import TrialAnswerEvaluator

# ─── 兼容桥接模块 (从 deer_think_budget 重新暴露) ───
from think_budget_vllm_adapter.optimizations.early_stopping_compat import (
    extract_answer_for_sc, check_consecutive_same,
    check_min_think_gate, make_accept_decision,
)
from think_budget_vllm_adapter.optimizations.confidence_gates_compat import (
    compute_decision_score, compute_dual_channel_score,
)
from think_budget_vllm_adapter.optimizations.token_filter_compat import (
    is_structural_token, set_task_key_names,
    extract_value_logprobs, cal_value_min_confidence,
    cal_reasoning_zone_confidence,
)
from think_budget_vllm_adapter.optimizations.context_control_compat import (
    make_induced_prompt, apply_clean_context,
)
from think_budget_vllm_adapter.optimizations.safety_gate_compat import (
    safety_gate_check, build_safety_fallback,
)
