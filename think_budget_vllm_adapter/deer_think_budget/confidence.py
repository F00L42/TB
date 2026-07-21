"""置信度与困惑度计算 — 供 state.py 和 confidence_gates.py 使用。

从 naie/models/confidence.py 移植，自包含无 naie 依赖。
新增 vLLM logprobs 格式转换工具函数。

Think tags 使用标准 XML 标记: <think> / </think>
(与 DEER 原版 state.py 和 think_budget confidence.py 一致)

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

import math

# ─── Think tags (严格按照 deer 源码 + think_budget 源码) ───
# deer/state.py: params.stop = ["</think>"], stop_reason == "</think>"
# think_budget/confidence.py: _THINK_OPEN_TAG = '<think>', _THINK_CLOSE_TAG = '</think>'
_THINK_OPEN_TAG = '<think>'
_THINK_CLOSE_TAG = '</think>'


def cal_confidence(logprobs: list[float | None]) -> float:
    """有效 token 的平均 exp 概率。

    ``None`` 表示 vLLM 没有返回该位置的 sampled-token logprob，不能既从
    分子跳过又留在分母中；否则缺失值会系统性压低分数，并与 perplexity 的
    有效样本口径不一致。
    """
    valid = [lp for lp in logprobs if lp is not None]
    if not valid:
        return 0.0
    return sum(math.exp(lp) for lp in valid) / len(valid)


def cal_perplexity(logprobs: list[float | None]) -> float:
    """困惑度 — exp(平均负 logprob)。"""
    if not logprobs:
        return 0.0
    valid = [lp for lp in logprobs if lp is not None]
    if not valid:
        return 0.0
    return math.exp(-sum(valid) / len(valid))


def is_think_tag_paired(text: str) -> bool:
    """判断 <think> 与 </think> 是否配对出现。"""
    if not text:
        return False
    open_count = text.count(_THINK_OPEN_TAG)
    close_count = text.count(_THINK_CLOSE_TAG)
    return open_count == close_count


def get_think_closing_tag(text: str = '') -> str:
    """返回标准的 think 闭标签 (固定为 </think>)。"""
    return _THINK_CLOSE_TAG


# ─── vLLM logprobs 格式转换 ───
# vLLM CompletionOutput.logprobs 是 SampleLogprobs 类型:
#   list[LogprobsOnePosition] 其中 LogprobsOnePosition = dict[str, Logprob]
#   Logprob(logprob: float, rank: int | None, decoded_token: str | None)
#
# Think Budget 算法需要:
#   logprobs: list[float | None] — 每个 token 的 top-1 logprob
#   tokens:   list[str | None]   — 每个 token 的文本

def _matches_sampled_token_id(candidate_id, sampled_token_id: int) -> bool:
    """兼容 vLLM 不同版本中 int 或字符串形式的 token-id 字典键。"""
    if candidate_id == sampled_token_id:
        return True
    try:
        return int(candidate_id) == sampled_token_id
    except (TypeError, ValueError):
        return False


def _sampled_logprob_entry(position, sampled_token_id: int):
    """从一个 vLLM 位置的候选字典中精确找回实际采样 token。"""
    if not hasattr(position, "items"):
        return None

    # vLLM 的 SampleLogprobs 按 token id 映射；先走 O(1) 查找，再兼容
    # JSON/封装层把键转为字符串的情况。绝不能以字典第一个元素作为猜测。
    for key in (sampled_token_id, str(sampled_token_id)):
        try:
            entry = position.get(key)
        except AttributeError:
            entry = None
        if entry is not None:
            return entry
    for key, entry in position.items():
        if _matches_sampled_token_id(key, sampled_token_id):
            return entry
        if getattr(entry, "token_id", None) == sampled_token_id:
            return entry
    return None


def extract_token_logprobs_and_texts(
    logprobs_list,
    sampled_token_ids: list[int] | tuple[int, ...] | None,
) -> tuple[list[float | None], list[str | None]]:
    """将 vLLM SampleLogprobs 转换为实际采样 token 的 logprob 与文本。

    Args:
        logprobs_list: vLLM CompletionOutput.logprobs，每个位置是 token-id
            到 Logprob 的映射。
        sampled_token_ids: 同一输出片段的 ``CompletionOutput.token_ids``。
            vLLM 的候选字典还含 top-k 备选 token，必须用该序列精确匹配实际
            采样 token；缺失或不匹配时返回 ``None``，而不猜测字典顺序。

    Returns:
        (logprobs, tokens): 两个等长列表，用于 token_filter / confidence 计算
    """
    token_logprobs: list[float | None] = []
    token_texts: list[str | None] = []

    if logprobs_list is None:
        return token_logprobs, token_texts

    sampled_token_ids = sampled_token_ids or ()
    for index, pos in enumerate(logprobs_list):
        if pos is None or index >= len(sampled_token_ids):
            token_logprobs.append(None)
            token_texts.append(None)
            continue

        sampled_entry = _sampled_logprob_entry(pos, sampled_token_ids[index])
        if sampled_entry is None or not hasattr(sampled_entry, "logprob"):
            token_logprobs.append(None)
            token_texts.append(None)
            continue
        token_logprobs.append(sampled_entry.logprob)
        token_texts.append(getattr(sampled_entry, "decoded_token", None))

    return token_logprobs, token_texts


def cal_full_chain_confidence(
    chain_logprobs: list[float | None],
    chain_tokens: list[str | None],
    ans_logprobs: list[float | None],
    ans_tokens: list[str | None],
    *,
    chain_min_percentile: float = 0.1,
    bottom_k: int = 3,
) -> tuple[float, float, float]:
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
    from think_budget_vllm_adapter.deer_think_budget.token_filter import cal_reasoning_zone_confidence, cal_value_min_confidence

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
