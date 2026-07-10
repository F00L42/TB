"""naie.models — 懒加载包 (PEP 562)

模型客户端、BudgetForcing循环、置信度计算等核心模块。
"""
_LAZY_IMPORTS = {
    'model_client': ('naie.models.model_client', 'model_client'),
    'async_model_call': ('naie.models.model_client', 'async_model_call'),
    'async_close_client': ('naie.models.model_client', 'async_close_client'),
    'MODEL_CONFIGS': ('naie.models.config', 'MODEL_CONFIGS'),
    'call_model': ('naie.models.budget_forcing', 'call_model'),
    'call_model_async': ('naie.models.budget_forcing', 'call_model_async'),
    'cal_confidence': ('naie.models.confidence', 'cal_confidence'),
    'cal_perplexity': ('naie.models.confidence', 'cal_perplexity'),
    'is_think_tag_paired': ('naie.models.confidence', 'is_think_tag_paired'),
}

__all__ = list(_LAZY_IMPORTS.keys())


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        value = getattr(mod, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'naie.models' has no attribute {name!r}")


def __dir__():
    return list(_LAZY_IMPORTS.keys())
