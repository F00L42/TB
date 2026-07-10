"""置信度门兼容桥接 — 从 deer_think_budget.confidence_gates 导入并重新暴露。

think_budget 源码的 confidence_gates.py 提供:
  - compute_decision_score
  - compute_supplementary_scores
  - compute_dual_channel_score

这些函数已在 deer_think_budget.confidence_gates 中实现。
本模块桥接到 deer_think_budget 的实现。
"""

from think_budget_vllm_adapter.deer_think_budget.confidence_gates import (
    compute_decision_score,
    compute_supplementary_scores,
    compute_dual_channel_score,
)

__all__ = [
    'compute_decision_score',
    'compute_supplementary_scores',
    'compute_dual_channel_score',
]
