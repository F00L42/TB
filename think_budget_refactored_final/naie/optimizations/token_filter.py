"""结构化Token过滤 + Value Min Confidence — P3.1 核心创新。

公共接口: is_structural_token, set_task_key_names, set_extra_struct_tokens,
          extract_value_logprobs, cal_value_min_confidence, cal_reasoning_zone_confidence

实现已迁移到 _struct_token_core.py 以打破循环依赖:
  confidence.py → token_filter.py → __init__.py → context_control.py → confidence.py
本模块保留公共 API，全部委托给 _struct_token_core。
"""
from naie.optimizations._struct_token_core import (
    is_structural_token,
    set_task_key_names,
    set_extra_struct_tokens,
    get_key_names,
    extract_value_logprobs,
    DEFAULT_STRUCT_TOKENS,
    DEFAULT_KEY_NAMES,
)


def cal_value_min_confidence(logprobs, tokens, bottom_k: int = 3) -> float:
    """Value 最低 K 个 token logprob 均值 — P3.1 核心创新。

    剔除结构 token (JSON 语法、键名等) 的高 logprob，
    只看 Value token (数字、中文值) 的 bottom-K，避免结构 token 掩盖低置信度。

    Args:
        logprobs: token logprob 列表
        tokens: token 文本列表 (与 logprobs 等长)
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


def cal_reasoning_zone_confidence(logprobs, tokens, min_percentile: float = 0.1) -> float:
    """推理区置信度 — 宽容度策略: 取最差 min_percentile(默认10%) Value Token 的 logprob 均值。

    逻辑: 推理链条允许偶尔废话(连接词低置信度), 但不允许出现严重逻辑断层。
    比纯全局平均更敏锐(能抓到严重幻觉), 又比 Bottom-1 更稳定(不会被单个连接词毙掉)。
    与动作区的零容忍 Bottom-K 策略互补, 构成双通道评估。

    参数:
        logprobs: token logprob 列表
        tokens: token 文本列表 (与 logprobs 等长)
        min_percentile: 取最差百分比, 默认 0.1 = 最差 10%
    返回:
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
