import time
import re
import math
import logging
from typing import Optional, Literal, List, Tuple, Dict, Any

from naie.models.confidence import (
    cal_confidence, cal_perplexity, is_think_tag_paired, get_think_closing_tag,
    reopen_think_tag,
)
from naie.models.model_client import model_client, async_model_call
from naie.optimizations.confidence_gates import (
    compute_dual_channel_score,
)
from naie.optimizations.early_stopping import (
    extract_answer_for_sc, check_consecutive_same,
    check_min_think_gate, make_accept_decision,
)
from naie.optimizations.context_control import (
    make_induced_prompt, apply_clean_context,
)
from naie.optimizations.safety_gate import (
    safety_gate_check, build_safety_fallback,
)
from naie.optimizations.complexity_analyzer import (
    estimate_complexity,
)
from naie.optimizations.difficulty_estimator import (
    estimate_difficulty, compute_budget_from_difficulty,
    count_repeat_phrases, detect_self_negation,
)
from naie.optimizations.scene_classifier import classify_scene, get_scene_typical_tokens
from naie.optimizations.trial_evaluator import TrialAnswerEvaluator
from naie.optimizations.task_type_budget import (
    classify_task_type, get_task_type_budget, register_new_task_type, is_known_task_type,
)
from naie.optimizations.history_budget import (
    compute_history_budget, detect_new_task_type, register_task_type_from_history,
)

logger = logging.getLogger(__name__)
# 文件顶部添加
_THINK_OPEN_TAG = '<think>'
_THINK_CLOSE_TAG = '</think>'

# 答案永远不被截断 — 最终答案生成使用此值，远超任何实际答案长度
# max_tokens 只控制推理 token，最终答案生成不受 max_tokens 限制
# 65536 是大多数 OpenAI 兼容 API 支持的上限，保证任何长度的答案都能完整输出
_UNLIMITED_ANSWER_TOKENS = 65536



_NATURAL_BREAK_CHARS = {'。', '！', '？', '.', '!', '?', '\n', '}', '】', '」'}


def _truncate_thinking_at_natural_breakpoint(content: str) -> str:
    """在推理过程的自然断点处截断推理，绝不截断答案。

    核心原则: max_tokens 只控制推理 token 用量，答案永远不被截断。
    仅当 content 中未包含 _THINK_CLOSE_TAG 时才执行截断 —
    此时说明推理被 max_tokens 截断（还在思考中），需要在推理内部
    找一个自然断点闭合，然后让模型继续生成完整答案。

    如果已经包含 _THINK_CLOSE_TAG，说明推理已完整结束，答案已跟在后面，不截断。
    """
    if _THINK_CLOSE_TAG in content:
        return content
    last_break = -1
    for i in range(len(content) - 1, max(len(content) - 500, -1), -1):
        if content[i] in _NATURAL_BREAK_CHARS:
            last_break = i + 1
            break
    if last_break > 0:
        truncated = content[:last_break]
        if _THINK_CLOSE_TAG not in truncated:
            truncated = truncated + f"\n{_THINK_CLOSE_TAG}\n"
        return truncated
    return content


