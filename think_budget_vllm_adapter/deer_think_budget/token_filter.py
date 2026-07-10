"""结构化Token过滤 + Value Min Confidence — P3.1 核心创新。

从 naie/optimizations/_struct_token_core.py + token_filter.py 移植，
自包含无外部依赖（仅 vllm 标准库）。

公共接口:
  is_structural_token, set_task_key_names, set_extra_struct_tokens,
  extract_value_logprobs, cal_value_min_confidence, cal_reasoning_zone_confidence

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

# ─── 全局配置 ───
_task_key_names: set[str] = set()
_extra_struct_tokens: set[str] = set()

# JSON 语法 token — 在所有场景中都是可预测的结构 token
DEFAULT_STRUCT_TOKENS = frozenset({
    '{', '}', '[', ']', ':', ',', '"', '```', '```json', '```python',
    '\\n', '\\t', 'true', 'false', 'null', 'True', 'False', 'None',
    '->', '=>', '::',
})

# 默认 JSON 键名 — 空集合，由 set_task_key_names() 注入
DEFAULT_KEY_NAMES: frozenset[str] = frozenset()


def set_task_key_names(names: set[str] | list[str]) -> None:
    """注入任务特有的 JSON 键名集合。"""
    global _task_key_names
    _task_key_names = set(names)


def set_extra_struct_tokens(tokens: set[str] | list[str]) -> None:
    """注入额外的结构 token。"""
    global _extra_struct_tokens
    _extra_struct_tokens = set(tokens)


def get_key_names() -> set[str]:
    """返回当前注入的键名集合。"""
    return _task_key_names


def is_structural_token(
    token: str,
    key_names: set[str] | None = None,
    extra_struct_tokens: set[str] | None = None,
) -> bool:
    """判断 token 是否为结构化 token（JSON 语法/键名/标点等）。

    结构 token 的 logprob 通常很高（可预测），不应参与置信度计算，
    否则会掩盖 Value token（数字、中文值）的低置信度。
    """
    t = token.strip()
    if not t:
        return True  # 空白 token
    if t in DEFAULT_STRUCT_TOKENS:
        return True
    if key_names is None:
        key_names = _task_key_names
    if t in key_names:
        return True
    if extra_struct_tokens is None:
        extra_struct_tokens = _extra_struct_tokens
    if t in extra_struct_tokens:
        return True
    # 单字符标点
    if len(t) == 1 and t in '.,;!?()':
        return True
    return False


def extract_value_logprobs(
    logprobs: list[float | None],
    tokens: list[str | None],
) -> list[float]:
    """从 logprobs/tokens 列表中提取 Value token 的 logprob。

    过滤掉结构 token（JSON 语法、键名、空白），只保留 Value token。
    这是 Value Min Confidence 的核心步骤：结构 token 的高 logprob
    不应参与置信度计算。
    """
    if not logprobs or not tokens:
        return []
    value_lps = []
    for lp, tok in zip(logprobs, tokens):
        if lp is None or tok is None:
            continue
        if is_structural_token(tok):
            continue
        value_lps.append(lp)
    return value_lps


def cal_value_min_confidence(
    logprobs: list[float | None],
    tokens: list[str | None],
    bottom_k: int = 3,
) -> float:
    """Value 最低 K 个 token logprob 均值 — P3.1 核心创新。

    剔除结构 token (JSON 语法、键名等) 的高 logprob，
    只看 Value token (数字、中文值) 的 bottom-K，避免结构 token 掩盖低置信度。

    Args:
        logprobs: token logprob 列表 (float | None)
        tokens: token 文本列表 (str | None，与 logprobs 等长)
        bottom_k: 取最低 K 个 value token 的 logprob 均值, 默认 3
    Returns:
        bottom-K value token 的 logprob 均值 (logprob 域, 非概率域)
    """
    value_lps = extract_value_logprobs(logprobs, tokens)
    if not value_lps:
        valid = [lp for lp in logprobs if lp is not None] if logprobs else []
        return min(valid) if valid else 0.0
    value_lps.sort()
    k = min(bottom_k, len(value_lps))
    return sum(value_lps[:k]) / k


def cal_reasoning_zone_confidence(
    logprobs: list[float | None],
    tokens: list[str | None],
    min_percentile: float = 0.1,
) -> float:
    """推理区置信度 — 宽容度策略: 取最差 min_percentile(默认10%) Value Token 的 logprob 均值。

    逻辑: 推理链条允许偶尔废话(连接词低置信度), 但不允许出现严重逻辑断层。
    比纯全局平均更敏锐(能抓到严重幻觉), 又比 Bottom-1 更稳定(不会被单个连接词毙掉)。
    与动作区的零容忍 Bottom-K 策略互补, 构成双通道评估。

    Args:
        logprobs: token logprob 列表
        tokens: token 文本列表 (与 logprobs 等长)
        min_percentile: 取最差百分比, 默认 0.1 = 最差 10%
    Returns:
        最差 min_percentile 个 Value Token 的 logprob 均值 (logprob 域, 非概率域)
    """
    value_lps = extract_value_logprobs(logprobs, tokens)
    if not value_lps:
        valid = [lp for lp in logprobs if lp is not None] if logprobs else []
        return min(valid) if valid else 0.0
    value_lps.sort()
    k = max(1, int(len(value_lps) * min_percentile))
    k = min(k, len(value_lps))
    return sum(value_lps[:k]) / k
