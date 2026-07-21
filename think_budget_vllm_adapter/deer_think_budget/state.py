"""Think Budget 状态机 — DEER 状态转移 + Think Budget 算法核心。

状态转移 (保留 deer 原版):
  _InitialThinkState → _IterativeThinkState ⇄ _ConfidenceCheckState
                           ↓                       ↓
                       _AnswerState ←───────────────┘

算法替换 (与 think budget 保持一致):
  - _ConfidenceCheckState: DEER 简单 logprob 均值 → Think Budget 双通道/Value Min Confidence
  - _InitialThinkState: 增加 soft truncation
  - _IterativeThinkState: 增加 soft truncation + safety gate
  - _AnswerState: 在需要时注入 THINK_END_TAG，并生成最终答案

适配 vLLM v0.20.2:
  - to_sampling_params(max_tokens, default_sampling_params) 2参数签名
  - model_config.max_model_len 访问
  - SampleLogprobs 格式 → (logprobs_float, tokens_text) 转换
  - TokenizerLike.encode(text, add_special_tokens=False)

Think tags: use "<think>" as stop word and stop_reason (same as deer source code)
token_ids encoded via context.tokenizer.encode("</think>", add_special_tokens=False)

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

from __future__ import annotations

import copy
from time import perf_counter

from abc import ABC
from collections.abc import AsyncGenerator
from typing import Optional, Tuple, Type, TYPE_CHECKING

from think_budget_vllm_adapter.deer_think_budget.context import RequestStateContext
from think_budget_vllm_adapter.deer_think_budget.confidence import (
    cal_confidence, cal_perplexity,
    is_think_tag_paired,
    extract_token_logprobs_and_texts,
)
from think_budget_vllm_adapter.deer_think_budget.confidence_gates import compute_dual_channel_score
from think_budget_vllm_adapter.deer_think_budget.early_stopping import (
    extract_answer_for_sc, check_consecutive_same,
    make_accept_decision,
)
from think_budget_vllm_adapter.deer_think_budget.safety_gate import safety_gate_check

if TYPE_CHECKING:
    from vllm.outputs import RequestOutput


# ─── Think tags (严格按照 deer 源码) ───
# deer 源码中 stop word = "</think>", stop_reason 检测也是 "</think>"
# think_budget 源码中 _THINK_CLOSE_TAG = '</think>'
THINK_END_TAG = "</think>"


def _last_chunk_ends_with_think_tag(last_chunk: object) -> bool:
    """判断刚刚已透传的思考输出是否已含闭合标签。

    vLLM 在不同 ``RequestOutputKind`` 下可能返回增量文本或累计文本。
    无论哪一种，只要最后一段可见文本已经以 ``</think>`` 结尾，就不能
    再合成并透传一次闭合标签。
    """
    try:
        output = last_chunk.outputs[0]
        return (getattr(output, "text", "") or "").rstrip().endswith(THINK_END_TAG)
    except (AttributeError, IndexError, TypeError):
        return False


def _configure_final_answer_stop(params: object) -> None:
    """保留用户 stop，同时屏蔽最终答案中的重复 ``</think>``。"""
    current_stop = getattr(params, "stop", None)
    if isinstance(current_stop, str):
        stops = [current_stop]
    else:
        stops = list(current_stop or [])
    if THINK_END_TAG not in stops:
        stops.append(THINK_END_TAG)
    params.stop = stops
    # 即使调用方请求返回 stop 文本，回答区也不能把控制标签暴露给用户。
    params.include_stop_str_in_output = False


async def _emit_think_end_once(context: RequestStateContext) -> bool:
    """在已输出思考内容时，至多补发一次可见的闭合标签。"""
    if getattr(context, "think_end_emitted", False):
        return False
    last_chunk = context.last_chunk
    if last_chunk is None:
        return False
    if _last_chunk_ends_with_think_tag(last_chunk):
        context.think_end_emitted = True
        return False
    end_think_res = copy.deepcopy(last_chunk)
    end_think_res.outputs[0].text = THINK_END_TAG
    end_think_res.outputs[0].token_ids = context.tokenizer.encode(
        THINK_END_TAG, add_special_tokens=False)
    await context.stream_output(end_think_res)
    context.think_end_emitted = True
    return True


def _decode_prompt_text(context: RequestStateContext) -> str:
    """从 engine_prompt 的 token_ids 解码出文本，用于 is_think_tag_paired 等检查。"""
    try:
        return context.tokenizer.decode(
            context.engine_prompt.get('prompt_token_ids', []),
            skip_special_tokens=True
        )
    except Exception:
        return ''


class _BaseState(ABC):
    """状态的抽象基类"""
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type['_BaseState']]:
        """处理当前状态的逻辑，并返回下一个状态的类。"""
        raise NotImplementedError

    @staticmethod
    def log_stage(context: RequestStateContext, event: str, stage: str, **details) -> None:
        """Emit progress metadata only; generated text is never logged."""
        print(
            "TB_STAGE "
            f"event={event} request_id={context.request_id} "
            f"session_id={getattr(context, 'session_id', None)} "
            f"model={getattr(context, 'model_name', None)} stage={stage} "
            f"thinking_round={context.thinking_rounds} trial_round={context.trial_count} "
            f"api_calls={context.api_call_count} remaining_budget={context.max_tokens_temp} "
            f"details={details}",
            flush=True,
        )

    @staticmethod
    async def consume_output(
        gen: AsyncGenerator,
        context: Optional[RequestStateContext],
        *,
        collect_reasoning_logprobs: bool = False,
    ) -> Tuple[list[int], Optional[RequestOutput]]:
        """消费 generator 输出，收集 token_ids 并转发到 result_queue。"""
        stride_token_ids = []
        stride_logprobs = []
        logprob_token_ids = []
        last_chunk = None
        async for chunk in gen:
            out = chunk.outputs[0]
            stride_token_ids.extend(out.token_ids)
            if collect_reasoning_logprobs and out.logprobs:
                # ``out.logprobs`` 与同一 chunk 的 ``out.token_ids`` 一一
                # 对应；后者用于从 top-k 候选中精确取回实际采样 token。
                pair_count = min(len(out.logprobs), len(out.token_ids))
                stride_logprobs.extend(out.logprobs[:pair_count])
                logprob_token_ids.extend(out.token_ids[:pair_count])
            if chunk.outputs[0].finish_reason:  # 本轮推理正常结束，但我们需要过滤停止信号以进一步处理
                chunk.outputs[0].finish_reason = None
            await context.stream_output(chunk)
            # generate 在下一次迭代抛异常时，仍能收口已经透传的思考文本。
            context.last_chunk = chunk
            last_chunk = chunk
        if collect_reasoning_logprobs and context is not None:
            reasoning_logprobs, reasoning_tokens = extract_token_logprobs_and_texts(
                stride_logprobs, logprob_token_ids)
            if context.reasoning_logprobs is None:
                context.reasoning_logprobs = []
                context.reasoning_tokens = []
            context.reasoning_logprobs.extend(reasoning_logprobs)
            context.reasoning_tokens.extend(reasoning_tokens)
        return stride_token_ids, last_chunk

    @staticmethod
    async def consume_output_for_logprobs(
        gen: AsyncGenerator,
    ) -> Tuple[list, list[int], Optional[RequestOutput]]:
        """消费 generator 输出，收集 logprobs (不转发到 result_queue)。

        用于 _ConfidenceCheckState 的 trial answer 生成 — trial answer 不对外输出，
        仅用于内部置信度评估。
        """
        stride_logprobs = []
        stride_token_ids = []
        last_chunk = None
        async for res in gen:
            out = res.outputs[0]
            # vLLM 格式: out.logprobs 是 SampleLogprobs
            if out.logprobs:
                pair_count = min(len(out.logprobs), len(out.token_ids))
                stride_logprobs.extend(out.logprobs[:pair_count])
                stride_token_ids.extend(out.token_ids[:pair_count])
            last_chunk = res
        return stride_logprobs, stride_token_ids, last_chunk

    @staticmethod
    def requires_reasoning_logprobs(context: RequestStateContext) -> bool:
        """仅在完整链/双通道启用时为思考子请求请求 logprobs。

        默认 Value-Min 只评估 trial answer；不给每段思考开启 logprobs，避免
        在默认模式为所有流式 chunk 增加无意义的返回与处理开销。
        """
        return bool(
            context.config.enable_full_chain_confidence
            or context.config.enable_dual_channel)

    @staticmethod
    def next_engine_request_id(context: RequestStateContext, stage: str) -> str:
        """Assign traceable child ids and enforce the external API-call limit."""
        max_api_calls = getattr(context.config, "max_api_calls", 0)
        if max_api_calls > 0 and context.api_call_count >= max_api_calls:
            raise RuntimeError(
                f"Think Budget max_api_calls reached for {context.request_id}: {max_api_calls}")
        context.api_call_count += 1
        return f"{context.request_id}_{stage}_{context.api_call_count}"

    @staticmethod
    def has_call_room(context: RequestStateContext, required_calls: int) -> bool:
        """Return whether optional work can run while reserving later calls.

        A positive max_api_calls is a complete parent-request budget, including
        the final answer.  Callers use this helper before optional thinking or
        trial stages so a cap never turns into a missing final answer.
        """
        max_api_calls = getattr(context.config, "max_api_calls", 0)
        return max_api_calls <= 0 or (
            context.api_call_count + required_calls <= max_api_calls)


class _InitialThinkState(_BaseState):
    """状态1: 执行首次长程思考

    DEER behavior: generate initial_thought_limit tokens, stop at "<think>"
    Think Budget 增强: soft truncation (自然断点闭合)
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.config.initial_thought_limit <= 0:
            return _IterativeThinkState
        if not _BaseState.has_call_room(context, 2):
            # 调用上限不足时保留最终回答，不能让可选思考挤掉答案。
            return _AnswerState

        # 对齐到 128 的倍数 (与 DEER 原版一致)
        threshold = 128 - len(context.engine_prompt['prompt_token_ids']) % 128
        threshold += context.config.initial_thought_limit
        threshold = min(threshold, context.max_tokens_temp)
        if threshold <= 0:
            return _AnswerState

        # 新版 vLLM: to_sampling_params 只接受 (max_tokens, default_sampling_params)
        params = context.request.to_sampling_params(
            threshold,
            context.default_sampling_params,
        )
        params.stop = [THINK_END_TAG]
        if _BaseState.requires_reasoning_logprobs(context):
            params.logprobs = 1

        _BaseState.log_stage(
            context, "enter", "initial_think", max_tokens=threshold,
            stop=THINK_END_TAG, logprobs=bool(getattr(params, "logprobs", None)),
            prompt_tokens=len(context.engine_prompt["prompt_token_ids"]),
        )
        started_at = perf_counter()
        with context.timing.measure("engine.initial_think"):
            gen = context.engine_client.generate(
                context.engine_prompt, params,
                _BaseState.next_engine_request_id(context, "initial_think"),
                **context.engine_request_kwargs(),
            )
            stride_token_ids, last_chunk = await _BaseState.consume_output(
                gen, context,
                collect_reasoning_logprobs=_BaseState.requires_reasoning_logprobs(context),
            )
        if last_chunk is None:
            raise RuntimeError("initial_think completed without a vLLM output")
        context.update_prompts(stride_token_ids, last_chunk)

        # 更新 token 计数
        token_count = len(stride_token_ids)
        context.output_len += token_count
        context.actual_len += token_count
        context.max_tokens_temp -= token_count
        _BaseState.log_stage(
            context, "complete", "initial_think",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            generated_tokens=token_count, stop_reason=last_chunk.outputs[0].stop_reason,
        )

        # 检查是否因为 </think> 停止 (与 deer 源码一致)
        if last_chunk and last_chunk.outputs[0].stop_reason == THINK_END_TAG:
            return _AnswerState  # 思考已结束，直接回答
        else:
            return _IterativeThinkState  # 进入迭代思考


