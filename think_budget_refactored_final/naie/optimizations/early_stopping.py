import re
import math
from typing import Optional, Tuple


def _normalize_for_sc(answer: str) -> str:
    answer = answer.strip().lower()
    answer = re.sub(r'[^\w一-鿿]', '', answer)
    return answer


def extract_answer_for_sc(content: str, close_tag: str = ''):
    """从内容中提取用于 Self-Consistency 的答案片段。"""
    after_think = content.split(close_tag)[-1] if close_tag and close_tag in content else content
    after_think = after_think.strip()
    json_match = re.search(r'\{[^}]+\}', after_think)
    if json_match:
        return json_match.group()
    letter_match = re.search(r'[A-D]', after_think[:50])
    if letter_match:
        return letter_match.group()
    return after_think[:100]


def check_consecutive_same(current_answer_text, prev_answer_text, consecutive_same_count,
                           consecutive_same_stop=999):
    if consecutive_same_stop >= 999:
        return prev_answer_text, consecutive_same_count
    if current_answer_text and prev_answer_text:
        if _normalize_for_sc(current_answer_text) == _normalize_for_sc(prev_answer_text):
            consecutive_same_count += 1
        else:
            consecutive_same_count = 0
    return current_answer_text, consecutive_same_count


def check_min_think_gate(actual_len, min_think_tokens):
    if min_think_tokens <= 0:
        return True
    return actual_len >= min_think_tokens


def make_accept_decision(decision_score, prob_th, actual_len, min_think_tokens,
                         is_paired, consecutive_same_count, consecutive_same_stop,
                         enable_value_min_confidence, decision_score_raw,
                         enable_full_chain_confidence=False,
                         reasoning_score_raw=0.0,
                         action_score_raw=0.0,
                         enable_dual_channel=False,
                         deer_should_early_stop=False,
                         deer_stable_count=0,
                         value_min_computed=True,
                         is_final=False):
    """早退判断 — 支持 value_min / full_chain / dual_channel / deer_trial 四种模式。

    融合模式: 多个开关同时启用时，各模式在 compute_dual_channel_score 中独立
    评估取 min，此处统一处理最终判定。

    DEER trial: 当 deer_should_early_stop=True 且 think_sufficient 时，作为独立的
    接受条件，与置信度门控并列——DEER 检测试验答案稳定，置信度门控检测概率确信度，
    二者互补而非互斥。

    value_min_computed: 指示 decision_score_raw 是否来自 value_min 计算。
        True = 正常计算, False = 退化回退(ans_tokens不可用)
    """
    think_sufficient = check_min_think_gate(actual_len, min_think_tokens)
    should_accept = False
    accept_reason = ''

    if decision_score >= prob_th and think_sufficient:
        should_accept = True
        if enable_full_chain_confidence:
            r_score = min(math.exp(reasoning_score_raw), 1.0)
            a_score = min(math.exp(action_score_raw), 1.0)
            accept_reason = f'full_chain_r{r_score:.2f}_a{a_score:.2f}'
        elif enable_dual_channel:
            r_score = min(math.exp(reasoning_score_raw), 1.0)
            a_score = min(math.exp(action_score_raw), 1.0)
            accept_reason = f'dual_ch_r{r_score:.2f}_a{a_score:.2f}'
        elif enable_value_min_confidence:
            if value_min_computed:
                accept_reason = f'value_min_conf_{decision_score_raw:.2f}'
            else:
                accept_reason = f'value_min_fallback_{decision_score:.2f}'
        else:
            accept_reason = 'confidence_above_threshold'
    elif decision_score >= prob_th and not think_sufficient:
        accept_reason = f'conf_ok_think_insufficient_{actual_len}_need_{min_think_tokens}'
    elif enable_full_chain_confidence and decision_score < prob_th and think_sufficient:
        r_score = min(math.exp(reasoning_score_raw), 1.0)
        a_score = min(math.exp(action_score_raw), 1.0)
        if r_score < a_score:
            accept_reason = f'chain_low_r{r_score:.2f}_a{a_score:.2f}'
        else:
            accept_reason = f'answer_low_r{r_score:.2f}_a{a_score:.2f}'
    elif enable_dual_channel and decision_score < prob_th and think_sufficient:
        r_score = min(math.exp(reasoning_score_raw), 1.0)
        a_score = min(math.exp(action_score_raw), 1.0)
        if r_score < a_score:
            accept_reason = f'reasoning_zone_low_{r_score:.2f}_a{a_score:.2f}'
        else:
            accept_reason = f'action_zone_low_{r_score:.2f}_a{a_score:.2f}'
    elif deer_should_early_stop and think_sufficient:
        # DEER trial 稳定检测: 连续多轮试验答案一致 → 即使置信度未达标也可接受
        should_accept = True
        accept_reason = f'deer_trial_stable_{deer_stable_count}'
    elif is_paired and think_sufficient:
        if is_final:
            should_accept = True
            accept_reason = 'think_tag_paired_final_accept'
        else:
            accept_reason = f'think_tag_paired_conf_low_{decision_score:.2f}'
    elif is_paired and not think_sufficient:
        accept_reason = f'think_tag_paired_insufficient_{actual_len}'
    elif consecutive_same_count >= consecutive_same_stop and think_sufficient:
        should_accept = True
        accept_reason = f'consecutive_same_{consecutive_same_count}'

    return should_accept, accept_reason
