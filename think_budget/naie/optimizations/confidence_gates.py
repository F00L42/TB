import math
from naie.optimizations.token_filter import cal_value_min_confidence, cal_reasoning_zone_confidence


def compute_decision_score(ans_logprobs, ans_tokens, confidence, perplexity, *,
                           enable_value_min_confidence=False,
                           enable_full_chain_confidence=False,
                           full_chain_min_percentile=0.1,
                           value_min_bottom_k=3):
    """单通道决策评分 — 答案区 Bottom-K 或完整链 AND门。"""
    if enable_full_chain_confidence:
        if ans_logprobs and ans_tokens:
            decision_score_raw = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k=value_min_bottom_k)
            decision_score = min(math.exp(decision_score_raw), 1.0)
        else:
            decision_score_raw = confidence
            decision_score = decision_score_raw
        value_min_raw = decision_score_raw
        return decision_score, value_min_raw

    if enable_value_min_confidence and ans_logprobs and ans_tokens:
        decision_score_raw = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k=value_min_bottom_k)
        decision_score = min(math.exp(decision_score_raw), 1.0)
    else:
        decision_score_raw = confidence
        decision_score = decision_score_raw

    value_min_raw = decision_score_raw if enable_value_min_confidence else 0.0
    return decision_score, value_min_raw


def compute_supplementary_scores(ans_logprobs, **kwargs):
    """补充评分 — 已废弃, 保留空函数体兼容调用。始终返回 0.0。"""
    return 0.0


def compute_dual_channel_score(
    # 思维链参数
    reasoning_logprobs, reasoning_tokens,
    # 答案区参数
    ans_logprobs, ans_tokens,
    confidence, perplexity,
    *,
    # 完整链配置
    enable_full_chain_confidence=False,
    full_chain_min_percentile=0.1,
    # 答案区配置
    enable_value_min_confidence=False,
    value_min_bottom_k=3,
    # 双通道配置
    enable_dual_channel=False,
    reasoning_min_percentile=0.1,
):
    """统一决策评分 — 支持4种模式: 完整链AND门 / 双通道 / 单通道 / 默认。

    优先级: enable_full_chain_confidence > enable_dual_channel > enable_value_min_confidence > 默认

    返回: (decision_score, value_min_raw, reasoning_score_raw, action_score_raw)
      - decision_score: [0,1] 概率域, 用于与 prob_th 比较
      - value_min_raw: 答案区 logprob 域原始值 (兼容旧 accept_reason)
      - reasoning_score_raw: 思维链/推理区 logprob 域原始值
      - action_score_raw: 动作/答案区 logprob 域原始值
    """
    reasoning_score_raw = 0.0
    action_score_raw = 0.0

    # ─── P3.4 完整思维链+答案 AND门 (最高优先) ───
    if enable_full_chain_confidence and reasoning_logprobs and ans_logprobs and ans_tokens:
        # 懒导入 — 避免 confidence_gates→confidence 循环依赖
        from naie.models.confidence import cal_full_chain_confidence
        decision_score, chain_score_raw, ans_score_raw = cal_full_chain_confidence(
            reasoning_logprobs, reasoning_tokens,
            ans_logprobs, ans_tokens,
            chain_min_percentile=full_chain_min_percentile,
            bottom_k=value_min_bottom_k)
        value_min_raw = ans_score_raw          # 兼容旧 accept_reason
        reasoning_score_raw = chain_score_raw  # 思维链区原始分
        action_score_raw = ans_score_raw       # 答案区原始分

    # ─── 双通道: 推理区(宽容) + 动作区(零容忍) ───
    elif enable_dual_channel and reasoning_logprobs and reasoning_tokens and ans_logprobs and ans_tokens:
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
    elif enable_value_min_confidence and ans_logprobs and ans_tokens:
        decision_score_raw = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k=value_min_bottom_k)
        decision_score = min(math.exp(decision_score_raw), 1.0)
        value_min_raw = decision_score_raw
        action_score_raw = decision_score_raw

    # ─── 默认: 平均置信度 ───
    else:
        decision_score_raw = confidence
        decision_score = decision_score_raw
        value_min_raw = 0.0

    return decision_score, value_min_raw, reasoning_score_raw, action_score_raw
