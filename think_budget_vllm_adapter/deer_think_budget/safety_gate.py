"""安全门 — 超时回退逻辑。

从 naie/optimizations/safety_gate.py 移植，自包含。

超时后不再调API, 直接从已有answer_list取最佳答案返回。
无answer时关闭think标签返回已有prompt内容(不调API)。

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

import logging

from think_budget_vllm_adapter.deer_think_budget.confidence import is_think_tag_paired, get_think_closing_tag

logger = logging.getLogger(__name__)


def safety_gate_check(
    elapsed_fn,
    baseline_time_limit: float,
    enable_safety_gate: bool = True,
) -> bool:
    """检查安全门是否触发。

    HARD trigger (>1.5x): 立即返回 True, 强制结束推理。
    SOFT trigger (>1.0x): 允许当前轮完成, 但下一轮检查时可能 HARD。

    Args:
        elapsed_fn: 返回已耗时的 callable
        baseline_time_limit: 基线时间限制 (秒)
        enable_safety_gate: 是否启用安全门
    """
    if not enable_safety_gate:
        return False
    elapsed = elapsed_fn()
    if elapsed > baseline_time_limit * 1.5:
        logger.warning(
            f"Safety gate HARD trigger: elapsed={elapsed:.1f}s > "
            f"{baseline_time_limit * 1.5:.1f}s"
        )
        return True
    if elapsed > baseline_time_limit:
        logger.info(
            f"Safety gate SOFT trigger: elapsed={elapsed:.1f}s > "
            f"{baseline_time_limit:.1f}s (allowing current round)"
        )
        return False
    return False


def build_safety_fallback(
    current_prompt: str,
    answer_list: list,
    current_output_len: int,
    current_actual_len: int,
    current_api_calls: int,
    current_thinking_rounds: int,
    current_sc_rounds: int,
    soft_truncation: bool = False,
) -> dict:
    """构建安全门回退结果。

    Args:
        soft_truncation: 是否为软截断模式 (accept_reason前缀不同)

    当 answer_list 非空时，取 decision_score 最高的答案；
    否则关闭 think 标签返回已有 prompt 内容 (不调API)。

    answer_list 元素格式:
        (content, output_len, confidence, perplexity, stop_reason,
         decision_score, value_min_raw, ln_score, accept_reason)
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
            'prompt_tokens': len(current_prompt) // 4,
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
            'prompt_tokens': len(current_prompt) // 4,
            'confidence': 0.0,
            'actual_len': current_actual_len,
            'perplexity': 0.0,
            'accept_reason': f'{prefix}_no_answers',
            'api_call_count': current_api_calls,
            'thinking_rounds': current_thinking_rounds,
            'sc_rounds': current_sc_rounds,
        }
