"""上下文控制兼容桥接 — 从 deer_think_budget.context_control 导入并重新暴露。

think_budget 源码的 context_control.py 提供:
  - make_induced_prompt
  - apply_clean_context

这些函数已在 deer_think_budget.context_control 中实现。
"""

from think_budget_vllm_adapter.deer_think_budget.context_control import (
    make_induced_prompt,
    apply_clean_context,
)

__all__ = ['make_induced_prompt', 'apply_clean_context']
