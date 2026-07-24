"""Think Budget algorithm with DEER's vLLM request-lifecycle integration."""

# 预导入内嵌的 deer_think_budget 子模块，确保其内部的相互引用能正确解析
import think_budget_vllm_adapter.deer_think_budget  # noqa: F401

from think_budget_vllm_adapter.budget_controller import BudgetController
from think_budget_vllm_adapter.context import (
    AlgorithmConfig,
    BudgetConfig,
    BudgetPlan,
    BudgetVector,
    PerformancePolicy,
)
from think_budget_vllm_adapter.deer_think_budget.context import (
    BudgetRequestContext,
)

__all__ = [
    'BudgetController',
    'AlgorithmConfig',
    'BudgetConfig',
    'BudgetPlan',
    'BudgetVector',
    'PerformancePolicy',
    'BudgetRequestContext',
]
