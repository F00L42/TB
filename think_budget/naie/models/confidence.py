"""置信度与困惑度计算 — 供 budget_forcing.py 和优化模块使用。

结构化 token 判断统一使用 _struct_token_core.is_structural_token，
解决了此前 confidence.py 与 token_filter.py 各自维护一套实现的同步问题。
_struct_token_core 是独立模块（无项目内导入），不会触发循环依赖。

注意: cal_value_min_confidence 和 cal_reasoning_zone_confidence 的规范实现
在 token_filter.py 中，本模块通过懒导入使用，避免模块级循环依赖。
"""
import math


def cal_confidence(logprobs) -> float:
    """平均 exp 概率 — 所有 token logprob 的 exp 均值。"""
    if not logprobs:
        return 0.0
    total = sum(math.exp(lp) for lp in logprobs if lp is not None)
    return total / len(logprobs)


def cal_perplexity(logprobs) -> float:
    """困惑度 — exp(平均负 logprob)。"""
    if not logprobs:
        return 0.0
    valid = [lp for lp in logprobs if lp is not None]
    if not valid:
        return 0.0
    return math.exp(-sum(valid) / len(valid))


# ============================================================
# 统一使用标准 XML 标签 <think> 和 </think>
# ============================================================

_THINK_OPEN_TAG = '<think>'
_THINK_CLOSE_TAG = '</think>'


def is_think_tag_paired(text) -> bool:
    """判断 <think> 与 </think> 是否配对出现。"""
    if not text:
        return False
    open_count = text.count(_THINK_OPEN_TAG)
    close_count = text.count(_THINK_CLOSE_TAG)
    return open_count == close_count


def get_think_closing_tag(text) -> str:
    """返回标准的 think 闭标签 (固定为 </think>)。"""
    return _THINK_CLOSE_TAG


def reopen_think_tag(text: str) -> str:
    """从文本中移除 </think> 闭标签及其之后的内容，使思考标签重新打开。

    例: "...<think>思考内容</think>答案<|im_end|>" → "...<think>思考内容"
    """
    text = text.rstrip()
    if text.endswith('<|im_end|>'):
        text = text[:-len('<|im_end|>')]

    # 找到最后一个 </think> 并截断
    idx = text.rfind(_THINK_CLOSE_TAG)
    if idx >= 0:
        text = text[:idx]

    return text.rstrip()


def cal_min_token_confidence(logprobs) -> float:
    """全局最低 token logprob。"""
    if not logprobs:
        return 0.0
    valid = [lp for lp in logprobs if lp is not None]
    return min(valid) if valid else 0.0


# ─── 懒导入: token_filter 中的 Value Min Confidence 函数 ───
# 避免模块级循环依赖: confidence ← confidence_gates ← token_filter
# 使用函数内导入，仅在调用时触发 import
def cal_value_min_confidence(logprobs, tokens, bottom_k: int = 3) -> float:
    """Value 最低 K 个 token logprob 均值 — 剔除结构 token, 只看 Value token。

    核心创新: 避免 {、}、:、faultNumber 等结构 token 的高 logprob 掩盖
    Value token (数字、中文值) 的低置信度。

    实际计算委托给 token_filter.cal_value_min_confidence，此处为便捷包装。
    """
    from naie.optimizations.token_filter import cal_value_min_confidence as _cal_vmc
    return _cal_vmc(logprobs, tokens, bottom_k=bottom_k)


def cal_reasoning_zone_confidence(logprobs, tokens, min_percentile: float = 0.1) -> float:
    """推理区置信度 — 宽容度策略: 取最差 min_percentile(默认10%) Value Token 的 logprob 均值。

    逻辑: 推理链条允许偶尔废话(连接词低置信度), 但不允许出现严重逻辑断层。
    比纯全局平均更敏锐(能抓到严重幻觉), 又比 Bottom-1 更稳定(不会被单个连接词毙掉)。
    与动作区的零容忍 Bottom-K 策略互补, 构成双通道评估。

    实际计算委托给 token_filter.cal_reasoning_zone_confidence，此处为便捷包装。
    """
    from naie.optimizations.token_filter import cal_reasoning_zone_confidence as _cal_rzc
    return _cal_rzc(logprobs, tokens, min_percentile=min_percentile)


def cal_full_chain_confidence(
    chain_logprobs, chain_tokens,
    ans_logprobs, ans_tokens,
    *, chain_min_percentile: float = 0.1, bottom_k: int = 3,
) -> tuple:
    """完整思维链+答案置信度 — 思维链Min-%(宽容) + 答案Bottom-K(零容忍), AND门取min。

    核心思想:
      - 思维链允许偶尔废话(连接词低置信度), 但不允许出现严重逻辑断层
      - 答案区零容忍, 每个Value token必须确信
      - AND门: 只有推理逻辑没有断层 AND 最终动作参数确信, 整条路径才算通过

    Args:
        chain_logprobs: 完整思维链(含所有历史思考)的 logprob 列表
        chain_tokens:   完整思维链的 token 文本列表
        ans_logprobs:   试探答案区的 logprob 列表
        ans_tokens:     试探答案区的 token 文本列表
        chain_min_percentile: 思维链最差百分比, 默认0.1=最差10%
        bottom_k:       答案区 Bottom-K, 默认3

    Returns:
        (decision_score, chain_score_raw, ans_score_raw)
        - decision_score: [0,1] 概率域, min(思维链score, 答案score)
        - chain_score_raw: 思维链区 logprob 域原始值
        - ans_score_raw: 答案区 logprob 域原始值
    """
    # 思维链区: Min-10% (宽容度策略)
    chain_score_raw = cal_reasoning_zone_confidence(
        chain_logprobs, chain_tokens, min_percentile=chain_min_percentile)
    chain_score = min(math.exp(chain_score_raw), 1.0)

    # 答案区: Bottom-K (零容忍)
    ans_score_raw = cal_value_min_confidence(ans_logprobs, ans_tokens, bottom_k=bottom_k)
    ans_score = min(math.exp(ans_score_raw), 1.0)

    # AND门
    decision_score = min(chain_score, ans_score)
    return decision_score, chain_score_raw, ans_score_raw
