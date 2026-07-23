"""结构化Token判断核心 — 独立模块，无任何外部导入，打破循环依赖。

循环依赖链:
  confidence.py → token_filter.py → __init__.py → context_control.py → confidence.py

解决方案: 将 is_structural_token 及其依赖的数据提取到本模块（无任何项目内导入），
confidence.py 和 token_filter.py 都从此模块导入，避免循环。

⚠️ 不要在本模块中添加任何项目内 import！
"""
import re
import threading

# ─── 默认结构 token 集合 ───
DEFAULT_STRUCT_TOKENS = {
    # JSON 语法
    '{', '}', '[', ']', '"', "'", ':', ',', '```', 'json', 'JSON',
    # 数学/代码高频结构符
    '=', '+', '-', '*', '/', '>', '<', '≥', '≤', '≈', '≠', '→', '=>',
    # 代码结构符
    ';', '!', '#', '%', '@', '&', '^', '~',
}

DEFAULT_KEY_NAMES = set()  # 空集合，由 set_task_key_names() 注入

_WHITESPACE_RE = re.compile(r'^\s+$')
_SPECIAL_TOKENS = ('<|im_start|>', '<|im_end|>', '<|im_sep|>')
_SINGLE_PUNCT = '{}[]"\':,._-|/\\()'

# ─── 模块级可变状态 + 线程安全锁 ───
_struct_tokens = set(DEFAULT_STRUCT_TOKENS)
_key_names = set(DEFAULT_KEY_NAMES)
_state_lock = threading.Lock()


def set_task_key_names(key_names):
    """注入任务特有的 JSON 键名集合（如 faultNumber, IMSI 等）— 线程安全。"""
    global _key_names
    with _state_lock:
        _key_names = {k.lower() for k in key_names}


def set_extra_struct_tokens(tokens):
    """追加额外结构 token — 线程安全。"""
    global _struct_tokens
    with _state_lock:
        _struct_tokens = set(DEFAULT_STRUCT_TOKENS) | set(tokens)


def get_key_names():
    """获取当前 key_names 集合（用于测试和同步）— 线程安全。"""
    with _state_lock:
        return set(_key_names)


def is_structural_token(token: str) -> bool:
    """判断 token 是否为结构 token（不参与置信度计算）— 线程安全读。

    结构 token: 标点、JSON 语法、任务键名、特殊标记、空白符。
    Value token: 数字、中文值、英文值等 — 参与置信度计算。

    性能说明: 使用局部快照避免持锁遍历，适用于高并发场景。
    """
    if not token:
        return True
    with _state_lock:
        st_snapshot = _struct_tokens
        kn_snapshot = _key_names
    if token in st_snapshot:
        return True
    if _WHITESPACE_RE.match(token):
        return True
    if token in _SPECIAL_TOKENS:
        return True
    cleaned = token.strip().lower()
    if cleaned in kn_snapshot:
        return True
    if len(cleaned) <= 1 and cleaned in _SINGLE_PUNCT:
        return True
    return False


def extract_value_logprobs(logprobs, tokens):
    """过滤结构 token logprob，只保留 Value token 的 logprob。"""
    if not logprobs or not tokens:
        return []
    value_lps = []
    for lp, tok in zip(logprobs, tokens):
        if lp is None:
            continue
        if is_structural_token(tok):
            continue
        value_lps.append(lp)
    return value_lps
