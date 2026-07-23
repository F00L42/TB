"""安全门 — 超时回退逻辑。

超时后不再调API, 直接从已有answer_list取最佳答案返回。
无answer时关闭think标签返回已有prompt内容(不调API)。

倍率参数由 budget_config.yaml SAFETY_GATE 控制。
"""
import logging
from naie.models.confidence import is_think_tag_paired, get_think_closing_tag
from naie.config.unified_config import get_safety_gate_config
from naie.utils.tokenization import count_tokens

logger = logging.getLogger(__name__)

# ─── 从 YAML 加载安全门配置 ───
_SG_CFG = get_safety_gate_config()
_HARD_TRIGGER_MULT = _SG_CFG.get('hard_trigger_multiplier', 1.5)
_SOFT_TRIGGER_MULT = _SG_CFG.get('soft_trigger_multiplier', 1.0)


def safety_gate_check(elapsed_fn, baseline_time_limit, enable_safety_gate=True):
    if not enable_safety_gate:
        return False
    elapsed = elapsed_fn()
    if elapsed > baseline_time_limit * _HARD_TRIGGER_MULT:
        logger.warning(f"Safety gate HARD trigger: elapsed={elapsed:.1f}s > {baseline_time_limit * _HARD_TRIGGER_MULT:.1f}s")
        return True
    if elapsed > baseline_time_limit * _SOFT_TRIGGER_MULT:
        logger.info(f"Safety gate SOFT trigger: elapsed={elapsed:.1f}s > {baseline_time_limit * _SOFT_TRIGGER_MULT:.1f}s (allowing current round)")
        return False
    return False


def build_safety_fallback(current_prompt, answer_list, current_output_len,
                          current_actual_len, current_api_calls, current_thinking_rounds,
                          current_sc_rounds, soft_truncation=False):
    """构建安全门回退结果。

    Args:
        soft_truncation: 是否为软截断模式(accept_reason前缀不同)

    当 answer_list 非空时，取 decision_score 最高的答案；
    否则关闭 think 标签返回已有 prompt 内容(不调API)。
    """
    prefix = 'soft_truncation' if soft_truncation else 'safety_gate'
    if answer_list:
        final_result = max(answer_list, key=lambda x: x[5])
        content = final_result[0]
        if not is_think_tag_paired(content):
            close_tag = get_think_closing_tag(content)
            content = content + f"\n{close_tag}\n"
        return {
            'content': content,
            'token_num': final_result[1],
            'prompt_tokens': count_tokens(current_prompt),
            'confidence': final_result[2],
            'actual_len': current_actual_len,
            'perplexity': final_result[3],
            'accept_reason': f'{prefix}_fallback',
            'api_call_count': current_api_calls,
            'thinking_rounds': current_thinking_rounds,
            'sc_rounds': current_sc_rounds,
        }
    else:
        close_tag = get_think_closing_tag(current_prompt)
        content = current_prompt + f"\n{close_tag}\n"
        return {
            'content': content,
            'token_num': current_output_len,
            'prompt_tokens': count_tokens(current_prompt),
            'confidence': 0.0,
            'actual_len': current_actual_len,
            'perplexity': 0.0,
            'accept_reason': f'{prefix}_no_answers',
            'api_call_count': current_api_calls,
            'thinking_rounds': current_thinking_rounds,
            'sc_rounds': current_sc_rounds,
        }
