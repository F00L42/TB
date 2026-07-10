"""Token 过滤兼容桥接 — 从 deer_think_budget.token_filter 导入并重新暴露。

think_budget 源码的 token_filter.py 提供了以下公共接口:
  - is_structural_token
  - set_task_key_names
  - set_extra_struct_tokens
  - get_key_names
  - extract_value_logprobs
  - cal_value_min_confidence
  - cal_reasoning_zone_confidence
  - DEFAULT_STRUCT_TOKENS, DEFAULT_KEY_NAMES

这些函数已在 deer_think_budget.token_filter 中实现。
本模块桥接到 deer_think_budget 的实现，避免 adapter 中的高级模块直接引用。
"""

from think_budget_vllm_adapter.deer_think_budget.token_filter import (
    is_structural_token,
    set_task_key_names,
    set_extra_struct_tokens,
    get_key_names,
    extract_value_logprobs,
    cal_value_min_confidence,
    cal_reasoning_zone_confidence,
    DEFAULT_STRUCT_TOKENS,
    DEFAULT_KEY_NAMES,
)

__all__ = [
    'is_structural_token',
    'set_task_key_names',
    'set_extra_struct_tokens',
    'get_key_names',
    'extract_value_logprobs',
    'cal_value_min_confidence',
    'cal_reasoning_zone_confidence',
    'DEFAULT_STRUCT_TOKENS',
    'DEFAULT_KEY_NAMES',
]
