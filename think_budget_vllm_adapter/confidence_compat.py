"""置信度兼容桥接 — 从 deer_think_budget.confidence 导入并重新暴露。

think_budget 源码的 confidence.py 提供了以下函数:
  - cal_confidence, cal_perplexity (已在 deer_think_budget 中)
  - vllm_logprobs_to_flat (已在 deer_think_budget 中，名为 extract_token_logprobs_and_texts)
  - cal_confidence_from_vllm, cal_perplexity_from_vllm (便捷包装)
  - is_think_tag_paired, get_think_closing_tag, reopen_think_tag (已在 deer_think_budget 中)
  - cal_min_token_confidence (简单工具函数)
  - cal_value_min_confidence, cal_reasoning_zone_confidence (懒导入 token_filter)
  - cal_full_chain_confidence (已在 deer_think_budget.confidence 中)

本模块将这些函数统一暴露为 think_budget_vllm_adapter 内部可用的接口，
避免 budget_controller 等模块直接引用 deer_think_budget 内部路径。
"""

# ─── 直接从内嵌的 deer_think_budget 子模块导入已有函数 ───
from think_budget_vllm_adapter.deer_think_budget.confidence import (
    cal_confidence,
    cal_perplexity,
    is_think_tag_paired,
    get_think_closing_tag,
    reopen_think_tag,
    extract_token_logprobs_and_texts,
    cal_full_chain_confidence,
)

import math


# ─── 兼容别名: vllm_logprobs_to_flat → extract_token_logprobs_and_texts ───
def vllm_logprobs_to_flat(logprobs_list):
    """将 vLLM 的 logprobs_list 转换为扁平格式 — 兼容旧名。

    实际委托给 deer_think_budget.confidence.extract_token_logprobs_and_texts。
    """
    flat_lps, tokens_txt = extract_token_logprobs_and_texts(logprobs_list)
    return flat_lps, tokens_txt


def cal_confidence_from_vllm(logprobs_list):
    """从 vLLM logprobs_list 直接计算置信度 (便捷函数)。"""
    flat_lps, _ = extract_token_logprobs_and_texts(logprobs_list)
    return cal_confidence(flat_lps)


def cal_perplexity_from_vllm(logprobs_list):
    """从 vLLM logprobs_list 直接计算困惑度 (便捷函数)。"""
    flat_lps, _ = extract_token_logprobs_and_texts(logprobs_list)
    return cal_perplexity(flat_lps)


def cal_min_token_confidence(logprobs) -> float:
    """全局最低 token logprob。"""
    if not logprobs:
        return 0.0
    valid = [lp for lp in logprobs if lp is not None]
    return min(valid) if valid else 0.0


# ─── 懒导入: token_filter 中的 Value Min Confidence 函数 ───
# 避免模块级循环依赖: confidence_compat ← confidence_gates_compat ← token_filter_compat
def cal_value_min_confidence(logprobs, tokens, bottom_k: int = 3) -> float:
    """Value 最低 K 个 token logprob 均值 — 剔除结构 token, 只看 Value token。

    实际计算委托给 deer_think_budget.token_filter.cal_value_min_confidence。
    """
    from think_budget_vllm_adapter.deer_think_budget.token_filter import cal_value_min_confidence as _cal_vmc
    return _cal_vmc(logprobs, tokens, bottom_k=bottom_k)


def cal_reasoning_zone_confidence(logprobs, tokens, min_percentile: float = 0.1) -> float:
    """推理区置信度 — 宽容度策略。

    实际计算委托给 deer_think_budget.token_filter.cal_reasoning_zone_confidence。
    """
    from think_budget_vllm_adapter.deer_think_budget.token_filter import cal_reasoning_zone_confidence as _cal_rzc
    return _cal_rzc(logprobs, tokens, min_percentile=min_percentile)
