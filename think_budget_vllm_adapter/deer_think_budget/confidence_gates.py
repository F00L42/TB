"""决策评分 — 双通道/单通道/完整链 AND门。

从 naie/optimizations/confidence_gates.py 移植，自包含。

统一决策评分 — 支持4种模式 (优先级从高到低):
  1. enable_full_chain_confidence — 完整思维链+答案 AND门
  2. enable_dual_channel — 双通道: 推理区(宽容) + 动作区(零容忍)
  3. enable_value_min_confidence — 单通道: 仅答案区 Bottom-K
  4. 默认 — 平均置信度

返回: (decision_score, value_min_raw, reasoning_score_raw, action_score_raw)
  - decision_score: [0,1] 概率域, 用于与 prob_th 比较
  - value_min_raw: 答案区 logprob 域原始值 (兼容旧 accept_reason)
  - reasoning_score_raw: 思维链/推理区 logprob 域原始值
  - action_score_raw: 动作/答案区 logprob 域原始值

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

import math

from think_budget_vllm_adapter.deer_think_budget.token_filter import (
    cal_value_min_confidence,
    cal_reasoning_zone_confidence,
)


def _has_observed_token_pairs(logprobs, tokens) -> bool:
    """仅在 sampled token 与 logprob 均实际返回时才允许置信度放行。"""
    return bool(logprobs and tokens and any(
        logprob is not None and token is not None
        for logprob, token in zip(logprobs, tokens)
    ))


def compute_decision_score(
    *args, **kwargs
) -> tuple[float, float, float, float]:
    """Legacy alias — delegates to compute_dual_channel_score."""
    return compute_dual_channel_score(*args, **kwargs)


def compute_supplementary_scores(
    *args, **kwargs
) -> tuple[float, float]:
    """Legacy alias — returns (reasoning_score_raw, action_score_raw)."""
    result = compute_dual_channel_score(*args, **kwargs)
    return result[2], result[3]


def compute_dual_channel_score(
    # 思维链参数
    reasoning_logprobs: list[float | None] | None,
    reasoning_tokens: list[str | None] | None,
    # 答案区参数
    ans_logprobs: list[float | None] | None,
    ans_tokens: list[str | None] | None,
    confidence: float,
    perplexity: float,
    *,
    # 完整链配置
    enable_full_chain_confidence: bool = False,
    full_chain_min_percentile: float = 0.1,
    # 答案区配置
    enable_value_min_confidence: bool = False,
    value_min_bottom_k: int = 3,
    # 双通道配置
    enable_dual_channel: bool = False,
    reasoning_min_percentile: float = 0.1,
) -> tuple[float, float, float, float]:
    """统一决策评分 — 支持4种模式。"""
    reasoning_score_raw = 0.0
    action_score_raw = 0.0

    # ─── P3.4 完整思维链+答案 AND门 (最高优先) ───
    if enable_full_chain_confidence:
        if not (_has_observed_token_pairs(reasoning_logprobs, reasoning_tokens)
                and _has_observed_token_pairs(ans_logprobs, ans_tokens)):
            # 显式启用严格模式却无法得到 sampled-token 证据时必须失败关闭，
            # 不能静默退化为 Value Min/平均分而放行请求。
            return 0.0, float("-inf"), float("-inf"), float("-inf")
        from think_budget_vllm_adapter.deer_think_budget.confidence import cal_full_chain_confidence
        decision_score, chain_score_raw, ans_score_raw = cal_full_chain_confidence(
            reasoning_logprobs, reasoning_tokens,
            ans_logprobs, ans_tokens,
            chain_min_percentile=full_chain_min_percentile,
            bottom_k=value_min_bottom_k)
        value_min_raw = ans_score_raw          # 兼容旧 accept_reason
        reasoning_score_raw = chain_score_raw  # 思维链区原始分
        action_score_raw = ans_score_raw       # 答案区原始分

    # ─── 双通道: 推理区(宽容) + 动作区(零容忍) ───
    elif enable_dual_channel:
        if not (_has_observed_token_pairs(reasoning_logprobs, reasoning_tokens)
                and _has_observed_token_pairs(ans_logprobs, ans_tokens)):
            return 0.0, float("-inf"), float("-inf"), float("-inf")
        # 推理区: Min-10% 均值 (宽容度策略)
        reasoning_score_raw = cal_reasoning_zone_confidence(
            reasoning_logprobs, reasoning_tokens,
            min_percentile=reasoning_min_percentile)
        reasoning_score = min(math.exp(reasoning_score_raw), 1.0)

        # 动作/答案区: Bottom-K (零容忍策略)
        action_score_raw = cal_value_min_confidence(
            ans_logprobs, ans_tokens,
            bottom_k=value_min_bottom_k)
        action_score = min(math.exp(action_score_raw), 1.0)

        # 决策聚合: 严格 AND 门
        decision_score = min(reasoning_score, action_score)
        value_min_raw = action_score_raw

    # ─── P3.1 单通道: 仅答案区 Bottom-K ───
    elif enable_value_min_confidence:
        if not _has_observed_token_pairs(ans_logprobs, ans_tokens):
            return 0.0, float("-inf"), 0.0, float("-inf")
        decision_score_raw = cal_value_min_confidence(
            ans_logprobs, ans_tokens, bottom_k=value_min_bottom_k)
        decision_score = min(math.exp(decision_score_raw), 1.0)
        value_min_raw = decision_score_raw
        action_score_raw = decision_score_raw

    # ─── 默认: 平均置信度 ───
    else:
        decision_score_raw = confidence
        decision_score = decision_score_raw
        value_min_raw = 0.0

    return decision_score, value_min_raw, reasoning_score_raw, action_score_raw