def call_model(prompt, max_tokens,
               # ─── 核心参数 ───
               enable_confidence: Optional[bool] = False,
               stop_condition: Literal['length', 'thinking_words'] = 'length',
               check_length: Optional[int] = 200,
               think_stride: Optional[int] = 50,
               num_thinking: Optional[int] = 3,
               check_answer_len: Optional[int] = 20,
               model_name: str = 'minimax27',
               prob_th = 0.95,
               consecutive_same_stop: int = 2,
               # ─── P3.1 Value最低概率 ───
               enable_value_min_confidence: bool = False,
               value_min_bottom_k: int = 3,
               # ─── P3.3 Clean Context ───
               enable_clean_context: bool = True,
               # ─── 安全门 ───
               enable_safety_gate: bool = True,
               baseline_time_limit: float = 35.0,
               # ─── 路由参数 ───
               min_think_tokens: int = 0,
               max_api_calls: int = 0,
               reference: str = '',
               # ─── 难度估计器 (P5) ───
               enable_difficulty_estimator: bool = False,
               difficulty_check_length_base: int = 150,
               difficulty_check_length_max: int = 4000,
               difficulty_min_think_base: int = 100,
               difficulty_min_think_max: int = 4000,
               difficulty_stride_base: int = 50,
               difficulty_stride_max: int = 600,
               difficulty_num_thinking_base: int = 3,
               difficulty_num_thinking_max: int = 15,
               difficulty_answer_len_base: int = 500,
               difficulty_answer_len_max: int = 4000,
               # ─── DEER trial (P6) ───
               enable_deer_trial: bool = False,
               deer_trial_stable_rounds: int = 2,
               deer_trial_answer_len: int = 30,
               deer_confidence_boost: float = 0.05,
               # ─── 历史统计 ───
               history_avg_tokens: float = 0.0,
               history_failure_rate: float = 0.0,
               history_category_avg_tokens: float = 0.0,
               history_category_failure_rate: float = 0.0,
               history_count: int = 0,
               # ─── 软截断 ───
               enable_soft_truncation: bool = True,
               # ─── 双通道评估 ───
               enable_dual_channel: bool = False,
               reasoning_min_percentile: float = 0.1,
               # ─── 完整链置信度 ───
               enable_full_chain_confidence: bool = False,
               full_chain_min_percentile: float = 0.1,
               # ─── 任务类型判断 ───
               enable_task_type_budget: bool = True,
               default_task_type: str = 'general',
               custom_task_types_path: Optional[str] = None,
               task_type_override: str = '',
               # ─── 历史轨迹预算 ───
               enable_history_budget: bool = True,
               history_budget_multiplier: float = 1.2,
               history_low_success_boost: float = 1.3,
               history_stride_percentile: float = 0.75,
               new_task_unlimited: bool = True,
               auto_cluster_threshold: int = 5,
               history_warmup_count: int = 3,
               history_warmup_strategy: str = 'moderate',
               # ─── 人工可控超参 ───
               fixed_budget_tokens: int = 0,
               fixed_num_thinking: int = 0,
               fixed_think_stride: int = 0,
               fixed_check_length: int = 0,
               fixed_prob_th: float = 0.0,
               fixed_min_think_tokens: int = 0,
               # ─── 统一预算配置 — 4个核心暴露超参数 ───
               BUDGET_ENABLED: bool = True,
               FIXED_BUDGET_TOKEN: int = 0,
               CONFIDENCE_THRESHOLD: float = 0.95,
               PERCENTILE_STEP: float = 0.75,
               ):
    chat_client = model_client(model_name)

    output_len = 0
    actual_len = 0
    max_tokens_temp = max_tokens
    api_call_count = 0
    thinking_rounds = 0
    sc_rounds = 0
    budget_start_time = time.time()
    complexity_info = None

    # ═══════════════════════════════════════════
    # 统一预算优先级链 (inline path)
    # 优先级: BUDGET_ENABLED开关 > FIXED_BUDGET_TOKEN > fixed_budget_tokens
    #         > task_type_override > task_type_budget > difficulty_estimator
    #         > history_budget > fixed子参数
    # ═════════════════════════════

    # ─── Step 0: 统一预算配置 — BUDGET_ENABLED总开关 ───
    # BUDGET_ENABLED=False 时，整个预算控制系统关闭，模型自由生成
    if not BUDGET_ENABLED:
        enable_confidence = False
        enable_task_type_budget = False
        enable_history_budget = False
        enable_difficulty_estimator = False

    # ─── Step 0.5: 统一预算配置 — FIXED_BUDGET_TOKEN覆盖 ───
    # FIXED_BUDGET_TOKEN > 0 时覆盖 fixed_budget_tokens
    if FIXED_BUDGET_TOKEN > 0 and fixed_budget_tokens <= 0:
        fixed_budget_tokens = FIXED_BUDGET_TOKEN

    # ─── Step 1: 人工固定值覆盖(最高优先级) ───
    if fixed_budget_tokens > 0:
        check_length = min(fixed_budget_tokens, check_length)
        min_think_tokens = fixed_budget_tokens
        if fixed_num_thinking > 0:
            num_thinking = fixed_num_thinking
        else:
            num_thinking = max(3, fixed_budget_tokens // check_length)
        if fixed_think_stride > 0:
            think_stride = fixed_think_stride
        else:
            think_stride = max(50, fixed_budget_tokens // num_thinking)
        if fixed_check_length > 0:
            check_length = fixed_check_length
        if fixed_min_think_tokens > 0:
            min_think_tokens = fixed_min_think_tokens
        if fixed_prob_th > 0.0:
            prob_th = fixed_prob_th
        # 跳过所有动态预算计算
    else:
        # ─── Step 2: 任务类型判断 ───
        scene_info_inline = classify_scene(prompt)
        scene_type_inline = scene_info_inline.get('scene_type', 'general')
        task_type_inline = default_task_type

        if enable_task_type_budget:
            if task_type_override:
                task_type_inline = task_type_override
                # 动态注册: 如果 task_type_override 指定的类型未知，自动注册
                if not is_known_task_type(task_type_override):
                    register_new_task_type(task_type_override,
                                           typical_tokens=3000,
                                           description=f'手动指定动态注册 — {task_type_override}',
                                           json_path=custom_task_types_path)
            else:
                task_info_inline = classify_task_type(prompt, scene_info_inline)
                task_type_inline = task_info_inline.get('task_type', 'general')
                # 动态注册: 推断出的类型如果未知，自动注册(不会抛出 KeyError)
                if not is_known_task_type(task_type_inline):
                    register_new_task_type(task_type_inline,
                                           typical_tokens=3000,
                                           description=f'推断动态注册 — {task_type_inline}',
                                           json_path=custom_task_types_path)

            # 应用任务类型基础预算
            task_budget_inline = get_task_type_budget(task_type_inline, custom_task_types_path)
            check_length = task_budget_inline.get('check_length', check_length)
            think_stride = task_budget_inline.get('think_stride', think_stride)
            num_thinking = task_budget_inline.get('num_thinking', num_thinking)
            min_think_tokens = task_budget_inline.get('min_think_tokens', min_think_tokens)
            check_answer_len = task_budget_inline.get('check_answer_len', check_answer_len)
            prob_th = task_budget_inline.get('prob_th', prob_th)

        # ─── Step 3: 难度估计器 ───
        if enable_difficulty_estimator:
            scene_typical_tokens_inline = scene_info_inline.get('typical_tokens', 0) if enable_task_type_budget else classify_scene(prompt).get('typical_tokens', 0)
            scene_confidence_inline = scene_info_inline.get('scene_confidence', 0.0) if enable_task_type_budget else classify_scene(prompt).get('scene_confidence', 0.0)

            diff_info = estimate_difficulty(
                prompt, reference,
                history_avg_tokens=history_avg_tokens,
                history_failure_rate=history_failure_rate,
                history_category_avg_tokens=history_category_avg_tokens,
                history_category_failure_rate=history_category_failure_rate,
                history_count=history_count,
                scene_type=scene_type_inline if enable_task_type_budget else classify_scene(prompt).get('scene_type', 'general'),
                scene_typical_tokens=scene_typical_tokens_inline,
                scene_confidence=scene_confidence_inline,
            )
            diff_budget = compute_budget_from_difficulty(
                diff_info.get('difficulty', 0.5),
                base_check_length=difficulty_check_length_base,
                max_check_length=difficulty_check_length_max,
                base_min_think=difficulty_min_think_base,
                max_min_think=difficulty_min_think_max,
                base_stride=difficulty_stride_base,
                max_stride=difficulty_stride_max,
                base_num_thinking=difficulty_num_thinking_base,
                max_num_thinking=difficulty_num_thinking_max,
                base_answer_len=difficulty_answer_len_base,
                max_answer_len=difficulty_answer_len_max,
            )
            check_length = diff_budget['check_length']
            min_think_tokens = diff_budget['min_think_tokens']
            think_stride = diff_budget['think_stride']
            num_thinking = diff_budget['num_thinking']
            check_answer_len = diff_budget.get('check_answer_len', check_answer_len)
            complexity_info = diff_info

        # ─── Step 4: 历史轨迹预算 ───
        if enable_history_budget:
            history_cfg_inline = {
                'history_budget_multiplier': history_budget_multiplier,
                'history_low_success_boost': history_low_success_boost,
                'history_stride_percentile': history_stride_percentile,
                'new_task_unlimited': new_task_unlimited,
                'auto_cluster_threshold': auto_cluster_threshold,
                'history_warmup_count': history_warmup_count,
                'history_warmup_strategy': history_warmup_strategy,
                'custom_task_types_path': custom_task_types_path,
            }
            h_budget = compute_history_budget(
                prompt, task_type=task_type_inline if enable_task_type_budget else 'general',
                scene_type=scene_type_inline if enable_task_type_budget else '',
                config=history_cfg_inline)
            budget_mode = h_budget.get('budget_mode', 'cold_start')

            if budget_mode == 'unlimited':
                num_thinking = h_budget.get('num_thinking', 99)
                min_think_tokens = h_budget.get('min_think_tokens', 0)
            elif budget_mode in ('warm_start', 'hot_start'):
                if not enable_difficulty_estimator:
                    check_length = h_budget.get('check_length', check_length)
                    min_think_tokens = h_budget.get('min_think_tokens', min_think_tokens)
                    num_thinking = h_budget.get('num_thinking', num_thinking)
                # 步长始终可被历史百分位覆盖
                h_stride = h_budget.get('think_stride', 0)
                if h_stride > 0:
                    if budget_mode == 'hot_start':
                        think_stride = h_stride
                    else:
                        think_stride = max(think_stride, h_stride)

            # ─── 自动注册新任务类型 ───
            # 当检测到新任务类型(is_new_task=True)且历史积累达到阈值时，
            # 自动将该任务类型注册到 custom_task_types.json，后续同类问题
            # 将使用该类型的历史统计进行 budget 推算，而非无限制执行。
            if h_budget.get('is_new_task', False) and auto_cluster_threshold > 0:
                try:
                    registered = register_task_type_from_history(
                        task_type=task_type_inline if enable_task_type_budget else 'general',
                        scene_type=scene_type_inline if enable_task_type_budget else '',
                        json_path=custom_task_types_path,
                        min_count=auto_cluster_threshold,
                    )
                    if registered:
                        logger.info(f"自动注册新任务类型: {task_type_inline} (阈值={auto_cluster_threshold})")
                except Exception as e:
                    logger.debug(f"自动注册任务类型失败: {e}")

        # ─── Step 5: 人工固定子参数覆盖 ───
        if fixed_num_thinking > 0:
            num_thinking = fixed_num_thinking
        if fixed_think_stride > 0:
            think_stride = fixed_think_stride
        if fixed_check_length > 0:
            check_length = fixed_check_length
        if fixed_min_think_tokens > 0:
            min_think_tokens = fixed_min_think_tokens
        if fixed_prob_th > 0.0:
            prob_th = fixed_prob_th

    # ─── 统一预算配置 — CONFIDENCE_THRESHOLD & PERCENTILE_STEP 覆盖 ───
    # CONFIDENCE_THRESHOLD 覆盖 prob_th(如果未被 fixed_prob_th 覆盖)
    if fixed_prob_th <= 0.0 and CONFIDENCE_THRESHOLD != 0.95:
        prob_th = CONFIDENCE_THRESHOLD
    # PERCENTILE_STEP 覆盖 history_stride_percentile
    if PERCENTILE_STEP != 0.75 and enable_history_budget:
        history_stride_percentile = PERCENTILE_STEP

    trial_evaluator = None
    if enable_deer_trial:
        trial_evaluator = TrialAnswerEvaluator({
            'enable_deer_trial': True,
            'deer_trial_stable_rounds': deer_trial_stable_rounds,
            'deer_trial_answer_len': deer_trial_answer_len,
            'deer_confidence_boost': deer_confidence_boost,
        })
        trial_evaluator.reset()

    complexity_info = estimate_complexity(prompt, reference)

    def _elapsed():
        return time.time() - budget_start_time

    # Budget 模式下安全门时间限制自动扩增：
    # baseline_time_limit 基于单次 API 调用的 baseline 延迟设定，
    # 但 Budget 模式每题需要 num_thinking × 2 + 1 次 API 调用，
    # 因此安全门阈值需按 API 调用倍数扩增，否则正常的多轮推理也会被误杀。
    # 增益系数: 1 + 0.5 × min(num_thinking, 4)，上限 3.0×
    if enable_confidence:
        _budget_amplify = 1.0 + 0.5 * min(num_thinking, 4)
        _effective_time_limit = min(baseline_time_limit * _budget_amplify, baseline_time_limit * 3.0)
    else:
        _effective_time_limit = baseline_time_limit

    def _call(p, max_tok, stop=None):
        nonlocal api_call_count
        if max_api_calls > 0 and api_call_count >= max_api_calls:
            return {'content': '', 'token_num': 0, 'logprobs': None, 'tokens': None, 'stop_reason': 'length', 'prompt_tokens': 0}
        api_call_count += 1
        return chat_client.model_call(p, max_tok, stop)

    def _safety_fallback(current_prompt, answer_list, current_output_len, current_actual_len,
                         current_api_calls, current_thinking_rounds, current_sc_rounds):
        return build_safety_fallback(current_prompt, answer_list,
                                     current_output_len, current_actual_len,
                                     current_api_calls, current_thinking_rounds, current_sc_rounds,
                                     soft_truncation=enable_soft_truncation)

       
    if not enable_confidence:
        # 无 confidence 模式 — 答案不受 max_tokens 限制，给足 token 确保完整输出
        result = chat_client.model_call(prompt, _UNLIMITED_ANSWER_TOKENS)
        content = result.get('content', '')
        full_content = prompt + content
        return {
            'content': full_content,
            'token_num': result.get('token_num', 0),
            'prompt_tokens': result.get('prompt_tokens', 0),
            'confidence': 0.0,
            'actual_len': result.get('token_num', 0),
            'perplexity': 0.0,
            'accept_reason': 'baseline_free_generation',
            'api_call_count': 1,
            'thinking_rounds': 0,
            'sc_rounds': 0,
            'complexity': None,
        }
    prompt = "<|im_start|>user\n" + prompt + "<|im_end|>\n<|im_start|>assistant\n" + _THINK_OPEN_TAG + "\n"
    
    if stop_condition == 'length':
        thinking_words = None
        max_tokens_thinking = min(check_length, max_tokens)
    elif stop_condition == 'thinking_words':
        thinking_words = ['等一下', '等等', '不过', '此外', '但是', 'Wait', 'wait', 'Hmm', 'hmm', 'Alternatively',
                          'alternatively', 'However', 'however']
        max_tokens_thinking = max_tokens
    else:
        raise ValueError("stop_condition must be 'length' or 'thinking_words'")

    result = _call(prompt, max_tokens_thinking, thinking_words)
    o = result['content']
    token_num = result['token_num']
    stop_reason = result['stop_reason']
    logprobs = result['logprobs']
    tokens = result.get('tokens')

    # 初始思考被截断时，在自然断点处闭合(soft_truncation语义)
    if enable_soft_truncation and stop_reason == 'length' and _THINK_CLOSE_TAG not in o:
        o = _truncate_thinking_at_natural_breakpoint(o)

    output_len += token_num
    actual_len += token_num
    max_tokens_temp -= token_num
    prompt += o

    if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
        return _safety_fallback(prompt, [], output_len, actual_len, api_call_count, thinking_rounds, sc_rounds)

    answer_list = []
    prev_answer_text = None
    consecutive_same_count = 0
    # 双通道: 跟踪最近一轮推理的 logprobs/tokens
    reasoning_logprobs = logprobs
    reasoning_tokens = tokens

    if enable_confidence:
        for i in range(num_thinking):
            if max_tokens_temp <= 0:
                break
            if max_api_calls > 0 and api_call_count >= max_api_calls:
                break
            if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
                return _safety_fallback(prompt, answer_list, output_len, actual_len, api_call_count, thinking_rounds, sc_rounds)

            if is_think_tag_paired(prompt):
                if stop_reason == 'stop':
                    if actual_len < min_think_tokens:
                        prompt = reopen_think_tag(prompt)
                        result = _call(prompt, think_stride, thinking_words)
                        thinking_rounds += 1
                        o = result['content']
                        token_num = result['token_num']
                        stop_reason = result['stop_reason']
                        logprobs = result['logprobs']
                        tokens = result.get('tokens')
                        # 继续思考被截断时，在自然断点处闭合
                        if enable_soft_truncation and stop_reason == 'length' and _THINK_CLOSE_TAG not in o:
                            o = _truncate_thinking_at_natural_breakpoint(o)
                        prompt += o
                        output_len += token_num
                        actual_len += token_num
                        max_tokens_temp -= token_num
                        continue
                    else:
                        logger.debug(f"think_tag_paired at round {i+1}, actual_len={actual_len}, falling through to check answer")
                induced_prompt = prompt
            else:
                if stop_reason == 'stop' and actual_len < min_think_tokens:
                    result = _call(prompt, think_stride, thinking_words)
                    thinking_rounds += 1
                    o = result['content']
                    token_num = result['token_num']
                    stop_reason = result['stop_reason']
                    logprobs = result['logprobs']
                    tokens = result.get('tokens')
                    # 继续思考被截断时，在自然断点处闭合
                    if enable_soft_truncation and stop_reason == 'length' and _THINK_CLOSE_TAG not in o:
                        o = _truncate_thinking_at_natural_breakpoint(o)
                    prompt += o
                    output_len += token_num
                    actual_len += token_num
                    max_tokens_temp -= token_num
                    continue
                induced_prompt = make_induced_prompt(prompt)

            result = _call(induced_prompt, check_answer_len)
            o = result['content']
            token_num = result['token_num']
            ans_logprobs = result['logprobs']
            ans_tokens = result.get('tokens')
            stop_reason = result['stop_reason']
            confidence = cal_confidence(ans_logprobs)
            perplexity = cal_perplexity(ans_logprobs)

            if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
                answer_list.append((induced_prompt + o, output_len + token_num, confidence, perplexity, stop_reason,
                                    0.0, 0.0, 0.0, 'safety_gate_fallback'))
                return _safety_fallback(prompt, answer_list, output_len + token_num, actual_len + token_num,
                                        api_call_count, thinking_rounds, sc_rounds)

            decision_score, value_min_raw, reasoning_score_raw, action_score_raw = compute_dual_channel_score(
                reasoning_logprobs, reasoning_tokens,
                ans_logprobs, ans_tokens, confidence, perplexity,
                enable_full_chain_confidence=enable_full_chain_confidence,
                full_chain_min_percentile=full_chain_min_percentile,
                enable_dual_channel=enable_dual_channel,
                reasoning_min_percentile=reasoning_min_percentile,
                enable_value_min_confidence=enable_value_min_confidence,
                value_min_bottom_k=value_min_bottom_k,
            )
            ln_score = 0.0

            current_answer_text = extract_answer_for_sc(induced_prompt + o)
            prev_answer_text, consecutive_same_count = check_consecutive_same(
                current_answer_text, prev_answer_text, consecutive_same_count, consecutive_same_stop)

            trial_info = None
            if trial_evaluator is not None:
                trial_info = trial_evaluator.evaluate_trial(induced_prompt + o, confidence)
                if trial_info.get('boosted_confidence') and trial_info['boosted_confidence'] > confidence:
                    confidence = trial_info['boosted_confidence']
                    decision_score = min(decision_score + deer_confidence_boost, 1.0)
                if trial_info.get('should_early_stop'):
                    should_accept = True
                    accept_reason = f'deer_trial_stable_{trial_info["stable_count"]}'

            should_accept, accept_reason = make_accept_decision(
                decision_score, prob_th, actual_len, min_think_tokens,
                is_think_tag_paired(prompt), consecutive_same_count, consecutive_same_stop,
                enable_value_min_confidence, value_min_raw,
                enable_full_chain_confidence=enable_full_chain_confidence,
                reasoning_score_raw=reasoning_score_raw,
                action_score_raw=action_score_raw,
                enable_dual_channel=enable_dual_channel,
            )

            if should_accept:
                answer = induced_prompt + o
                output_len += token_num
                answer_list.append((answer, output_len, confidence, perplexity, stop_reason,
                                    decision_score, value_min_raw, ln_score, accept_reason))
                final_result = max(answer_list, key=lambda x: x[5])

                if final_result[4] == 'stop' and is_think_tag_paired(final_result[0]):
                    content = final_result[0]
                    output_len = final_result[1]
                elif enable_soft_truncation:
                    result2 = _call(final_result[0], _UNLIMITED_ANSWER_TOKENS)
                    content = _truncate_thinking_at_natural_breakpoint(final_result[0] + result2['content'])
                    output_len = final_result[1] + result2['token_num']
                    actual_len += result2['token_num']
                else:
                    result2 = _call(final_result[0], _UNLIMITED_ANSWER_TOKENS)
                    content = final_result[0] + result2['content']
                    output_len = final_result[1] + result2['token_num']
                    actual_len += result2['token_num']
                ret = {
                    'content': content,
                    'token_num': output_len,
                    'prompt_tokens': len(prompt) // 4,
                    'confidence': final_result[2],
                    'actual_len': actual_len,
                    'perplexity': final_result[3],
                    'accept_reason': final_result[8],
                    'api_call_count': api_call_count,
                    'thinking_rounds': thinking_rounds,
                    'sc_rounds': sc_rounds,
                }
                if enable_value_min_confidence:
                    ret['value_min_confidence'] = final_result[6]
                ret['complexity'] = complexity_info
                # 返回推断的任务类型和场景类型，供下游记录历史和调试
                if enable_task_type_budget:
                    ret['task_type'] = task_type_inline
                    ret['scene_type'] = scene_type_inline
                return ret

            answer_list.append((induced_prompt + o, output_len + token_num, confidence, perplexity, stop_reason,
                                decision_score, value_min_raw, ln_score, 'continue'))

            prompt, output_len = apply_clean_context(
                enable_clean_context, prompt, induced_prompt, o, output_len, token_num)

            stride = min(think_stride, max_tokens_temp)

            result = _call(prompt, stride, thinking_words)
            thinking_rounds += 1
            o = result['content']
            token_num = result['token_num']
            stop_reason = result['stop_reason']
            logprobs = result['logprobs']
            tokens = result.get('tokens')
            # 继续思考被stride截断时，在自然断点处闭合
            if enable_soft_truncation and stop_reason == 'length' and _THINK_CLOSE_TAG not in o:
                o = _truncate_thinking_at_natural_breakpoint(o)
            prompt += o
            output_len += token_num
            actual_len += token_num
            max_tokens_temp -= token_num
            # 双通道: 更新最近一轮推理的 logprobs/tokens
            reasoning_logprobs = logprobs
            reasoning_tokens = tokens

            if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
                return _safety_fallback(prompt, answer_list, output_len, actual_len, api_call_count, thinking_rounds, sc_rounds)

    if not is_think_tag_paired(prompt):
        prompt = make_induced_prompt(prompt)

    # 循环结束后的最终答案生成 — 不限 token，让模型输出完整答案
    # check_answer_len 仅用于循环中试探答案（置信度评估），最终答案必须完整
    result = _call(prompt, _UNLIMITED_ANSWER_TOKENS)
    o = result['content']
    token_num = result['token_num']
    logprobs = result['logprobs']
    ans_tokens = result.get('tokens')
    stop_reason = result['stop_reason']

    confidence = cal_confidence(logprobs)
    perplexity = cal_perplexity(logprobs)
    output_len += token_num
    actual_len += token_num

    if enable_confidence:
        decision_score, value_min_raw, _rsr_fb, _asr_fb = compute_dual_channel_score(
            reasoning_logprobs, reasoning_tokens,
            logprobs, ans_tokens, confidence, perplexity,
            enable_full_chain_confidence=enable_full_chain_confidence,
            full_chain_min_percentile=full_chain_min_percentile,
            enable_dual_channel=enable_dual_channel,
            reasoning_min_percentile=reasoning_min_percentile,
            enable_value_min_confidence=enable_value_min_confidence,
            value_min_bottom_k=value_min_bottom_k,
        )
        ln_score = 0.0
        answer_list.append((prompt + o, output_len, confidence, perplexity, stop_reason,
                            decision_score, value_min_raw, ln_score, 'final_fallback'))
        final_result = max(answer_list, key=lambda x: x[5])

        if final_result[4] == 'stop' and is_think_tag_paired(final_result[0]):
            content = final_result[0]
            output_len = final_result[1]
        elif enable_soft_truncation:
            result2 = _call(final_result[0], _UNLIMITED_ANSWER_TOKENS)
            content = _truncate_thinking_at_natural_breakpoint(final_result[0] + result2['content'])
            output_len = final_result[1] + result2['token_num']
            actual_len += result2['token_num']
        else:
            result2 = _call(final_result[0], _UNLIMITED_ANSWER_TOKENS)
            content = final_result[0] + result2['content']
            output_len = final_result[1] + result2['token_num']
            actual_len += result2['token_num']
        return {
            'content': content,
            'token_num': output_len,
            'prompt_tokens': len(prompt) // 4,
            'confidence': final_result[2],
            'actual_len': actual_len,
            'perplexity': final_result[3],
            'accept_reason': final_result[8],
            'api_call_count': api_call_count,
            'thinking_rounds': thinking_rounds,
            'sc_rounds': sc_rounds,
            'value_min_confidence': final_result[6] if enable_value_min_confidence else None,
            'complexity': complexity_info,
        }
    else:
        return {
            'content': prompt + o,
            'token_num': output_len,
            'prompt_tokens': len(prompt) // 4,
            'confidence': confidence,
            'actual_len': actual_len,
            'perplexity': perplexity,
            'complexity': complexity_info,
        }


# ══════════════════════════════════════════════════════
# 异步版 call_model — 与 call_model 完全并行的实现
# ══════════════════════════════════════════════════════


async def call_model_async(prompt, max_tokens,
               # ─── 核心参数 ───
               enable_confidence: Optional[bool] = False,
               stop_condition: Literal['length', 'thinking_words'] = 'length',
               check_length: Optional[int] = 200,
               think_stride: Optional[int] = 50,
               num_thinking: Optional[int] = 3,
               check_answer_len: Optional[int] = 20,
               model_name: str = 'minimax27',
               prob_th = 0.95,
               consecutive_same_stop: int = 2,
               # ─── P3.1 Value最低概率 ───
               enable_value_min_confidence: bool = False,
               value_min_bottom_k: int = 3,
               # ─── P3.3 Clean Context ───
               enable_clean_context: bool = True,
               # ─── 安全门 ───
               enable_safety_gate: bool = True,
               baseline_time_limit: float = 35.0,
               # ─── 路由参数 ───
               min_think_tokens: int = 0,
               max_api_calls: int = 0,
               reference: str = '',
               # ─── 难度估计器 (P5) ───
               enable_difficulty_estimator: bool = False,
               difficulty_check_length_base: int = 150,
               difficulty_check_length_max: int = 4000,
               difficulty_min_think_base: int = 100,
               difficulty_min_think_max: int = 4000,
               difficulty_stride_base: int = 50,
               difficulty_stride_max: int = 600,
               difficulty_num_thinking_base: int = 3,
               difficulty_num_thinking_max: int = 15,
               difficulty_answer_len_base: int = 500,
               difficulty_answer_len_max: int = 4000,
               # ─── DEER trial (P6) ───
               enable_deer_trial: bool = False,
               deer_trial_stable_rounds: int = 2,
               deer_trial_answer_len: int = 30,
               deer_confidence_boost: float = 0.05,
               # ─── 历史统计 ───
               history_avg_tokens: float = 0.0,
               history_failure_rate: float = 0.0,
               history_category_avg_tokens: float = 0.0,
               history_category_failure_rate: float = 0.0,
               history_count: int = 0,
               # ─── 软截断 ───
               enable_soft_truncation: bool = True,
               # ─── 双通道评估 ───
               enable_dual_channel: bool = False,
               reasoning_min_percentile: float = 0.1,
               # ─── 完整链置信度 ───
               enable_full_chain_confidence: bool = False,
               full_chain_min_percentile: float = 0.1,
               # ─── 任务类型判断 ───
               enable_task_type_budget: bool = True,
               default_task_type: str = 'general',
               custom_task_types_path: Optional[str] = None,
               task_type_override: str = '',
               # ─── 历史轨迹预算 ───
               enable_history_budget: bool = True,
               history_budget_multiplier: float = 1.2,
               history_low_success_boost: float = 1.3,
               history_stride_percentile: float = 0.75,
               new_task_unlimited: bool = True,
               auto_cluster_threshold: int = 5,
               history_warmup_count: int = 3,
               history_warmup_strategy: str = 'moderate',
               # ─── 人工可控超参 ───
               fixed_budget_tokens: int = 0,
               fixed_num_thinking: int = 0,
               fixed_think_stride: int = 0,
               fixed_check_length: int = 0,
               fixed_prob_th: float = 0.0,
               fixed_min_think_tokens: int = 0,
               # ─── 统一预算配置 — 4个核心暴露超参数 ───
               BUDGET_ENABLED: bool = True,
               FIXED_BUDGET_TOKEN: int = 0,
               CONFIDENCE_THRESHOLD: float = 0.95,
               PERCENTILE_STEP: float = 0.75,
               ):
    """call_model 的异步版 — 使用 httpx.AsyncClient 替代同步 model_client。

    业务逻辑与 call_model 完全一致, 仅将同步 API 调用替换为异步。
    参数列表与 call_model 完全一致, 方便 naie/think_budget.py 无缝切换。
    """
    output_len = 0
    actual_len = 0
    max_tokens_temp = max_tokens
    api_call_count = 0
    thinking_rounds = 0
    sc_rounds = 0
    budget_start_time = time.time()
    complexity_info = None

    # ─── 统一预算优先级链 (与 call_model 同步版完全一致) ───
    # 优先级: fixed_budget_tokens > task_type_override > task_type_budget >
    #          trajectory > difficulty > history > fixed sub-params
    if fixed_budget_tokens > 0:
        # Step 1: 固定 budget token 覆盖一切
        check_length = min(fixed_budget_tokens, check_length)
        min_think_tokens = fixed_budget_tokens
        if fixed_num_thinking > 0:
            num_thinking = fixed_num_thinking
        else:
            num_thinking = max(3, fixed_budget_tokens // check_length)
        if fixed_think_stride > 0:
            think_stride = fixed_think_stride
        else:
            think_stride = max(50, fixed_budget_tokens // num_thinking)
        if fixed_check_length > 0:
            check_length = fixed_check_length
        if fixed_min_think_tokens > 0:
            min_think_tokens = fixed_min_think_tokens
        if fixed_prob_th > 0.0:
            prob_th = fixed_prob_th
    else:
        # ─── Step 2: 任务类型判断 ───
        scene_info_async = classify_scene(prompt)
        scene_type_async = scene_info_async.get('scene_type', 'general')
        task_type_async = default_task_type

        if enable_task_type_budget:
            if task_type_override:
                task_type_async = task_type_override
            else:
                task_info_async = classify_task_type(prompt, scene_info_async)
                task_type_async = task_info_async.get('task_type', 'general')

            # 应用任务类型基础预算
            task_budget_async = get_task_type_budget(task_type_async, custom_task_types_path)
            check_length = task_budget_async.get('check_length', check_length)
            think_stride = task_budget_async.get('think_stride', think_stride)
            num_thinking = task_budget_async.get('num_thinking', num_thinking)
            min_think_tokens = task_budget_async.get('min_think_tokens', min_think_tokens)
            check_answer_len = task_budget_async.get('check_answer_len', check_answer_len)
            prob_th = task_budget_async.get('prob_th', prob_th)

        # ─── Step 3: 难度估计器 ───
        if enable_difficulty_estimator:
            scene_typical_tokens_async = scene_info_async.get('typical_tokens', 0) if enable_task_type_budget else classify_scene(prompt).get('typical_tokens', 0)
            scene_confidence_async = scene_info_async.get('scene_confidence', 0.0) if enable_task_type_budget else classify_scene(prompt).get('scene_confidence', 0.0)

            diff_info = estimate_difficulty(
                prompt, reference,
                history_avg_tokens=history_avg_tokens,
                history_failure_rate=history_failure_rate,
                history_category_avg_tokens=history_category_failure_rate,
                history_count=history_count,
                scene_type=scene_type_async if enable_task_type_budget else classify_scene(prompt).get('scene_type', 'general'),
                scene_typical_tokens=scene_typical_tokens_async,
                scene_confidence=scene_confidence_async,
            )
            diff_budget = compute_budget_from_difficulty(
                diff_info.get('difficulty', 0.5),
                base_check_length=difficulty_check_length_base,
                max_check_length=difficulty_check_length_max,
                base_min_think=difficulty_min_think_base,
                max_min_think=difficulty_min_think_max,
                base_stride=difficulty_stride_base,
                max_stride=difficulty_stride_max,
                base_num_thinking=difficulty_num_thinking_base,
                max_num_thinking=difficulty_num_thinking_max,
                base_answer_len=difficulty_answer_len_base,
                max_answer_len=difficulty_answer_len_max,
            )
            check_length = diff_budget['check_length']
            min_think_tokens = diff_budget['min_think_tokens']
            think_stride = diff_budget['think_stride']
            num_thinking = diff_budget['num_thinking']
            check_answer_len = diff_budget.get('check_answer_len', check_answer_len)
            complexity_info = diff_info

        # ─── Step 4: 历史轨迹预算 ───
        if enable_history_budget:
            history_cfg_async = {
                'history_budget_multiplier': history_budget_multiplier,
                'history_low_success_boost': history_low_success_boost,
                'history_stride_percentile': history_stride_percentile,
                'new_task_unlimited': new_task_unlimited,
                'auto_cluster_threshold': auto_cluster_threshold,
                'history_warmup_count': history_warmup_count,
                'history_warmup_strategy': history_warmup_strategy,
                'custom_task_types_path': custom_task_types_path,
            }
            h_budget = compute_history_budget(
                prompt, task_type=task_type_async if enable_task_type_budget else 'general',
                scene_type=scene_type_async if enable_task_type_budget else '',
                config=history_cfg_async)
            budget_mode = h_budget.get('budget_mode', 'cold_start')

            if budget_mode == 'unlimited':
                num_thinking = h_budget.get('num_thinking', 99)
                min_think_tokens = h_budget.get('min_think_tokens', 0)
            elif budget_mode in ('warm_start', 'hot_start'):
                if not enable_difficulty_estimator:
                    check_length = h_budget.get('check_length', check_length)
                    min_think_tokens = h_budget.get('min_think_tokens', min_think_tokens)
                    num_thinking = h_budget.get('num_thinking', num_thinking)
                h_stride = h_budget.get('think_stride', 0)
                if h_stride > 0:
                    if budget_mode == 'hot_start':
                        think_stride = h_stride
                    else:
                        think_stride = max(think_stride, h_stride)

            # 自动注册新任务类型
            if h_budget.get('is_new_task', False) and auto_cluster_threshold > 0:
                try:
                    registered = register_task_type_from_history(
                        task_type=task_type_async if enable_task_type_budget else 'general',
                        scene_type=scene_type_async if enable_task_type_budget else '',
                        json_path=custom_task_types_path,
                        min_count=auto_cluster_threshold,
                    )
                    if registered:
                        logger.info(f"自动注册新任务类型: {task_type_async} (阈值={auto_cluster_threshold})")
                except Exception as e:
                    logger.debug(f"自动注册任务类型失败: {e}")

        # ─── Step 5: 人工固定子参数覆盖 ───
        if fixed_num_thinking > 0:
            num_thinking = fixed_num_thinking
        if fixed_think_stride > 0:
            think_stride = fixed_think_stride
        if fixed_check_length > 0:
            check_length = fixed_check_length
        if fixed_min_think_tokens > 0:
            min_think_tokens = fixed_min_think_tokens
        if fixed_prob_th > 0.0:
            prob_th = fixed_prob_th

    # ─── 统一预算配置 — CONFIDENCE_THRESHOLD & PERCENTILE_STEP 覆盖 ───
    # CONFIDENCE_THRESHOLD 覆盖 prob_th(如果未被 fixed_prob_th 覆盖)
    if fixed_prob_th <= 0.0 and CONFIDENCE_THRESHOLD != 0.95:
        prob_th = CONFIDENCE_THRESHOLD
    # PERCENTILE_STEP 覆盖 history_stride_percentile
    if PERCENTILE_STEP != 0.75 and enable_history_budget:
        history_stride_percentile = PERCENTILE_STEP

    trial_evaluator = None
    if enable_deer_trial:
        trial_evaluator = TrialAnswerEvaluator({
            'enable_deer_trial': True,
            'deer_trial_stable_rounds': deer_trial_stable_rounds,
            'deer_trial_answer_len': deer_trial_answer_len,
            'deer_confidence_boost': deer_confidence_boost,
        })
        trial_evaluator.reset()

    complexity_info = estimate_complexity(prompt, reference)

    def _elapsed():
        return time.time() - budget_start_time

    # Budget 模式下安全门时间限制自动扩增（与同步版相同逻辑）
    if enable_confidence:
        _budget_amplify = 1.0 + 0.5 * min(num_thinking, 4)
        _effective_time_limit = min(baseline_time_limit * _budget_amplify, baseline_time_limit * 3.0)
    else:
        _effective_time_limit = baseline_time_limit

    async def _acall(p, max_tok, stop=None):
        nonlocal api_call_count
        if max_api_calls > 0 and api_call_count >= max_api_calls:
            return {'content': '', 'token_num': 0, 'logprobs': None, 'tokens': None,
                    'stop_reason': 'length', 'prompt_tokens': 0}
        api_call_count += 1
        return await async_model_call(model_name, p, max_tok, stop)

    def _safety_fallback(current_prompt, answer_list, current_output_len, current_actual_len,
                         current_api_calls, current_thinking_rounds, current_sc_rounds):
        return build_safety_fallback(current_prompt, answer_list,
                                     current_output_len, current_actual_len,
                                     current_api_calls, current_thinking_rounds, current_sc_rounds,
                                     soft_truncation=enable_soft_truncation)

    if not enable_confidence:
        # 无 confidence 模式 — 答案不受 max_tokens 限制，给足 token 确保完整输出
        result = await async_model_call(model_name, prompt, _UNLIMITED_ANSWER_TOKENS)
        content = result.get('content', '')
        full_content = prompt + content
        return {
            'content': full_content,
            'token_num': result.get('token_num', 0),
            'prompt_tokens': result.get('prompt_tokens', 0),
            'confidence': 0.0,
            'actual_len': result.get('token_num', 0),
            'perplexity': 0.0,
            'accept_reason': 'baseline_free_generation',
            'api_call_count': 1,
            'thinking_rounds': 0,
            'sc_rounds': 0,
            'complexity': None,
        }

    prompt = "<|im_start|>user\n" + prompt + "<|im_end|>\n<|im_start|>assistant\n" + _THINK_OPEN_TAG + "\n"
    if stop_condition == 'length':
        thinking_words = None
        max_tokens_thinking = min(check_length, max_tokens)
    elif stop_condition == 'thinking_words':
        thinking_words = ['等一下', '等等', '不过', '此外', '但是', 'Wait', 'wait', 'Hmm', 'hmm', 'Alternatively',
                          'alternatively', 'However', 'however']
        max_tokens_thinking = max_tokens
    else:
        raise ValueError("stop_condition must be 'length' or 'thinking_words'")

    result = await _acall(prompt, max_tokens_thinking, thinking_words)
    o = result['content']
    token_num = result['token_num']
    stop_reason = result['stop_reason']
    logprobs = result['logprobs']
    tokens = result.get('tokens')

    output_len += token_num
    actual_len += token_num
    max_tokens_temp -= token_num
    prompt += o

    if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
        return _safety_fallback(prompt, [], output_len, actual_len, api_call_count, thinking_rounds, sc_rounds)

    answer_list = []
    prev_answer_text = None
    consecutive_same_count = 0
    # 双通道: 跟踪最近一轮推理的 logprobs/tokens
    reasoning_logprobs = logprobs
    reasoning_tokens = tokens

    if enable_confidence:
        for i in range(num_thinking):
            if max_tokens_temp <= 0:
                break
            if max_api_calls > 0 and api_call_count >= max_api_calls:
                break
            if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
                return _safety_fallback(prompt, answer_list, output_len, actual_len, api_call_count, thinking_rounds, sc_rounds)

            if is_think_tag_paired(prompt):
                if stop_reason == 'stop':
                    if actual_len < min_think_tokens:
                        prompt = reopen_think_tag(prompt)
                        result = await _acall(prompt, think_stride, thinking_words)
                        thinking_rounds += 1
                        o = result['content']
                        token_num = result['token_num']
                        stop_reason = result['stop_reason']
                        logprobs = result['logprobs']
                        tokens = result.get('tokens')
                        prompt += o
                        output_len += token_num
                        actual_len += token_num
                        max_tokens_temp -= token_num
                        continue
                    else:
                        logger.debug(f"think_tag_paired at round {i+1}, actual_len={actual_len}, falling through to check answer")
                induced_prompt = prompt
            else:
                if stop_reason == 'stop' and actual_len < min_think_tokens:
                    result = await _acall(prompt, think_stride, thinking_words)
                    thinking_rounds += 1
                    o = result['content']
                    token_num = result['token_num']
                    stop_reason = result['stop_reason']
                    logprobs = result['logprobs']
                    tokens = result.get('tokens')
                    prompt += o
                    output_len += token_num
                    actual_len += token_num
                    max_tokens_temp -= token_num
                    continue
                induced_prompt = make_induced_prompt(prompt)

            result = await _acall(induced_prompt, check_answer_len)
            o = result['content']
            token_num = result['token_num']
            ans_logprobs = result['logprobs']
            ans_tokens = result.get('tokens')
            stop_reason = result['stop_reason']
            confidence = cal_confidence(ans_logprobs)
            perplexity = cal_perplexity(ans_logprobs)

            if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
                answer_list.append((induced_prompt + o, output_len + token_num, confidence, perplexity, stop_reason,
                                    0.0, 0.0, 0.0, 'safety_gate_fallback'))
                return _safety_fallback(prompt, answer_list, output_len + token_num, actual_len + token_num,
                                        api_call_count, thinking_rounds, sc_rounds)

            decision_score, value_min_raw, reasoning_score_raw, action_score_raw = compute_dual_channel_score(
                reasoning_logprobs, reasoning_tokens,
                ans_logprobs, ans_tokens, confidence, perplexity,
                enable_full_chain_confidence=enable_full_chain_confidence,
                full_chain_min_percentile=full_chain_min_percentile,
                enable_dual_channel=enable_dual_channel,
                reasoning_min_percentile=reasoning_min_percentile,
                enable_value_min_confidence=enable_value_min_confidence,
                value_min_bottom_k=value_min_bottom_k,
            )
            ln_score = 0.0

            current_answer_text = extract_answer_for_sc(induced_prompt + o)
            prev_answer_text, consecutive_same_count = check_consecutive_same(
                current_answer_text, prev_answer_text, consecutive_same_count, consecutive_same_stop)

            trial_info = None
            if trial_evaluator is not None:
                trial_info = trial_evaluator.evaluate_trial(induced_prompt + o, confidence)
                if trial_info.get('boosted_confidence') and trial_info['boosted_confidence'] > confidence:
                    confidence = trial_info['boosted_confidence']
                    decision_score = min(decision_score + deer_confidence_boost, 1.0)
                if trial_info.get('should_early_stop'):
                    should_accept = True
                    accept_reason = f'deer_trial_stable_{trial_info["stable_count"]}'

            should_accept, accept_reason = make_accept_decision(
                decision_score, prob_th, actual_len, min_think_tokens,
                is_think_tag_paired(prompt), consecutive_same_count, consecutive_same_stop,
                enable_value_min_confidence, value_min_raw,
                enable_full_chain_confidence=enable_full_chain_confidence,
                reasoning_score_raw=reasoning_score_raw,
                action_score_raw=action_score_raw,
                enable_dual_channel=enable_dual_channel,
            )

            if should_accept:
                answer = induced_prompt + o
                output_len += token_num
                answer_list.append((answer, output_len, confidence, perplexity, stop_reason,
                                    decision_score, value_min_raw, ln_score, accept_reason))
                final_result = max(answer_list, key=lambda x: x[5])

                if final_result[4] == 'stop' and is_think_tag_paired(final_result[0]):
                    content = final_result[0]
                    output_len = final_result[1]
                elif enable_soft_truncation:
                    result2 = await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)
                    content = _truncate_thinking_at_natural_breakpoint(final_result[0] + result2['content'])
                    output_len = final_result[1] + result2['token_num']
                    actual_len += result2['token_num']
                else:
                    result2 = await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)
                    content = final_result[0] + result2['content']
                    output_len = final_result[1] + result2['token_num']
                    actual_len += result2['token_num']
                ret = {
                    'content': content,
                    'token_num': output_len,
                    'prompt_tokens': len(prompt) // 4,
                    'confidence': final_result[2],
                    'actual_len': actual_len,
                    'perplexity': final_result[3],
                    'accept_reason': final_result[8],
                    'api_call_count': api_call_count,
                    'thinking_rounds': thinking_rounds,
                    'sc_rounds': sc_rounds,
                }
                if enable_value_min_confidence:
                    ret['value_min_confidence'] = final_result[6]
                ret['complexity'] = complexity_info
                # 返回推断的任务类型和场景类型，供下游记录历史和调试
                if enable_task_type_budget:
                    ret['task_type'] = task_type_async
                    ret['scene_type'] = scene_type_async
                return ret

            answer_list.append((induced_prompt + o, output_len + token_num, confidence, perplexity, stop_reason,
                                decision_score, value_min_raw, ln_score, 'continue'))

            # ─── v2: 在线动态 budget 调整 ───
            # 第2轮后根据实际 thinking 消耗动态调整后续 budget
            if enable_difficulty_estimator and thinking_rounds >= 2:
                actual_tpm = actual_len / max(thinking_rounds, 1)  # tokens per round
                if actual_tpm > check_length * 0.8:
                    old_cl = check_length
                    check_length = min(int(check_length * 1.5), difficulty_check_length_max)
                    think_stride = min(int(think_stride * 1.3), difficulty_stride_max)
                    logger.debug(f"Dynamic budget UP: tpm={actual_tpm:.0f}, cl {old_cl}→{check_length}, stride→{think_stride}")
                elif actual_tpm < check_length * 0.3 and thinking_rounds >= 3:
                    num_thinking = max(num_thinking - 2, thinking_rounds + 1)
                    think_stride = max(think_stride // 2, 50)
                    logger.debug(f"Dynamic budget DOWN: tpm={actual_tpm:.0f}, num_thinking→{num_thinking}, stride→{think_stride}")

            prompt, output_len = apply_clean_context(
                enable_clean_context, prompt, induced_prompt, o, output_len, token_num)

            stride = min(think_stride, max_tokens_temp)

            result = await _acall(prompt, stride, thinking_words)
            thinking_rounds += 1
            o = result['content']
            token_num = result['token_num']
            stop_reason = result['stop_reason']
            logprobs = result['logprobs']
            tokens = result.get('tokens')
            prompt += o
            output_len += token_num
            actual_len += token_num
            max_tokens_temp -= token_num
            # 双通道: 更新最近一轮推理的 logprobs/tokens
            reasoning_logprobs = logprobs
            reasoning_tokens = tokens

            if safety_gate_check(_elapsed, _effective_time_limit, enable_safety_gate):
                return _safety_fallback(prompt, answer_list, output_len, actual_len, api_call_count, thinking_rounds, sc_rounds)

    if not is_think_tag_paired(prompt):
        prompt = make_induced_prompt(prompt)

    # 循环结束后的最终答案生成 — 不限 token，让模型输出完整答案
    # check_answer_len 仅用于循环中试探答案（置信度评估），最终答案必须完整
    result = await _acall(prompt, _UNLIMITED_ANSWER_TOKENS)
    o = result['content']
    token_num = result['token_num']
    logprobs = result['logprobs']
    ans_tokens = result.get('tokens')
    stop_reason = result['stop_reason']

    confidence = cal_confidence(logprobs)
    perplexity = cal_perplexity(logprobs)
    output_len += token_num
    actual_len += token_num

    if enable_confidence:
        decision_score, value_min_raw, _rsr_fb, _asr_fb = compute_dual_channel_score(
            reasoning_logprobs, reasoning_tokens,
            logprobs, ans_tokens, confidence, perplexity,
            enable_full_chain_confidence=enable_full_chain_confidence,
            full_chain_min_percentile=full_chain_min_percentile,
            enable_dual_channel=enable_dual_channel,
            reasoning_min_percentile=reasoning_min_percentile,
            enable_value_min_confidence=enable_value_min_confidence,
            value_min_bottom_k=value_min_bottom_k,
        )
        ln_score = 0.0
        answer_list.append((prompt + o, output_len, confidence, perplexity, stop_reason,
                            decision_score, value_min_raw, ln_score, 'final_fallback'))
        final_result = max(answer_list, key=lambda x: x[5])

        if final_result[4] == 'stop' and is_think_tag_paired(final_result[0]):
            content = final_result[0]
            output_len = final_result[1]
        elif enable_soft_truncation:
            result2 = await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)
            content = _truncate_thinking_at_natural_breakpoint(final_result[0] + result2['content'])
            output_len = final_result[1] + result2['token_num']
            actual_len += result2['token_num']
        else:
            result2 = await _acall(final_result[0], _UNLIMITED_ANSWER_TOKENS)
            content = final_result[0] + result2['content']
            output_len = final_result[1] + result2['token_num']
            actual_len += result2['token_num']
        return {
            'content': content,
            'token_num': output_len,
            'prompt_tokens': len(prompt) // 4,
            'confidence': final_result[2],
            'actual_len': actual_len,
            'perplexity': final_result[3],
            'accept_reason': final_result[8],
            'api_call_count': api_call_count,
            'thinking_rounds': thinking_rounds,
            'sc_rounds': sc_rounds,
            'value_min_confidence': final_result[6] if enable_value_min_confidence else None,
            'complexity': complexity_info,
        }
    else:
        return {
            'content': prompt + o,
            'token_num': output_len,
            'prompt_tokens': len(prompt) // 4,
            'confidence': confidence,
            'actual_len': actual_len,
            'perplexity': perplexity,
            'complexity': complexity_info,
        }
