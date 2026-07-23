"""Streaming Think Budget state machine for API SSE responses.

This module is intentionally separate from ``budget_forcing.call_model``.  The
non-streaming path keeps using the original implementation.  The streaming path
has different visibility rules:

- thinking calls stream visible ``reasoning_delta`` events;
- trial-answer confidence probes are hidden and never yielded;
- final-answer calls stream visible ``content_delta`` events.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator, Dict, Optional

from naie.inference.think_budget import DEFAULT_MODEL, _cfg_to_params
from naie.models.budget_forcing import (
    _THINK_OPEN_TAG,
    _THINK_CLOSE_TAG,
    _THINKING_WORDS,
    _TRIAL_ANSWER_TOKENS,
    _UNLIMITED_ANSWER_TOKENS,
    _BUDGET_AMPLIFY_FACTOR,
    _BUDGET_AMPLIFY_CAP,
    _BUDGET_TIME_CAP_MULTIPLIER,
    _truncate_thinking_at_natural_breakpoint,
)
from naie.models.confidence import (
    cal_confidence,
    cal_perplexity,
    is_think_tag_paired,
    reopen_think_tag,
)
from naie.models.model_client import async_model_call, async_model_stream_call
from naie.optimizations.confidence_gates import compute_dual_channel_score
from naie.optimizations.context_control import apply_clean_context, make_induced_prompt
from naie.optimizations.early_stopping import (
    check_consecutive_same,
    extract_answer_for_sc,
    make_accept_decision,
)
from naie.optimizations.safety_gate import safety_gate_check
from naie.utils.tokenization import count_tokens

_USER_PREFIX = "<|im_start|>user\n"
_USER_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n"
_FINAL_TRIGGER = "OK, I have completed my thinking and can now provide the final answer."


def _clean_visible_piece(piece: str, *, reasoning: bool) -> str:
    """Remove internal prompt-control markers from streamed public chunks."""
    if not piece:
        return ""
    cleaned = piece
    for marker in (
        _USER_PREFIX,
        _USER_SUFFIX,
        "<|im_start|>assistant",
        "<|im_start|>user",
        "<|im_end|>",
        _THINK_OPEN_TAG,
        _THINK_CLOSE_TAG,
        _FINAL_TRIGGER,
    ):
        cleaned = cleaned.replace(marker, "")
    if reasoning:
        return cleaned
    return cleaned.lstrip()


def _resolve_stream_params(
    max_tokens: int,
    budget_config: Optional[Dict[str, Any]],
    reference: str,
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    cfg = dict(budget_config or {})
    params = _cfg_to_params(cfg) if cfg else _cfg_to_params({})
    params.update(kwargs)
    if "model_name" not in params or not params["model_name"]:
        params["model_name"] = cfg.get("model_name", DEFAULT_MODEL)
    params["reference"] = reference
    params.pop("max_tokens", None)

    if not params.get("BUDGET_ENABLED", True):
        params["enable_confidence"] = False

    if params.get("FIXED_BUDGET_TOKEN", 0) > 0 and params.get("fixed_budget_tokens", 0) <= 0:
        params["fixed_budget_tokens"] = params["FIXED_BUDGET_TOKEN"]

    fixed_budget_tokens = int(params.get("fixed_budget_tokens", 0) or 0)
    check_length = int(params.get("check_length", 200) or 200)
    think_stride = int(params.get("think_stride", 100) or 100)
    num_thinking = int(params.get("num_thinking", 10) or 0)
    min_think_tokens = int(params.get("min_think_tokens", 100) or 0)
    prob_th = float(params.get("prob_th", 0.95) or 0.95)

    if fixed_budget_tokens > 0:
        if params.get("fixed_think_stride", 0) > 0:
            think_stride = int(params["fixed_think_stride"])
        if params.get("fixed_check_length", 0) > 0:
            check_length = int(params["fixed_check_length"])
        check_length = min(fixed_budget_tokens, check_length)
        if params.get("fixed_num_thinking", 0) > 0:
            num_thinking = int(params["fixed_num_thinking"])
        else:
            remaining = max(fixed_budget_tokens - check_length, 0)
            num_thinking = (remaining + max(think_stride, 1) - 1) // max(think_stride, 1)
        min_think_tokens = fixed_budget_tokens
        if params.get("fixed_min_think_tokens", 0) > 0:
            min_think_tokens = int(params["fixed_min_think_tokens"])
        if params.get("fixed_prob_th", 0.0) > 0.0:
            prob_th = float(params["fixed_prob_th"])
    else:
        if params.get("fixed_num_thinking", 0) > 0:
            num_thinking = int(params["fixed_num_thinking"])
        if params.get("fixed_think_stride", 0) > 0:
            think_stride = int(params["fixed_think_stride"])
        if params.get("fixed_check_length", 0) > 0:
            check_length = int(params["fixed_check_length"])
        if params.get("fixed_min_think_tokens", 0) > 0:
            min_think_tokens = int(params["fixed_min_think_tokens"])
        if params.get("fixed_prob_th", 0.0) > 0.0:
            prob_th = float(params["fixed_prob_th"])

    if params.get("fixed_prob_th", 0.0) <= 0.0 and params.get("CONFIDENCE_THRESHOLD", 0.95) != 0.95:
        prob_th = float(params["CONFIDENCE_THRESHOLD"])

    check_answer_len = int(_TRIAL_ANSWER_TOKENS)
    if params.get("enable_confidence", True) and num_thinking > 0:
        planned = check_length + think_stride * max(num_thinking, 0)
        probe = 0 if params.get("enable_clean_context", True) else check_answer_len * num_thinking
        estimated = planned + probe
        if estimated > max_tokens:
            ratio = max_tokens / max(estimated, 1)
            check_length = max(int(check_length * ratio), 50)
            think_stride = max(int(think_stride * ratio), 20)
            num_thinking = max(int(num_thinking * ratio), 1)

    params.update({
        "check_length": check_length,
        "think_stride": think_stride,
        "num_thinking": num_thinking,
        "check_answer_len": check_answer_len,
        "min_think_tokens": min_think_tokens,
        "prob_th": prob_th,
        "max_tokens": max_tokens,
        "budget_plan": {
            "check_length": check_length,
            "think_stride": think_stride,
            "num_thinking": num_thinking,
            "check_answer_len": check_answer_len,
            "min_think_tokens": min_think_tokens,
            "max_tokens": max_tokens,
            "planned_thinking_budget": check_length + think_stride * max(num_thinking, 0),
        },
    })
    return params


async def call_model_stream(
    prompt: str,
    max_tokens: int,
    budget_config: Optional[Dict[str, Any]] = None,
    reference: str = "",
    **kwargs: Any,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield budget-controlled streaming events.

    Event shapes:
        {"type": "reasoning_delta", "content": "..."}
        {"type": "content_delta", "content": "..."}
        {"type": "done", "budget_info": {...}, "usage": {...}}
    """
    params = _resolve_stream_params(max_tokens, budget_config, reference, kwargs)
    model_name = params.get("model_name", DEFAULT_MODEL)
    enable_confidence = bool(params.get("enable_confidence", True))
    stop_condition = params.get("stop_condition", "length")
    check_length = int(params["check_length"])
    think_stride = int(params["think_stride"])
    num_thinking = int(params["num_thinking"])
    check_answer_len = int(params["check_answer_len"])
    min_think_tokens = int(params["min_think_tokens"])
    prob_th = float(params["prob_th"])
    max_api_calls = int(params.get("max_api_calls", 0) or 0)
    enable_clean_context = bool(params.get("enable_clean_context", True))
    enable_soft_truncation = bool(params.get("enable_soft_truncation", True))
    enable_safety_gate = bool(params.get("enable_safety_gate", False))
    baseline_time_limit = float(params.get("baseline_time_limit", 90.0))
    consecutive_same_stop = int(params.get("consecutive_same_stop", 2) or 2)
    enable_value_min_confidence = bool(params.get("enable_value_min_confidence", True))
    value_min_bottom_k = int(params.get("value_min_bottom_k", 5) or 5)
    enable_full_chain_confidence = bool(params.get("enable_full_chain_confidence", False))
    full_chain_min_percentile = float(params.get("full_chain_min_percentile", 0.1) or 0.1)
    enable_dual_channel = bool(params.get("enable_dual_channel", False))
    reasoning_min_percentile = float(params.get("reasoning_min_percentile", 0.1) or 0.1)

    api_call_count = 0
    thinking_rounds = 0
    output_len = 0
    actual_len = 0
    thinking_tokens = 0
    max_tokens_temp = max_tokens
    started_at = time.time()
    last_confidence = 0.0
    last_perplexity = 0.0
    last_value_min = None
    accept_reason = "final_fallback"

    if enable_confidence:
        budget_amplify = 1.0 + _BUDGET_AMPLIFY_FACTOR * min(num_thinking, _BUDGET_AMPLIFY_CAP)
        effective_time_limit = min(
            baseline_time_limit * budget_amplify,
            baseline_time_limit * _BUDGET_TIME_CAP_MULTIPLIER,
        )
    else:
        effective_time_limit = baseline_time_limit

    def _elapsed() -> float:
        return time.time() - started_at

    async def _hidden_call(p: str, max_tok: int, stop=None) -> Dict[str, Any]:
        nonlocal api_call_count
        if max_api_calls > 0 and api_call_count >= max_api_calls:
            return {
                "content": "",
                "token_num": 0,
                "logprobs": None,
                "tokens": None,
                "stop_reason": "length",
                "prompt_tokens": 0,
            }
        api_call_count += 1
        return await async_model_call(model_name, p, max_tok, stop)

    async def _visible_stream(p: str, max_tok: int, phase: str, stop=None) -> Dict[str, Any]:
        nonlocal api_call_count
        if max_api_calls > 0 and api_call_count >= max_api_calls:
            yield {
                "type": "_call_result",
                "result": {
                    "content": "",
                    "token_num": 0,
                    "logprobs": None,
                    "tokens": None,
                    "stop_reason": "length",
                    "prompt_tokens": 0,
                },
            }
            return
        api_call_count += 1
        chunks = []
        token_logprobs = []
        token_texts = []
        finish_reason = "stop"
        token_num = 0
        async for chunk in async_model_stream_call(model_name, p, max_tok, stop, logprobs=True):
            piece = chunk.get("content", "") or ""
            if piece:
                chunks.append(piece)
                token_num += count_tokens(piece)
                visible = _clean_visible_piece(piece, reasoning=(phase == "reasoning"))
                if visible:
                    yield_type = "reasoning_delta" if phase == "reasoning" else "content_delta"
                    yield {"type": yield_type, "content": visible}
            if chunk.get("logprobs"):
                token_logprobs.extend(lp for lp in chunk["logprobs"] if lp is not None)
            if chunk.get("tokens"):
                token_texts.extend(chunk["tokens"])
            if chunk.get("finish_reason"):
                finish_reason = chunk["finish_reason"]

        yield {
            "type": "_call_result",
            "result": {
                "content": "".join(chunks),
                "token_num": token_num,
                "logprobs": token_logprobs or None,
                "tokens": token_texts or None,
                "stop_reason": finish_reason,
                "prompt_tokens": count_tokens(p),
            },
        }

    async def _consume_visible_stream(p: str, max_tok: int, phase: str, stop=None):
        result = None
        async for event in _visible_stream(p, max_tok, phase, stop):
            if event["type"] == "_call_result":
                result = event["result"]
            else:
                yield event
        yield {"type": "_call_result", "result": result or {}}

    if not enable_confidence:
        final_result = None
        async for event in _consume_visible_stream(prompt, _UNLIMITED_ANSWER_TOKENS, "content"):
            if event["type"] == "_call_result":
                final_result = event["result"]
            else:
                yield event
        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": (final_result or {}).get("prompt_tokens", 0),
                "completion_tokens": (final_result or {}).get("token_num", 0),
                "reasoning_tokens": 0,
            },
            "budget_info": {
                "accept_reason": "baseline_free_generation",
                "confidence": 0.0,
                "perplexity": 0.0,
                "api_call_count": api_call_count,
                "thinking_rounds": 0,
                "thinking_tokens": 0,
                "budget_plan": params["budget_plan"],
            },
        }
        return

    internal_prompt = _USER_PREFIX + prompt + _USER_SUFFIX + _THINK_OPEN_TAG + "\n"
    thinking_words = None if stop_condition == "length" else _THINKING_WORDS
    max_tokens_thinking = min(check_length, max_tokens) if stop_condition == "length" else max_tokens

    result = None
    async for event in _consume_visible_stream(internal_prompt, max_tokens_thinking, "reasoning", thinking_words):
        if event["type"] == "_call_result":
            result = event["result"]
        else:
            yield event
    result = result or {}
    thinking_text = result.get("content", "")
    token_num = int(result.get("token_num", 0) or 0)
    stop_reason = result.get("stop_reason")
    logprobs = result.get("logprobs")
    tokens = result.get("tokens")
    if enable_soft_truncation and stop_reason == "length" and _THINK_CLOSE_TAG not in thinking_text:
        thinking_text = _truncate_thinking_at_natural_breakpoint(thinking_text)
    internal_prompt += thinking_text
    output_len += token_num
    actual_len += token_num
    thinking_tokens += token_num
    max_tokens_temp -= token_num

    last_round_logprobs = logprobs
    last_round_tokens = tokens
    full_chain_logprobs = list(logprobs) if logprobs else []
    full_chain_tokens = list(tokens) if tokens else []
    answer_list = []
    prev_answer_text = None
    consecutive_same_count = 0

    def _reasoning_logprobs():
        if enable_full_chain_confidence:
            return full_chain_logprobs or last_round_logprobs
        return last_round_logprobs

    def _reasoning_tokens():
        if enable_full_chain_confidence:
            return full_chain_tokens or last_round_tokens
        return last_round_tokens

    confidence_checks = 0
    while confidence_checks < num_thinking:
        if max_tokens_temp <= 0:
            break
        if max_api_calls > 0 and api_call_count >= max_api_calls:
            break
        if safety_gate_check(_elapsed, effective_time_limit, enable_safety_gate):
            accept_reason = "safety_gate_fallback"
            break

        if is_think_tag_paired(internal_prompt):
            if stop_reason == "stop" and actual_len < min_think_tokens:
                internal_prompt = reopen_think_tag(internal_prompt)
                stream_result = None
                async for event in _consume_visible_stream(internal_prompt, think_stride, "reasoning", thinking_words):
                    if event["type"] == "_call_result":
                        stream_result = event["result"]
                    else:
                        yield event
                stream_result = stream_result or {}
                o = stream_result.get("content", "")
                if enable_soft_truncation and stream_result.get("stop_reason") == "length" and _THINK_CLOSE_TAG not in o:
                    o = _truncate_thinking_at_natural_breakpoint(o)
                internal_prompt += o
                token_num = int(stream_result.get("token_num", 0) or 0)
                output_len += token_num
                actual_len += token_num
                thinking_tokens += token_num
                max_tokens_temp -= token_num
                thinking_rounds += 1
                stop_reason = stream_result.get("stop_reason")
                last_round_logprobs = stream_result.get("logprobs")
                last_round_tokens = stream_result.get("tokens")
                if enable_full_chain_confidence and last_round_logprobs:
                    full_chain_logprobs.extend(last_round_logprobs)
                    if last_round_tokens:
                        full_chain_tokens.extend(last_round_tokens)
                continue
            induced_prompt = internal_prompt
        else:
            if stop_reason == "stop" and actual_len < min_think_tokens:
                stream_result = None
                async for event in _consume_visible_stream(internal_prompt, think_stride, "reasoning", thinking_words):
                    if event["type"] == "_call_result":
                        stream_result = event["result"]
                    else:
                        yield event
                stream_result = stream_result or {}
                o = stream_result.get("content", "")
                if enable_soft_truncation and stream_result.get("stop_reason") == "length" and _THINK_CLOSE_TAG not in o:
                    o = _truncate_thinking_at_natural_breakpoint(o)
                internal_prompt += o
                token_num = int(stream_result.get("token_num", 0) or 0)
                output_len += token_num
                actual_len += token_num
                thinking_tokens += token_num
                max_tokens_temp -= token_num
                thinking_rounds += 1
                stop_reason = stream_result.get("stop_reason")
                last_round_logprobs = stream_result.get("logprobs")
                last_round_tokens = stream_result.get("tokens")
                if enable_full_chain_confidence and last_round_logprobs:
                    full_chain_logprobs.extend(last_round_logprobs)
                    if last_round_tokens:
                        full_chain_tokens.extend(last_round_tokens)
                continue
            induced_prompt = make_induced_prompt(internal_prompt)

        trial = await _hidden_call(induced_prompt, check_answer_len)
        trial_text = trial.get("content", "")
        trial_tokens = int(trial.get("token_num", 0) or 0)
        ans_logprobs = trial.get("logprobs")
        ans_tokens = trial.get("tokens")
        stop_reason = trial.get("stop_reason")
        last_confidence = cal_confidence(ans_logprobs)
        last_perplexity = cal_perplexity(ans_logprobs)
        confidence_checks += 1

        decision_score, value_min_raw, reasoning_score_raw, action_score_raw, value_min_computed = compute_dual_channel_score(
            _reasoning_logprobs(), _reasoning_tokens(),
            ans_logprobs, ans_tokens, last_confidence, last_perplexity,
            enable_full_chain_confidence=enable_full_chain_confidence,
            full_chain_min_percentile=full_chain_min_percentile,
            enable_dual_channel=enable_dual_channel,
            reasoning_min_percentile=reasoning_min_percentile,
            enable_value_min_confidence=enable_value_min_confidence,
            value_min_bottom_k=value_min_bottom_k,
        )
        last_value_min = value_min_raw

        current_answer_text = extract_answer_for_sc(induced_prompt + trial_text)
        prev_answer_text, consecutive_same_count = check_consecutive_same(
            current_answer_text, prev_answer_text, consecutive_same_count, consecutive_same_stop)
        should_accept, accept_reason = make_accept_decision(
            decision_score, prob_th, actual_len, min_think_tokens,
            is_think_tag_paired(internal_prompt), consecutive_same_count, consecutive_same_stop,
            enable_value_min_confidence, value_min_raw,
            enable_full_chain_confidence=enable_full_chain_confidence,
            reasoning_score_raw=reasoning_score_raw,
            action_score_raw=action_score_raw,
            enable_dual_channel=enable_dual_channel,
            value_min_computed=value_min_computed,
        )

        answer_list.append((induced_prompt + trial_text, output_len + trial_tokens, last_confidence,
                            last_perplexity, stop_reason, decision_score, value_min_raw, 0.0,
                            accept_reason if should_accept else "continue"))
        if should_accept:
            break

        internal_prompt, output_len = apply_clean_context(
            enable_clean_context, internal_prompt, induced_prompt, trial_text, output_len, trial_tokens)

        stride = min(think_stride, max_tokens_temp)
        if stride <= 0:
            break
        stream_result = None
        async for event in _consume_visible_stream(internal_prompt, stride, "reasoning", thinking_words):
            if event["type"] == "_call_result":
                stream_result = event["result"]
            else:
                yield event
        stream_result = stream_result or {}
        o = stream_result.get("content", "")
        if enable_soft_truncation and stream_result.get("stop_reason") == "length" and _THINK_CLOSE_TAG not in o:
            o = _truncate_thinking_at_natural_breakpoint(o)
        internal_prompt += o
        token_num = int(stream_result.get("token_num", 0) or 0)
        output_len += token_num
        actual_len += token_num
        thinking_tokens += token_num
        max_tokens_temp -= token_num
        thinking_rounds += 1
        stop_reason = stream_result.get("stop_reason")
        last_round_logprobs = stream_result.get("logprobs")
        last_round_tokens = stream_result.get("tokens")
        if enable_full_chain_confidence and last_round_logprobs:
            full_chain_logprobs.extend(last_round_logprobs)
            if last_round_tokens:
                full_chain_tokens.extend(last_round_tokens)

    final_prompt = internal_prompt if is_think_tag_paired(internal_prompt) else make_induced_prompt(internal_prompt)
    final_stream_result = None
    async for event in _consume_visible_stream(final_prompt, _UNLIMITED_ANSWER_TOKENS, "content"):
        if event["type"] == "_call_result":
            final_stream_result = event["result"]
        else:
            yield event
    final_stream_result = final_stream_result or {}
    final_tokens = int(final_stream_result.get("token_num", 0) or 0)
    output_len += final_tokens
    actual_len += final_tokens

    yield {
        "type": "done",
        "usage": {
            "prompt_tokens": count_tokens(final_prompt),
            "completion_tokens": output_len,
            "reasoning_tokens": thinking_tokens,
        },
        "budget_info": {
            "accept_reason": accept_reason,
            "confidence": last_confidence,
            "perplexity": last_perplexity,
            "value_min_confidence": last_value_min,
            "api_call_count": api_call_count,
            "thinking_rounds": thinking_rounds,
            "thinking_tokens": thinking_tokens,
            "actual_len": actual_len,
            "budget_plan": params["budget_plan"],
            "elapsed_time": round(time.time() - started_at, 2),
            "model": model_name,
        },
    }
