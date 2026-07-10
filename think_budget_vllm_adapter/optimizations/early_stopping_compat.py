"""early_stopping 兼容桥接 — 从 deer_think_budget.early_stopping 导入并重新暴露。

trial_evaluator.py 依赖 extract_answer_for_sc 和 _normalize_for_sc，
这些函数已在 deer_think_budget.early_stopping 中实现。
本模块作为桥接层，避免重复实现。
"""

from think_budget_vllm_adapter.deer_think_budget.early_stopping import (
    extract_answer_for_sc,
    _normalize_for_sc,
    check_consecutive_same,
    check_min_think_gate,
    make_accept_decision,
)

__all__ = [
    'extract_answer_for_sc',
    '_normalize_for_sc',
    'check_consecutive_same',
    'check_min_think_gate',
    'make_accept_decision',
]
