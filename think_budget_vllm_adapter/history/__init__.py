"""历史轨迹模块。"""
from think_budget_vllm_adapter.history.history_module import (
    AsyncHistoryRecorder, HistoryOutcome,
    record_inference, load_history, get_category_stats,
    get_scene_stats, get_task_type_stats, get_cv_for_task_type,
    clear_history,
)
from think_budget_vllm_adapter.history.history_budget import (
    compute_history_budget, register_task_type_from_history,
)

__all__ = [
    'AsyncHistoryRecorder', 'HistoryOutcome',
    'record_inference', 'load_history', 'get_category_stats',
    'get_scene_stats', 'get_task_type_stats', 'get_cv_for_task_type',
    'clear_history',
    'compute_history_budget', 'register_task_type_from_history',
]