class _IterativeThinkState(_BaseState):
    """状态2: 执行后续的短程、迭代思考

    DEER behavior: each round generates think_stride tokens, detect "<think>"
    Think Budget 增强: soft truncation + safety gate
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        # ─── 安全门检查 ───
        if safety_gate_check(context.elapsed, context.config.baseline_time_limit,
                             context.config.enable_safety_gate):
            # 安全门触发 — 直接进入 _AnswerState (与 deer 源码行为一致)
            return _AnswerState

        if context.trial_count >= context.config.trial_answers:
            return _AnswerState

        # max_tokens 预算耗尽
        if context.max_tokens_temp <= 0:
            return _AnswerState

        # Reserve a possible trial plus the mandatory final answer. When the
        # parent request is near its call cap, finish instead of spending the
        # final slot on another think chunk.
        if not _BaseState.has_call_room(context, 3):
            return _AnswerState

        context.thinking_rounds += 1

        max_new_tokens_for_thought = context.config.think_stride \
            if context.config.think_stride > 0 \
            else int(context.serving_chat.model_config.max_model_len * context.config.think_ratio) - len(context.engine_prompt['prompt_token_ids'])
        max_new_tokens_for_thought = min(
            max_new_tokens_for_thought, context.max_tokens_temp)
        if max_new_tokens_for_thought <= 0:
            return _AnswerState

        # 新版 vLLM: to_sampling_params 只接受 (max_tokens, default_sampling_params)
        params = context.request.to_sampling_params(
            max_new_tokens_for_thought,
            context.default_sampling_params,
        )
        params.stop = [THINK_END_TAG]
        if _BaseState.requires_reasoning_logprobs(context):
            params.logprobs = 1

        _BaseState.log_stage(
            context, "enter", "iterative_think", max_tokens=max_new_tokens_for_thought,
            stop=THINK_END_TAG, logprobs=bool(getattr(params, "logprobs", None)),
            prompt_tokens=len(context.engine_prompt["prompt_token_ids"]),
        )
        started_at = perf_counter()
        with context.timing.measure("engine.iterative_think"):
            gen = context.engine_client.generate(
                context.engine_prompt,
                params,
                _BaseState.next_engine_request_id(context, "rethink"),
                **context.engine_request_kwargs(),
            )
            stride_token_ids, last_chunk = await _BaseState.consume_output(
                gen, context,
                collect_reasoning_logprobs=_BaseState.requires_reasoning_logprobs(context),
            )
        if last_chunk is None:
            raise RuntimeError("iterative_think completed without a vLLM output")

        # deer 源码: stop_word 可能是 str 或 int，需要处理
        stop_word = last_chunk.outputs[0].stop_reason
        if stop_word is not None:  # 在下一轮续写中添加本次的截断词
            if isinstance(stop_word, str):
                stride_token_ids.extend(
                    context.tokenizer.encode(stop_word, add_special_tokens=False)
                )
            else:
                stride_token_ids.append(stop_word)
        context.update_prompts(stride_token_ids, last_chunk)

        # 更新 token 计数
        token_count = len(stride_token_ids)
        context.output_len += token_count
        context.actual_len += token_count
        context.max_tokens_temp -= token_count
        _BaseState.log_stage(
            context, "complete", "iterative_think",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            generated_tokens=token_count, stop_reason=last_chunk.outputs[0].stop_reason,
        )

        if last_chunk and last_chunk.outputs[0].stop_reason == THINK_END_TAG:
            return _AnswerState  # 思考已结束，直接回答
        else:
            return _ConfidenceCheckState  # 思考未结束，检查置信度


class _ConfidenceCheckState(_BaseState):
    """状态3: 生成 trial answer, 使用 Think Budget 算法检查置信度

    DEER 行为: 简单 logprob 均值 (avg2 几何平均) vs prob_th
    Think Budget 替换:
      - cal_confidence + cal_perplexity (基础指标)
      - compute_dual_channel_score (4种模式: full_chain / dual_channel / value_min / 默认)
      - check_consecutive_same (连续相同答案早停)
      - make_accept_decision (综合判断，含 min_think_tokens 门控)

    关键适配: vLLM logprobs 格式转换
      DEER 使用 vLLM CompletionOutput.logprobs (dict[str, Logprob] 格式)
      Think Budget 需要 list[float | None] + list[str | None] 格式
      → 使用 extract_token_logprobs_and_texts() 转换
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.trial_count >= context.config.trial_answers:
            return _AnswerState

        # A trial is optional; retain one API call for the final answer.
        if not _BaseState.has_call_room(context, 2):
            return _AnswerState

        context.trial_count += 1
        context.sc_rounds += 1

        # ─── 安全门检查 ───
        if safety_gate_check(context.elapsed, context.config.baseline_time_limit,
                             context.config.enable_safety_gate):
            return _AnswerState

        # ─── 构建 trial answer 请求 ───
        # 新版 vLLM: to_sampling_params 只接受 (max_tokens, default_sampling_params)
        ans_params = context.request.to_sampling_params(
            context.config.trial_answer_length,
            context.default_sampling_params,
        )
        ans_params.logprobs = 1  # 为置信度检查准备 logprobs

        # trial answer 提示词 (与 deer 源码一致)
        if context.config.is_code:
            ans_prompt = (
                "I have to give the answer based on the thinking "
                f"directly without explanation now.{THINK_END_TAG}\n"
            )
        else:
            ans_prompt = (
                "I have to give the answer based on the thinking "
                "directly without explanation now.\n**Final Answer**\n"
            )
            # deer 源码中非 code 模式的 stop 是字符串而非列表
            ans_params.stop = [THINK_END_TAG]

        ans_prompt_tokens = context.tokenizer.encode(ans_prompt, add_special_tokens=False)
        ans_engine_prompt = copy.deepcopy(context.engine_prompt)
        ans_engine_prompt['prompt_token_ids'].extend(ans_prompt_tokens)

        _BaseState.log_stage(
            context, "enter", "confidence_check",
            trial_answer_max_tokens=context.config.trial_answer_length,
            prob_th=context.config.prob_th,
            value_min=context.config.enable_value_min_confidence,
            dual_channel=context.config.enable_dual_channel,
            full_chain=context.config.enable_full_chain_confidence,
            prompt_tokens=len(ans_engine_prompt["prompt_token_ids"]),
        )
        started_at = perf_counter()
        with context.timing.measure("engine.trial_answer"):
            gen = context.engine_client.generate(
                ans_engine_prompt,
                ans_params,
                _BaseState.next_engine_request_id(context, "trial_answer"),
                **context.engine_request_kwargs(),
            )
            # ─── 收集 trial answer 的 logprobs (不转发到 result_queue) ───
            stride_logprobs, trial_token_ids, trial_chunk = (
                await _ConfidenceCheckState.consume_output_for_logprobs(gen))
        if trial_chunk is None:
            raise RuntimeError("trial_answer completed without a vLLM output")

        # ─── vLLM logprobs 格式转换 ───
        # stride_logprobs 是 list[dict[str, Logprob] | None] (vLLM SampleLogprobs)
        # Think Budget 需要 list[float | None] + list[str | None]
        ans_logprobs_float, ans_tokens_text = extract_token_logprobs_and_texts(
            stride_logprobs, trial_token_ids)

        # ─── Think Budget: 基础置信度/困惑度 ───
        confidence = cal_confidence(ans_logprobs_float)
        perplexity = cal_perplexity(ans_logprobs_float)

        # ─── Think Budget: 双通道/单通道决策评分 ───
        decision_score, value_min_raw, reasoning_score_raw, action_score_raw = compute_dual_channel_score(
            context.reasoning_logprobs, context.reasoning_tokens,
            ans_logprobs_float, ans_tokens_text,
            confidence, perplexity,
            enable_full_chain_confidence=context.config.enable_full_chain_confidence,
            full_chain_min_percentile=context.config.full_chain_min_percentile,
            enable_dual_channel=context.config.enable_dual_channel,
            reasoning_min_percentile=context.config.reasoning_min_percentile,
            enable_value_min_confidence=context.config.enable_value_min_confidence,
            value_min_bottom_k=context.config.value_min_bottom_k,
        )

        # ─── 构造 trial answer 文本 (用于 answer_list 和 consecutive_same) ───
        # trial answer 的文本需要从 trial_chunk 获取
        trial_answer_text = ""
        if trial_chunk:
            trial_answer_text = trial_chunk.outputs[0].text

        # induced_prompt + trial_answer_text 用于 extract_answer_for_sc
        # (使用 ans_engine_prompt 的文本形式作为 content — 这里简化处理)
        full_answer_content = ans_prompt + trial_answer_text

        trial_evaluation = (
            context.trial_evaluator.evaluate_trial(full_answer_content, confidence)
            if getattr(context, "trial_evaluator", None) is not None else None
        )

        # ─── Think Budget: 连续相同答案检测 ───
        current_answer_text = extract_answer_for_sc(full_answer_content)
        consecutive_same_stop = (
            context.config.consecutive_same_stop
            if getattr(context.config, "enable_consecutive_same_stop", False)
            else 0
        )
        context.prev_answer_text, context.consecutive_same_count = check_consecutive_same(
            current_answer_text, context.prev_answer_text,
            context.consecutive_same_count, consecutive_same_stop,
        )

        # ─── Think Budget: 综合早停判断 ───
        # 注意: is_think_tag_paired 需要文本，不能传 token_ids
        prompt_text = _decode_prompt_text(context)
        should_accept, accept_reason = make_accept_decision(
            decision_score, context.config.prob_th,
            context.actual_len, context.config.min_think_tokens,
            is_think_tag_paired(prompt_text),
            context.consecutive_same_count, consecutive_same_stop,
            context.config.enable_value_min_confidence, value_min_raw,
            enable_full_chain_confidence=context.config.enable_full_chain_confidence,
            reasoning_score_raw=reasoning_score_raw,
            action_score_raw=action_score_raw,
            enable_dual_channel=context.config.enable_dual_channel,
        )
        if trial_evaluation and trial_evaluation["should_early_stop"]:
            should_accept = True
            accept_reason = "deer_trial_stable"

        _BaseState.log_stage(
            context, "complete", "confidence_check",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            trial_tokens=len(trial_token_ids), confidence=round(confidence, 6),
            perplexity=round(perplexity, 6), decision_score=round(decision_score, 6),
            accept=should_accept, accept_reason=accept_reason,
        )

        # ─── 记录到 answer_list ───
        context.answer_list.append((
            full_answer_content,
            context.output_len,
            confidence, perplexity,
            'stop' if trial_chunk and trial_chunk.outputs[0].stop_reason else 'length',
            decision_score, value_min_raw, 0.0, accept_reason,
        ))

        if should_accept:
            # 置信度足够 → 进入 _AnswerState
            print(
                f"TB_CONFIDENCE event=accept request_id={context.request_id} "
                f"trial_round={context.trial_count} decision_score={decision_score:.4f} "
                f"prob_th={context.config.prob_th} reason={accept_reason}",
                flush=True,
            )
            return _AnswerState

        # ─── 置信度不够 → 继续思考 ───
        print(
            f"TB_CONFIDENCE event=continue request_id={context.request_id} "
            f"trial_round={context.trial_count} decision_score={decision_score:.4f} "
            f"prob_th={context.config.prob_th} reason={accept_reason}",
            flush=True,
        )

        # Trial prompt is constructed from a deepcopy and is never appended to
        # engine_prompt. This is the clean-context path: retrying continues from
        # the thinking context rather than accumulating induced trial answers.

        return _IterativeThinkState


