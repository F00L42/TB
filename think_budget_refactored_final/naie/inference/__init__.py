"""naie.inference — 懒加载包 (PEP 562)

避免急切导入子模块，防止循环依赖问题。
"""

_LAZY_IMPORTS = {
    'call_model': ('naie.inference.think_budget', 'call_model'),
    'call_model_async': ('naie.inference.think_budget', 'call_model_async'),
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        value = getattr(mod, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'naie.inference' has no attribute {name!r}")


def __dir__():
    return list(_LAZY_IMPORTS.keys())
