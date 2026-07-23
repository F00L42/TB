"""naie.scoring — 懒加载包 (PEP 562)"""

_LAZY_IMPORTS = {
    'preprocess_json_string': ('naie.scoring.json_utils', 'preprocess_json_string'),
    'flatten_json': ('naie.scoring.json_utils', 'flatten_json'),
    'compute_similarity': ('naie.scoring.json_utils', 'compute_similarity'),
    'is_list_equal_unordered': ('naie.scoring.json_utils', 'is_list_equal_unordered'),
    'compare_json_entities': ('naie.scoring.json_utils', 'compare_json_entities'),
    'full_compare_json_entities': ('naie.scoring.json_utils', 'full_compare_json_entities'),
    'extract_think_answer': ('naie.scoring.answer_extractor', 'extract_think_answer'),
    'calculate_score': ('naie.scoring.score', 'calculate_score'),
    'evaluate_answer': ('naie.scoring.llm_judge', 'evaluate_answer'),
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        value = getattr(mod, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'naie.scoring' has no attribute {name!r}")


def __dir__():
    return list(_LAZY_IMPORTS.keys())
