"""naie.prompts — 懒加载包 (PEP 562)"""

_LAZY_IMPORTS = {
    'toolselect_5to1_prompt': ('naie.prompts.templates', 'toolselect_5to1_prompt'),
    'toolselect_6to1_prompt': ('naie.prompts.templates', 'toolselect_6to1_prompt'),
    'paramselect_prompt': ('naie.prompts.templates', 'paramselect_prompt'),
    'generate_prompt_question': ('naie.prompts.builder', 'generate_prompt_question'),
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        value = getattr(mod, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'naie.prompts' has no attribute {name!r}")


def __dir__():
    return list(_LAZY_IMPORTS.keys())
