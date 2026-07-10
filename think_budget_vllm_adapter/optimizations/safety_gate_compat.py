"""安全门兼容桥接 — 从 deer_think_budget.safety_gate 导入并重新暴露。

think_budget 源码的 safety_gate.py 提供:
  - safety_gate_check
  - build_safety_fallback

这些函数已在 deer_think_budget.safety_gate 中实现。
"""

from think_budget_vllm_adapter.deer_think_budget.safety_gate import (
    safety_gate_check,
    build_safety_fallback,
)

__all__ = ['safety_gate_check', 'build_safety_fallback']
