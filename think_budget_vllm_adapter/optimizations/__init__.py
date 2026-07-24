"""Budget decision modules with no duplicate compatibility re-exports."""
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
