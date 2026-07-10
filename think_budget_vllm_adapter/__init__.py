"""think_budget_vllm_adapter — Think Budget 高级模块适配 vLLM DEER 框架。

提供 Budget 决策链、任务类型判断、难度估计、场景分类、历史轨迹预算等高级功能，
配合内嵌的 deer_think_budget 模块中的基础状态机、置信度、token过滤等模块使用。

架构:
  ┌─────────────────────────────────────────────────────────────┐
  │ BudgetController (入口)                                     │
  │   ├─ BudgetConfig (扩展 ThinkBudgetConfig)                  │
  │   ├─ BudgetRequestContext (扩展 RequestStateContext)         │
  │   ├─ Budget 决策链:                                         │
  │   │   ├─ Step 0: BUDGET_ENABLED 总开关                      │
  │   │   ├─ Step 0.5: FIXED_BUDGET_TOKEN 覆盖                 │
  │   │   ├─ Step 2: scene_classifier + task_type_budget        │
  │   │   ├─ Step 3: difficulty_estimator                       │
  │   │   ├─ Step 4: history_budget                             │
  │   │   └─ Step 5: 复杂度分析 + 开关参数                      │
  │   └─ AsyncReasoningOrchestrator (复用 deer_think_budget)    │
  └─────────────────────────────────────────────────────────────┘

使用方式:
  1. 将本包放在 Python 路径下 (或与 vllm 同级)
  2. 在 vllm 的 serving.py 中导入 BudgetController 并添加分流逻辑
  3. 在 vllm 的 protocol.py 中添加 Budget 请求字段
  4. 通过 patches/ 目录中的补丁说明操作

注意: deer_think_budget 已内嵌为本包的子模块 (think_budget_vllm_adapter.deer_think_budget)，
无需依赖外部 deer_think_budget 顶级包或动态 sys.path 操作。
"""

# 预导入内嵌的 deer_think_budget 子模块，确保其内部的相互引用能正确解析
import think_budget_vllm_adapter.deer_think_budget  # noqa: F401

from think_budget_vllm_adapter.budget_controller import BudgetController
from think_budget_vllm_adapter.context import BudgetConfig, BudgetRequestContext

__all__ = ['BudgetController', 'BudgetConfig', 'BudgetRequestContext']
