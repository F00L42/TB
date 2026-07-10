"""naie.evaluation — 懒加载包 (PEP 562)"""

_LAZY_IMPORTS = {
    'load_datasets': ('naie.evaluation.runner', 'load_datasets'),
    'calculate_metrics': ('naie.evaluation.runner', 'calculate_metrics'),
    'evaluate_baseline': ('naie.evaluation.runner', 'evaluate_baseline'),
    'evaluate_budget': ('naie.evaluation.runner', 'evaluate_budget'),
    'evaluate_baseline_parallel': ('naie.evaluation.parallel_runner', 'evaluate_baseline_parallel'),
    'evaluate_budget_parallel': ('naie.evaluation.parallel_runner', 'evaluate_budget_parallel'),
    '_evaluate_one_async': ('naie.evaluation.parallel_runner', '_evaluate_one_async'),
    'analyze_results': ('naie.evaluation.analyzer', 'analyze_results'),
    'print_comparison': ('naie.evaluation.analyzer', 'print_comparison'),
    'log_trajectory': ('naie.evaluation.trajectory_logger', 'log_trajectory'),
    'build_trajectory_entry': ('naie.evaluation.trajectory_logger', 'build_trajectory_entry'),
    'load_trajectories': ('naie.evaluation.trajectory_logger', 'load_trajectories'),
    'get_trajectory_summary': ('naie.evaluation.trajectory_logger', 'get_trajectory_summary'),
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        value = getattr(mod, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'naie.evaluation' has no attribute {name!r}")


def __dir__():
    return list(_LAZY_IMPORTS.keys())
