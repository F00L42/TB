"""naie.optimizations — 懒加载包 (PEP 562)

此包使用 __getattr__ 实现懒加载，避免循环导入问题:

循环链 (已解决):
  confidence.py → _struct_token_core → (触发) __init__.py
  → confidence_gates.py → confidence.cal_full_chain_confidence  ← 循环!

通过懒加载，只有实际访问的符号才会触发对应子模块的导入，
不会因 __init__.py 的急切导入而触发循环依赖。
"""

_LAZY_IMPORTS = {
    # token_filter.py (P3.1)
    'is_structural_token': ('naie.optimizations.token_filter', 'is_structural_token'),
    'set_task_key_names': ('naie.optimizations.token_filter', 'set_task_key_names'),
    'set_extra_struct_tokens': ('naie.optimizations.token_filter', 'set_extra_struct_tokens'),
    'cal_value_min_confidence': ('naie.optimizations.token_filter', 'cal_value_min_confidence'),
    'cal_reasoning_zone_confidence': ('naie.optimizations.token_filter', 'cal_reasoning_zone_confidence'),
    'extract_value_logprobs': ('naie.optimizations.token_filter', 'extract_value_logprobs'),

    # confidence_gates.py (P3.1)
    'compute_decision_score': ('naie.optimizations.confidence_gates', 'compute_decision_score'),
    'compute_dual_channel_score': ('naie.optimizations.confidence_gates', 'compute_dual_channel_score'),

    # early_stopping.py (P2.2)
    'extract_answer_for_sc': ('naie.optimizations.early_stopping', 'extract_answer_for_sc'),
    'check_consecutive_same': ('naie.optimizations.early_stopping', 'check_consecutive_same'),
    'check_min_think_gate': ('naie.optimizations.early_stopping', 'check_min_think_gate'),
    'make_accept_decision': ('naie.optimizations.early_stopping', 'make_accept_decision'),

    # safety_gate.py
    'safety_gate_check': ('naie.optimizations.safety_gate', 'safety_gate_check'),
    'build_safety_fallback': ('naie.optimizations.safety_gate', 'build_safety_fallback'),

    # complexity_analyzer.py (P4)
    'estimate_complexity': ('naie.optimizations.complexity_analyzer', 'estimate_complexity'),

    # difficulty_estimator.py (P5)
    'estimate_difficulty': ('naie.optimizations.difficulty_estimator', 'estimate_difficulty'),
    'compute_budget_from_difficulty': ('naie.optimizations.difficulty_estimator', 'compute_budget_from_difficulty'),
    'count_entities': ('naie.optimizations.difficulty_estimator', 'count_entities'),
    'count_constraints': ('naie.optimizations.difficulty_estimator', 'count_constraints'),
    'count_ambiguity': ('naie.optimizations.difficulty_estimator', 'count_ambiguity'),
    'needs_multi_tool': ('naie.optimizations.difficulty_estimator', 'needs_multi_tool'),
    'needs_cross_turn': ('naie.optimizations.difficulty_estimator', 'needs_cross_turn'),
    'needs_validation': ('naie.optimizations.difficulty_estimator', 'needs_validation'),
    'count_repeat_phrases': ('naie.optimizations.difficulty_estimator', 'count_repeat_phrases'),
    'detect_self_negation': ('naie.optimizations.difficulty_estimator', 'detect_self_negation'),

    # trial_evaluator.py (P6)
    'TrialAnswerEvaluator': ('naie.optimizations.trial_evaluator', 'TrialAnswerEvaluator'),

    # task_type_budget.py
    'classify_task_type': ('naie.optimizations.task_type_budget', 'classify_task_type'),
    'get_task_type_budget': ('naie.optimizations.task_type_budget', 'get_task_type_budget'),
    'get_all_task_type_budgets': ('naie.optimizations.task_type_budget', 'get_all_task_type_budgets'),
    'register_custom_task_type': ('naie.optimizations.task_type_budget', 'register_custom_task_type'),
    'register_new_task_type': ('naie.optimizations.task_type_budget', 'register_new_task_type'),
    'is_known_task_type': ('naie.optimizations.task_type_budget', 'is_known_task_type'),
    'TASK_TYPE_BUDGET_DEFAULTS': ('naie.optimizations.task_type_budget', 'TASK_TYPE_BUDGET_DEFAULTS'),
    'VALID_TASK_TYPES': ('naie.optimizations.task_type_budget', 'VALID_TASK_TYPES'),

    # history_budget.py
    'compute_history_budget': ('naie.optimizations.history_budget', 'compute_history_budget'),
    'detect_new_task_type': ('naie.optimizations.history_budget', 'detect_new_task_type'),
    'register_task_type_from_history': ('naie.optimizations.history_budget', 'register_task_type_from_history'),
}


def __getattr__(name):
    """PEP 562 — 按需懒加载，避免循环导入。"""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        value = getattr(mod, attr_name)
        # 缓存到模块命名空间，后续访问不再触发 __getattr__
        globals()[name] = value
        return value
    raise AttributeError(f"module 'naie.optimizations' has no attribute {name!r}")


def __dir__():
    """支持 dir() 和 IDE 自动补全。"""
    return list(_LAZY_IMPORTS.keys())
