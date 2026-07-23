import math
from naie.optimizations.token_filter import cal_value_min_confidence, cal_reasoning_zone_confidence


def compute_decision_score(ans_logprobs, ans_tokens, confidence, perplexity, *,
                           enable_value_min_confidence=False,
                           enable_full_chain_confidence=False,
                           full_chain_min_percentile=0.1,
                           value_min_bottom_k=3):
    """单通道决策评分 — 兼容旧接口，仅支持 value_min 或默认模式。

    ⚠️ 此函数不支持完整链/双通道的推理区 logprobs 参数，无法正确执行
    完整链或双通道评估。请使用 compute_dual_channel_score() 代替。
    当 enable_full_chain_confidence=True 时，此函数退化为 value_min 模式
    并发出警告。
    """
    import warnings
    if enable_full_chain_confidence:
        warnings.warn(
            "compute_decision_score() 不支持完整链评估，请使用 compute_dual_channel_score()。"
            "当前调用退化为 value_min 模式。",
            DeprecationWarning,
            stacklevel=2,
        )

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


def _compute_full_chain_score(reasoning_logprobs, reasoning_tokens,
                              ans_logprobs, ans_tokens,
                              full_chain_min_percentile, value_min_bottom_k):
    """完整链 AND门: 思维链Min-%(宽容) + 答案Bottom-K(零容忍) → min()。

    Returns: (decision_score, chain_score_raw, ans_score_raw) or None if insufficient data
    """
    if not (reasoning_logprobs and reasoning_tokens and ans_logprobs and ans_tokens):
        return None
    from naie.models.confidence import cal_full_chain_confidence
    decision_score, chain_score_raw, ans_score_raw = cal_full_chain_confidence(
        reasoning_logprobs, reasoning_tokens,
        ans_logprobs, ans_tokens,
        chain_min_percentile=full_chain_min_percentile,
        bottom_k=value_min_bottom_k)
    return decision_score, chain_score_raw, ans_score_raw


def _compute_dual_channel_score(reasoning_logprobs, reasoning_tokens,
                                ans_logprobs, ans_tokens,
                                reasoning_min_percentile, value_min_bottom_k):
    """双通道: 推理区Min-%(宽容) + 动作区Bottom-K(零容忍) → min()。

    Returns: (decision_score, reasoning_score_raw, action_score_raw) or None if insufficient data
    """
    if not (reasoning_logprobs and reasoning_tokens and ans_logprobs and ans_tokens):
        return None
    reasoning_score_raw = cal_reasoning_zone_confidence(
        reasoning_logprobs, reasoning_tokens,
        min_percentile=reasoning_min_percentile)
    reasoning_score = min(math.exp(reasoning_score_raw), 1.0)

    action_score_raw = cal_value_min_confidence(
        ans_logprobs, ans_tokens,
        bottom_k=value_min_bottom_k)
    action_score = min(math.exp(action_score_raw), 1.0)

    decision_score = min(reasoning_score, action_score)
    return decision_score, reasoning_score_raw, action_score_raw


def _compute_value_min_score(ans_logprobs, ans_tokens, value_min_bottom_k):
    """单通道 P3.1: 仅答案区 Bottom-K。

    Returns: (decision_score, value_min_raw) or None if insufficient data
    """
    if not (ans_logprobs and ans_tokens):
        return None
    decision_score_raw = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k=value_min_bottom_k)
    decision_score = min(math.exp(decision_score_raw), 1.0)
    return decision_score, decision_score_raw


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
    """统一决策评分 — 支持融合模式: 多个开关同时启用时取各模式 decision_score 的 min。

    融合策略:
      - 三个开关可任意组合启用
      - 每个启用的模式独立计算其 decision_score
      - 最终 decision_score = min(所有有效模式的 decision_score) — AND 语义
      - 无任何模式启用时回退到平均置信度

    与旧版区别:
      - 旧版: 互斥优先级 (full_chain > dual_channel > value_min > 默认)
      - 新版: 融合模式 (多开关启用时各模式独立评估取 min, 更严格也更全面)

    返回: (decision_score, value_min_raw, reasoning_score_raw, action_score_raw)
      - decision_score: [0,1] 概率域, 用于与 prob_th 比较
      - value_min_raw: 答案区 logprob 域原始值 (兼容旧 accept_reason)
      - reasoning_score_raw: 思维链/推理区 logprob 域原始值 (取最严格的那个)
      - action_score_raw: 动作/答案区 logprob 域原始值 (取最严格的那个)
    """
    reasoning_score_raw = 0.0
    action_score_raw = 0.0
    value_min_raw = 0.0

    # 收集所有启用模式的有效分数
    _scores = []          # (decision_score, source_name)
    _reasoning_raws = []  # reasoning_score_raw 候选
    _action_raws = []     # action_score_raw 候选
    _value_min_raws = []  # value_min_raw 候选

    # ─── P3.4 完整链 AND门 ───
    if enable_full_chain_confidence:
        fc = _compute_full_chain_score(
            reasoning_logprobs, reasoning_tokens,
            ans_logprobs, ans_tokens,
            full_chain_min_percentile, value_min_bottom_k)
        if fc is not None:
            ds, cs_raw, as_raw = fc
            _scores.append((ds, 'full_chain'))
            _reasoning_raws.append(cs_raw)
            _action_raws.append(as_raw)
            _value_min_raws.append(as_raw)

    # ─── 双通道: 推理区(宽容) + 动作区(零容忍) ───
    if enable_dual_channel:
        dc = _compute_dual_channel_score(
            reasoning_logprobs, reasoning_tokens,
            ans_logprobs, ans_tokens,
            reasoning_min_percentile, value_min_bottom_k)
        if dc is not None:
            ds, rs_raw, as_raw = dc
            _scores.append((ds, 'dual_channel'))
            _reasoning_raws.append(rs_raw)
            _action_raws.append(as_raw)
            _value_min_raws.append(as_raw)

    # ─── P3.1 单通道: 仅答案区 Bottom-K ───
    if enable_value_min_confidence:
        vm = _compute_value_min_score(ans_logprobs, ans_tokens, value_min_bottom_k)
        if vm is not None:
            ds, vm_raw = vm
            _scores.append((ds, 'value_min'))
            _action_raws.append(vm_raw)
            _value_min_raws.append(vm_raw)

    # ─── 聚合 ───
    _value_min_computed = bool(_scores)  # 有任何模式有效计算过
    if _scores:
        # AND 语义: 取所有有效模式的 min
        decision_score = min(s for s, _ in _scores)
        # 诊断原始值: 取最严格(最负)的那个
        reasoning_score_raw = min(_reasoning_raws) if _reasoning_raws else 0.0
        action_score_raw = min(_action_raws) if _action_raws else 0.0
        value_min_raw = min(_value_min_raws) if _value_min_raws else 0.0
    else:
        # ─── 默认: 平均置信度 ───
        decision_score = confidence
        value_min_raw = 0.0
        _value_min_computed = False

    return decision_score, value_min_raw, reasoning_score_raw, action_score_raw, _value_min_computed