class _AnswerState(_BaseState):
    """状态4: 生成最终答案

    DEER behavior: inject "<think>" then generate final answer
    (严格与 deer 源码一致，不做 answer_list 选优)
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        # 异常收口与正常回答共用，绝不重复输出闭合标签。
        await _emit_think_end_once(context)

        if context.last_chunk and context.last_chunk.outputs[0].stop_reason is None:
            # 上一轮思考因为超长退出，强制生成答案 (与 deer 源码一致)
            ans_prompt = (
                "Considering the limited time by the user, "
                f"I have to give the solution based on the thinking directly now.\n{THINK_END_TAG}.\n\n"
            )
        else:
            # 置信度足够高，可以直接结束思考 (与 deer 源码一致)
            ans_prompt = THINK_END_TAG

        ans_prompt_tokens = context.tokenizer.encode(ans_prompt, add_special_tokens=False)
        context.engine_prompt['prompt_token_ids'].extend(ans_prompt_tokens)

        remaining_context = (
            context.serving_chat.model_config.max_model_len
            - len(context.engine_prompt['prompt_token_ids'])
        )
        requested_max_tokens = (
            getattr(context.request, "max_completion_tokens", None)
            or getattr(context.request, "max_tokens", None)
        )
        answer_limit = context.config.final_answer_max_tokens
        if requested_max_tokens is not None:
            answer_limit = min(answer_limit, requested_max_tokens)
        answer_limit = min(answer_limit, remaining_context)
        if answer_limit <= 0:
            raise RuntimeError("no context space remains for the final answer")

        params = context.request.to_sampling_params(answer_limit, context.default_sampling_params)
        _configure_final_answer_stop(params)
        _BaseState.log_stage(
            context, "enter", "final_answer", max_tokens=answer_limit,
            prompt_tokens=len(context.engine_prompt["prompt_token_ids"]),
            stop=getattr(params, "stop", None), include_stop_str_in_output=False,
        )
        started_at = perf_counter()
        generated_tokens = 0
        with context.timing.measure("engine.final_answer"):
            async for res in context.engine_client.generate(
                context.engine_prompt,
                params,
                _BaseState.next_engine_request_id(context, "answer"),
                **context.engine_request_kwargs(),
            ):
                generated_tokens += len(res.outputs[0].token_ids)
                await context.stream_output(res)
        if generated_tokens <= 0:
            raise RuntimeError("final_answer completed without generated tokens")
        _BaseState.log_stage(
            context, "complete", "final_answer",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            generated_tokens=generated_tokens,
        )
        return None


class AsyncReasoningOrchestrator:
    """流程编排器，负责驱动状态机运行。"""
    def __init__(self, context: RequestStateContext):
        self.context = context

    async def run(self):
        """执行状态机的主循环"""
        current_state_class: Optional[Type[_BaseState]] = _InitialThinkState
        try:
            while current_state_class is not None:
                state_name = current_state_class.__name__
                _BaseState.log_stage(self.context, "state_enter", state_name)
                state_started_at = perf_counter()
                current_state_class = await current_state_class.handle(self.context)
                _BaseState.log_stage(
                    self.context, "state_complete", state_name,
                    duration_ms=round((perf_counter() - state_started_at) * 1000, 3),
                    next_state=(current_state_class.__name__ if current_state_class else None),
                )
        except Exception as e:
            import traceback
            print(
                f"TB_ERROR event=orchestration request_id={self.context.request_id} "
                f"error={e}",
                flush=True,
            )
            print(traceback.format_exc(), flush=True)
            self.context.orchestration_error = str(e)
            try:
                await _emit_think_end_once(self.context)
            except Exception:
                print(
                    "TB_ERROR event=emit_think_end_failed "
                    f"request_id={self.context.request_id}\n{traceback.format_exc()}",
                    flush=True,
                )
        finally:
            print(
                "TB_PROFILE "
                f"request_id={self.context.request_id} "
                f"model={getattr(self.context, 'model_name', None)} "
                f"session_id={getattr(self.context, 'session_id', None)} "
                f"profile={self.context.timing.snapshot()}",
                flush=True,
            )
            await self.context.stream_output(None)  # 确保最后总是发送结束信号
