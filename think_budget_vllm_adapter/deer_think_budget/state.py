"""Three-state Think Budget execution on vLLM child ``generate`` calls."""
from __future__ import annotations

import asyncio
import copy
from abc import ABC
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any, Optional, Type, TYPE_CHECKING

from think_budget_vllm_adapter.deer_think_budget.confidence_gates import (
    assess_trial_confidence,
)
from think_budget_vllm_adapter.deer_think_budget.context import (
    FinalAnswerReason,
    ReasoningBoundary,
    ReasoningState,
    RequestStateContext,
    TrialObservation,
)
from think_budget_vllm_adapter.deer_think_budget.safety_gate import (
    safety_gate_check,
)

if TYPE_CHECKING:
    from vllm.outputs import RequestOutput


THINK_END_TAG = "</think>"
TOOL_CALL_START_TAG = "<tool_call>"
BUDGET_EXHAUSTED_FINAL_PROMPT = (
    "Considering the limited time by the user, I have to give the solution "
    "based on the thinking directly now.\n</think>\n\n"
)
TRIAL_PROMPT = (
    "I have to give the answer based on the thinking directly "
    "without explanation now.\n**Final Answer**\n"
)


def _output_kind_name(value: Any) -> str:
    kind = getattr(value, "output_kind", value)
    name = getattr(kind, "name", None)
    if name:
        return str(name).upper()
    text = str(kind or "DELTA").rsplit(".", 1)[-1]
    return text.upper()


def _replace_or_append(
    output_kind: str,
    current_text: str,
    current_token_ids: list[int],
    chunk_text: str,
    chunk_token_ids: list[int],
) -> tuple[str, list[int]]:
    if output_kind == "DELTA":
        return (
            current_text + chunk_text,
            [*current_token_ids, *chunk_token_ids],
        )
    return chunk_text, list(chunk_token_ids)


def _aligned_logprobs(
    raw_logprobs: Any,
    token_count: int,
) -> list[Any | None]:
    values = list(raw_logprobs or [])
    if len(values) < token_count:
        values.extend([None] * (token_count - len(values)))
    return values[:token_count]


def _append_stops(params: Any, *required_stops: str) -> None:
    current_stop = getattr(params, "stop", None)
    stops = [current_stop] if isinstance(current_stop, str) else list(
        current_stop or [])
    for stop in required_stops:
        if stop not in stops:
            stops.append(stop)
    params.stop = stops


def _configure_thinking_stop(params: Any) -> None:
    _append_stops(params, THINK_END_TAG, TOOL_CALL_START_TAG)
    # These two internal stops are also parser-visible protocol boundaries.
    # Keeping them in the child output prevents a token-id/text mismatch at
    # the DelegatingParser handoff.
    params.include_stop_str_in_output = True


def _configure_final_answer_stop(params: Any) -> None:
    _append_stops(params, THINK_END_TAG)
    params.include_stop_str_in_output = False


def _last_chunk_contains_boundary(
    last_chunk: object,
    boundary: ReasoningBoundary,
) -> bool:
    try:
        output = last_chunk.outputs[0]
        return boundary.marker in (getattr(output, "text", "") or "")
    except (AttributeError, IndexError, TypeError):
        return False


def _detect_reasoning_boundary(
    context: RequestStateContext,
    text: str,
    token_ids: list[int],
    stop_reason: object = None,
) -> ReasoningBoundary | None:
    text_candidates: list[tuple[int, ReasoningBoundary]] = []
    token_candidates: list[tuple[int, ReasoningBoundary]] = []
    stop_candidate: ReasoningBoundary | None = None
    for boundary in ReasoningBoundary:
        text_index = text.find(boundary.marker)
        marker_ids = list(context.encode_prompt_suffix(boundary.marker))
        token_index = context._find_token_subsequence(token_ids, marker_ids)
        if text_index >= 0:
            text_candidates.append((text_index, boundary))
        if token_index is not None:
            token_candidates.append((token_index, boundary))
        if stop_reason == boundary.marker:
            stop_candidate = boundary
    if stop_candidate is not None:
        return stop_candidate
    if text_candidates:
        return min(text_candidates, key=lambda item: item[0])[1]
    if token_candidates:
        return min(token_candidates, key=lambda item: item[0])[1]
    return stop_candidate


def _align_stop_boundary_delta(
    context: RequestStateContext,
    text: str,
    token_ids: list[int],
    boundary: ReasoningBoundary | None,
    stop_reason: object,
    *,
    prior_text: str = "",
    prior_token_ids: tuple[int, ...] | list[int] = (),
) -> tuple[str, list[int], bool]:
    """Drop speculative token suffixes hidden by a string stop boundary."""
    if boundary is None or stop_reason != boundary.marker:
        return text, token_ids, False
    cumulative_text = prior_text + text
    if not cumulative_text.endswith(boundary.marker):
        return text, token_ids, False

    prior_token_count = len(prior_token_ids)
    cumulative_token_ids = [*prior_token_ids, *token_ids]
    marker_ids = list(context.encode_prompt_suffix(boundary.marker))
    if not marker_ids:
        raise RuntimeError(
            f"reasoning stop boundary encoded to no token ids: "
            f"{boundary.marker}"
        )
    marker_token_start = next(
        (
            index
            for index in range(
                len(cumulative_token_ids) - len(marker_ids),
                -1,
                -1,
            )
            if cumulative_token_ids[index:index + len(marker_ids)]
            == marker_ids
        ),
        None,
    )
    if marker_token_start is None:
        raise RuntimeError(
            "reasoning stop boundary is visible in text but absent from "
            f"token ids: {boundary.marker}"
        )
    marker_token_end = marker_token_start + len(marker_ids) - prior_token_count
    if marker_token_end <= 0:
        raise RuntimeError(
            "reasoning stop boundary token ids ended outside the terminal "
            "delta; already-forwarded token ids cannot be retracted"
        )
    if marker_token_end >= len(token_ids):
        return text, token_ids, False
    return text, token_ids[:marker_token_end], True


async def _emit_reasoning_boundary_once(
    context: RequestStateContext,
    boundary: ReasoningBoundary = ReasoningBoundary.THINK_END,
) -> bool:
    """Emit one controller-selected boundary without copying request metrics."""
    if boundary is not ReasoningBoundary.THINK_END:
        raise ValueError("only </think> may be synthesized by the controller")
    if context.reasoning_boundary_emitted:
        return False
    last_chunk = context.last_chunk
    if last_chunk is None:
        return False
    if _last_chunk_contains_boundary(last_chunk, boundary):
        context.reasoning_boundary_emitted = True
        return False
    result = copy.copy(last_chunk)
    output = copy.copy(last_chunk.outputs[0])
    result.outputs = [output, *last_chunk.outputs[1:]]
    output.text = boundary.marker
    output.token_ids = list(context.encode_prompt_suffix(boundary.marker))
    output.finish_reason = None
    output.stop_reason = None
    result.finished = False
    await context.stream_output(
        result,
        phase="reasoning_boundary",
        child_request_id=f"{context.request_id}_reasoning_boundary",
        event="synthetic_boundary",
        controller_state="thinking",
        next_state="free_final_answer",
        reasoning_boundary=boundary,
    )
    context.reasoning_boundary_emitted = True
    return True


async def _emit_think_end_once(context: RequestStateContext) -> bool:
    """Compatibility wrapper for the reviewed reasoning-boundary helper."""
    return await _emit_reasoning_boundary_once(context)


class _BaseState(ABC):
    @staticmethod
    async def handle(
        context: RequestStateContext,
    ) -> Optional[Type["_BaseState"]]:
        raise NotImplementedError

    @staticmethod
    def log_stage(
        context: RequestStateContext,
        event: str,
        stage: str,
        **details: Any,
    ) -> None:
        if not context.config.verbose_stage_logging:
            return
        state = context.algorithm_state
        print(
            "TB_STAGE "
            f"event={event} request_id={context.request_id} "
            f"stage={stage} thinking_segments={state.thinking_segments} "
            f"probe_round={state.probe_round} "
            f"api_calls={state.api_call_count} details={details}",
            flush=True,
        )

    @staticmethod
    def next_engine_request_id(
        context: RequestStateContext,
        stage: str,
    ) -> str:
        state = context.algorithm_state
        maximum = context.config.max_internal_calls
        if maximum > 0 and state.api_call_count >= maximum:
            raise RuntimeError(
                "Think Budget max_internal_calls reached for "
                f"{context.request_id}: {maximum}")
        state.api_call_count += 1
        return f"{context.request_id}_{stage}_{state.api_call_count}"

    @staticmethod
    def has_call_room(
        context: RequestStateContext,
        required_calls: int,
    ) -> bool:
        maximum = context.config.max_internal_calls
        return maximum <= 0 or (
            context.algorithm_state.api_call_count + required_calls
            <= maximum
        )

    @staticmethod
    def enter_final_answer(
        context: RequestStateContext,
        reason: FinalAnswerReason,
    ) -> Type["_FinalAnswerState"]:
        """Record why FinalAnswer was selected before changing state."""
        context.algorithm_state.final_answer_reason = reason
        return _FinalAnswerState

    @staticmethod
    def remaining_think_budget(context: RequestStateContext) -> int:
        return max(
            0,
            context.budget_plan.hard_think_token_limit
            - context.algorithm_state.thinking_tokens,
        )

    @staticmethod
    def remaining_context(context: RequestStateContext) -> int:
        return max(
            0,
            int(context.serving_chat.model_config.max_model_len)
            - len(context.engine_prompt.get("prompt_token_ids", [])),
        )

    @staticmethod
    async def consume_output(
        gen: AsyncGenerator,
        context: RequestStateContext,
        *,
        phase: str,
        child_request_id: str,
        output_kind: Any = None,
        collect_reasoning_logprobs: bool = False,
    ) -> tuple[
        list[int],
        int,
        Optional[RequestOutput],
        str,
        object,
        ReasoningBoundary | None,
    ]:
        """Collect a visible thought and align internal string-stop output."""
        del collect_reasoning_logprobs
        kind = _output_kind_name(
            output_kind
            if output_kind is not None
            else ("DELTA" if getattr(context.request, "stream", False)
                  else "FINAL_ONLY")
        )
        full_text = ""
        all_token_ids: list[int] = []
        raw_full_text = ""
        raw_all_token_ids: list[int] = []
        raw_generated_tokens = 0
        last_output = None
        terminal_reason = None
        detected_boundary: ReasoningBoundary | None = None
        async for result in gen:
            source_output = result.outputs[0]
            chunk_text = source_output.text or ""
            chunk_token_ids = list(source_output.token_ids or [])
            prior_raw_text = raw_full_text if kind == "DELTA" else ""
            prior_raw_token_ids = (
                raw_all_token_ids if kind == "DELTA" else []
            )
            raw_full_text, raw_all_token_ids = _replace_or_append(
                kind,
                raw_full_text,
                raw_all_token_ids,
                chunk_text,
                chunk_token_ids,
            )
            if kind == "DELTA":
                raw_generated_tokens += len(chunk_token_ids)
            else:
                raw_generated_tokens = len(chunk_token_ids)

            chunk_boundary = _detect_reasoning_boundary(
                context,
                chunk_text,
                chunk_token_ids,
                source_output.stop_reason,
            )
            (
                chunk_text,
                chunk_token_ids,
                stop_boundary_aligned,
            ) = _align_stop_boundary_delta(
                context,
                chunk_text,
                chunk_token_ids,
                chunk_boundary,
                source_output.stop_reason,
                prior_text=prior_raw_text,
                prior_token_ids=prior_raw_token_ids,
            )

            full_text, all_token_ids = _replace_or_append(
                kind,
                full_text,
                all_token_ids,
                chunk_text,
                chunk_token_ids,
            )
            if source_output.finish_reason is not None:
                terminal_reason = (
                    source_output.stop_reason
                    or source_output.finish_reason
                )

            if chunk_boundary is not None:
                if (
                    detected_boundary is not None
                    and detected_boundary is not chunk_boundary
                ):
                    raise RuntimeError(
                        "one thinking child emitted multiple reasoning "
                        "boundary types")
                detected_boundary = chunk_boundary

            visible_result = copy.copy(result)
            visible_output = copy.copy(source_output)
            visible_result.outputs = [
                visible_output,
                *result.outputs[1:],
            ]
            visible_output.text = chunk_text
            visible_output.token_ids = chunk_token_ids
            if (
                stop_boundary_aligned
                and getattr(source_output, "logprobs", None) is not None
            ):
                visible_output.logprobs = _aligned_logprobs(
                    source_output.logprobs,
                    len(chunk_token_ids),
                )
            visible_output.finish_reason = None
            visible_output.stop_reason = None
            visible_result.finished = False
            if chunk_text or chunk_token_ids:
                await context.stream_output(
                    visible_result,
                    source_result=result,
                    phase=phase,
                    child_request_id=child_request_id,
                    controller_state="thinking",
                    next_state=(
                        "free_final_answer"
                        if chunk_boundary is not None
                        else "thinking"
                    ),
                    reasoning_boundary=chunk_boundary,
                    stop_boundary_aligned=stop_boundary_aligned,
                )
                context.last_chunk = visible_result
            last_output = result

        if detected_boundary is None:
            detected_boundary = _detect_reasoning_boundary(
                context,
                full_text,
                all_token_ids,
                terminal_reason,
            )
        return (
            all_token_ids,
            raw_generated_tokens,
            last_output,
            full_text,
            terminal_reason,
            detected_boundary,
        )

    @staticmethod
    async def consume_trial_output(
        gen: AsyncGenerator,
        *,
        output_kind: Any = "DELTA",
    ) -> TrialObservation:
        """Collect the complete hidden trial for DELTA/FINAL/CUMULATIVE modes."""
        kind = _output_kind_name(output_kind)
        text = ""
        token_ids: list[int] = []
        sample_logprobs: list[Any | None] = []
        sampled_token_ids: list[int] = []
        finish_reason = None
        stop_reason = None
        last_output = None

        async for result in gen:
            output = result.outputs[0]
            chunk_text = output.text or ""
            if THINK_END_TAG in chunk_text:
                chunk_text = chunk_text.split(THINK_END_TAG, 1)[0]
            chunk_token_ids = list(output.token_ids or [])
            chunk_logprobs = _aligned_logprobs(
                output.logprobs, len(chunk_token_ids))
            if kind == "DELTA":
                text += chunk_text
                token_ids.extend(chunk_token_ids)
                sampled_token_ids.extend(chunk_token_ids)
                sample_logprobs.extend(chunk_logprobs)
            else:
                text = chunk_text
                token_ids = chunk_token_ids
                sampled_token_ids = list(chunk_token_ids)
                sample_logprobs = chunk_logprobs
            if output.finish_reason is not None:
                finish_reason = output.finish_reason
            if output.stop_reason is not None:
                stop_reason = output.stop_reason
            last_output = result

        return TrialObservation(
            full_text=text,
            all_token_ids=token_ids,
            sample_logprobs=sample_logprobs,
            sampled_token_ids=sampled_token_ids,
            generated_tokens=len(token_ids),
            finish_reason=finish_reason,
            stop_reason=stop_reason,
            last_output=last_output,
            output_kind=kind,
        )


class _ThinkingState(_BaseState):
    """Generate check_length first, then one stride after each failed probe."""

    @staticmethod
    async def handle(
        context: RequestStateContext,
    ) -> Optional[Type[_BaseState]]:
        state = context.algorithm_state
        if state.reasoning_state is ReasoningState.CLOSED:
            raise RuntimeError("ThinkingState cannot run after FinalAnswerState")
        if safety_gate_check(
            context.elapsed,
            context.config.baseline_time_limit,
            context.config.enable_safety_gate,
        ):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        remaining_budget = _BaseState.remaining_think_budget(context)
        remaining_context = _BaseState.remaining_context(context)
        forced_final_reserve = len(context.encode_prompt_suffix(
            BUDGET_EXHAUSTED_FINAL_PROMPT)) + 1
        available_think_context = remaining_context - forced_final_reserve
        if remaining_budget <= 0 or available_think_context <= 0:
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        if not _BaseState.has_call_room(context, 2):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)

        first_segment = state.thinking_segments == 0
        requested_tokens = (
            context.budget_plan.budget.check_length
            if first_segment
            else context.budget_plan.budget.think_stride
        )
        maximum = min(
            requested_tokens,
            remaining_budget,
            available_think_context,
        )
        if maximum <= 0:
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)

        phase = "initial_think" if first_segment else "iterative_think"
        phase_started = perf_counter()
        controller_started = perf_counter()
        prompt_tokens = len(context.engine_prompt["prompt_token_ids"])
        params = context.request.to_sampling_params(
            maximum, context.default_sampling_params)
        _configure_thinking_stop(params)
        output_kind = getattr(params, "output_kind", None)
        child_request_id = _BaseState.next_engine_request_id(
            context, phase)
        controller_cpu_ms = (
            perf_counter() - controller_started) * 1000

        with context.timing.measure(f"engine.{phase}"):
            generator = context.engine_client.generate(
                context.engine_prompt,
                params,
                child_request_id,
                **context.engine_request_kwargs(),
            )
            (
                generated_token_ids,
                raw_generated_tokens,
                last_output,
                generated_text,
                stop_reason,
                reasoning_boundary,
            ) = await _BaseState.consume_output(
                generator,
                context,
                phase=phase,
                child_request_id=child_request_id,
                output_kind=output_kind,
            )
        if last_output is None:
            raise RuntimeError(f"{phase} completed without a vLLM output")

        controller_started = perf_counter()
        (
            visible_thinking_tokens,
            boundary_final_tokens,
        ) = context.append_thinking(
            generated_token_ids,
            generated_text,
            last_output,
            reasoning_boundary,
        )
        generated_tokens = raw_generated_tokens
        controller_cpu_ms += (
            perf_counter() - controller_started) * 1000
        wall_ms = (perf_counter() - phase_started) * 1000
        context.timing.record_phase(
            parent_request_id=context.request_id,
            child_request_id=child_request_id,
            phase=phase,
            round_id=0 if first_segment else state.thinking_segments - 1,
            wall_ms=wall_ms,
            controller_cpu_ms=controller_cpu_ms,
            prompt_tokens=prompt_tokens,
            generated_tokens=generated_tokens,
            visible=True,
            output=last_output,
            stop_reason=stop_reason,
            ledger_visible_think_tokens=visible_thinking_tokens,
            ledger_final_answer_tokens=boundary_final_tokens,
        )

        if reasoning_boundary is not None:
            reason = (
                FinalAnswerReason.MODEL_TOOL_CALL
                if reasoning_boundary is ReasoningBoundary.TOOL_CALL
                else FinalAnswerReason.MODEL_THINK_END
            )
            return _BaseState.enter_final_answer(context, reason)
        if generated_tokens <= 0:
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        if (
            _BaseState.remaining_think_budget(context) <= 0
            or _BaseState.remaining_context(context) <= forced_final_reserve
        ):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        # After the final failed probe, the required stride is generated and
        # then the fresh final answer starts without another probe.
        if (
            state.probe_round
            >= context.budget_plan.budget.num_thinking
        ):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        if not _BaseState.has_call_room(context, 2):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        return _ConfidenceProbeState


class _ConfidenceProbeState(_BaseState):
    """Generate one hidden <=50-token trial from a shallow child prompt."""

    @staticmethod
    async def handle(
        context: RequestStateContext,
    ) -> Optional[Type[_BaseState]]:
        state = context.algorithm_state
        if state.reasoning_state is ReasoningState.CLOSED:
            raise RuntimeError(
                "ConfidenceProbeState cannot run after FinalAnswerState")
        if (
            state.probe_round
            >= context.budget_plan.budget.num_thinking
        ):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        if not _BaseState.has_call_room(context, 2):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        if safety_gate_check(
            context.elapsed,
            context.config.baseline_time_limit,
            context.config.enable_safety_gate,
        ):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)

        phase_started = perf_counter()
        controller_started = perf_counter()
        child_prompt = context.child_prompt(TRIAL_PROMPT)
        child_prompt_text = child_prompt.get("prompt")
        prompt_tokens = len(child_prompt["prompt_token_ids"])
        trial_limit = min(
            context.policy.trial_answer_length,
            max(
                0,
                int(context.serving_chat.model_config.max_model_len)
                - prompt_tokens,
            ),
        )
        requested_limit = (
            getattr(context.request, "max_completion_tokens", None)
            or getattr(context.request, "max_tokens", None)
        )
        if isinstance(requested_limit, int) and requested_limit > 0:
            trial_limit = min(trial_limit, requested_limit)
        if trial_limit <= 0:
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        state.probe_round += 1
        params = context.request.to_sampling_params(
            trial_limit, context.default_sampling_params)
        params.logprobs = 1
        params.stop = [THINK_END_TAG]
        params.include_stop_str_in_output = False
        output_kind = getattr(params, "output_kind", None)
        child_request_id = _BaseState.next_engine_request_id(
            context, "confidence_probe")
        controller_cpu_ms = (
            perf_counter() - controller_started) * 1000

        with context.timing.measure("engine.confidence_probe"):
            generator = context.engine_client.generate(
                child_prompt,
                params,
                child_request_id,
                **context.engine_request_kwargs(
                    prompt_text=child_prompt_text,
                    reasoning_ended=True,
                ),
            )
            observation = await _BaseState.consume_trial_output(
                generator,
                output_kind=output_kind,
            )
        if observation.last_output is None:
            raise RuntimeError(
                "confidence_probe completed without a vLLM output")

        controller_started = perf_counter()
        state.current_trial = observation
        state.hidden_probe_tokens += observation.generated_tokens
        assessment = assess_trial_confidence(
            observation,
            enable_value_min_confidence=(
                context.config.enable_value_min_confidence),
            value_min_bottom_k=context.config.value_min_bottom_k,
        )
        state.last_confidence = assessment.score
        if not assessment.available:
            accepted = True
            accept_reason = "confidence_unavailable_fail_open"
        elif (
            state.thinking_tokens
            < context.budget_plan.budget.min_think_budget
        ):
            accepted = False
            accept_reason = "min_think_budget"
        else:
            accepted = bool(
                assessment.score is not None
                and assessment.score
                >= context.budget_plan.confidence_threshold
            )
            accept_reason = (
                assessment.method if accepted else "below_threshold")

        controller_cpu_ms += (
            perf_counter() - controller_started) * 1000
        wall_ms = (perf_counter() - phase_started) * 1000
        context.timing.record_phase(
            parent_request_id=context.request_id,
            child_request_id=child_request_id,
            phase="confidence_probe",
            round_id=state.probe_round,
            wall_ms=wall_ms,
            controller_cpu_ms=controller_cpu_ms,
            prompt_tokens=prompt_tokens,
            generated_tokens=observation.generated_tokens,
            visible=False,
            output=observation.last_output,
            confidence=assessment.score,
            accepted=accepted,
            confidence_method=assessment.method,
            average_confidence=assessment.average_confidence,
            value_min_raw=assessment.value_min_raw,
            pair_count=assessment.pair_count,
            value_pair_count=assessment.value_pair_count,
            accept_reason=accept_reason,
            stop_reason=(
                observation.stop_reason or observation.finish_reason),
        )
        _BaseState.log_stage(
            context,
            "complete",
            "confidence_probe",
            generated_tokens=observation.generated_tokens,
            confidence=assessment.score,
            confidence_method=assessment.method,
            accepted=accepted,
            accept_reason=accept_reason,
        )
        if accepted:
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.CONFIDENCE_ACCEPTED)
        return _ThinkingState


class _FinalAnswerState(_BaseState):
    """Continue freely after either a model or controller reasoning boundary."""

    @staticmethod
    async def handle(
        context: RequestStateContext,
    ) -> Optional[Type[_BaseState]]:
        state = context.algorithm_state
        phase_started = perf_counter()
        controller_started = perf_counter()
        reason = state.final_answer_reason
        if reason is None:
            raise RuntimeError(
                "FinalAnswerState requires an explicit final_answer_reason")
        prompt_suffix = None
        if reason is FinalAnswerReason.CONFIDENCE_ACCEPTED:
            prompt_suffix = THINK_END_TAG
        elif reason is FinalAnswerReason.BUDGET_EXHAUSTED:
            prompt_suffix = BUDGET_EXHAUSTED_FINAL_PROMPT
        if state.reasoning_state is ReasoningState.OPEN:
            if prompt_suffix is None:
                raise RuntimeError(
                    "model boundary final answer requires closed reasoning")
            await _emit_reasoning_boundary_once(context)
            context.close_reasoning(prompt_suffix)
        elif not context.reasoning_boundary_emitted:
            raise RuntimeError(
                "closed reasoning has no parser-visible boundary")

        remaining_context = _BaseState.remaining_context(context)
        requested_limit = (
            getattr(context.request, "max_completion_tokens", None)
            or getattr(context.request, "max_tokens", None)
        )
        answer_limit = min(
            remaining_context,
            context.config.final_answer_max_tokens,
        )
        if isinstance(requested_limit, int) and requested_limit > 0:
            answer_limit = min(answer_limit, requested_limit)
        if answer_limit <= 0:
            raise RuntimeError(
                "no context space remains for the final answer")

        params = context.request.to_sampling_params(
            answer_limit, context.default_sampling_params)
        _configure_final_answer_stop(params)
        output_kind = _output_kind_name(
            getattr(params, "output_kind", None))
        prompt_tokens = len(context.engine_prompt["prompt_token_ids"])
        child_request_id = _BaseState.next_engine_request_id(
            context, "final_answer")
        controller_cpu_ms = (
            perf_counter() - controller_started) * 1000
        generated_tokens = 0
        last_output = None
        terminal_output = None
        propagated_error = False

        with context.timing.measure("engine.final_answer"):
            async for result in context.engine_client.generate(
                context.engine_prompt,
                params,
                child_request_id,
                **context.engine_request_kwargs(reasoning_ended=True),
            ):
                if not context.reasoning_boundary_emitted:
                    # A hard limit can enter FinalAnswer before any visible
                    # thought chunk exists.  Use the first final output only as
                    # a shallow RequestOutput template for the close marker.
                    context.last_chunk = result
                    await _emit_reasoning_boundary_once(context)
                chunk_tokens = len(result.outputs[0].token_ids or [])
                if output_kind == "DELTA":
                    generated_tokens += chunk_tokens
                else:
                    generated_tokens = chunk_tokens
                last_output = result
                output = result.outputs[0]
                if output.finish_reason is None:
                    await context.stream_output(
                        result,
                        phase="final_answer",
                        child_request_id=child_request_id,
                        controller_state="free_final_answer",
                        next_state="free_final_answer",
                        reasoning_boundary=state.reasoning_boundary,
                    )
                    continue

                if str(output.finish_reason) == "error":
                    await context.stream_output(
                        result,
                        phase="final_answer",
                        child_request_id=child_request_id,
                        event="error_terminal",
                        controller_state="free_final_answer",
                        next_state="error",
                        reasoning_boundary=state.reasoning_boundary,
                    )
                    propagated_error = True
                    break
                if terminal_output is not None:
                    raise RuntimeError(
                        "final answer child emitted multiple terminals")
                terminal_output = result
                if output.text or output.token_ids:
                    body_result = copy.copy(result)
                    body_output = copy.copy(output)
                    body_result.outputs = [
                        body_output,
                        *result.outputs[1:],
                    ]
                    body_output.finish_reason = None
                    body_output.stop_reason = None
                    body_result.finished = False
                    await context.stream_output(
                        body_result,
                        source_result=result,
                        phase="final_answer",
                        child_request_id=child_request_id,
                        event="terminal_body",
                        controller_state="free_final_answer",
                        next_state="terminal_flush",
                        reasoning_boundary=state.reasoning_boundary,
                    )
        if last_output is None:
            raise RuntimeError(
                "final_answer completed without a vLLM output")
        if propagated_error:
            state.completion_status = "error"
            return None
        if terminal_output is None:
            raise RuntimeError(
                "final_answer completed without a terminal output")

        await context.stream_terminal(
            terminal_output,
            phase="final_answer",
            child_request_id=child_request_id,
        )

        state.final_answer_tokens += generated_tokens
        state.completion_status = "success"
        wall_ms = (perf_counter() - phase_started) * 1000
        context.timing.record_phase(
            parent_request_id=context.request_id,
            child_request_id=child_request_id,
            phase="final_answer",
            round_id=state.probe_round,
            wall_ms=wall_ms,
            controller_cpu_ms=controller_cpu_ms,
            prompt_tokens=prompt_tokens,
            generated_tokens=generated_tokens,
            visible=True,
            output=last_output,
            final_answer_reason=reason.value,
        )
        return None


class AsyncReasoningOrchestrator:
    def __init__(self, context: RequestStateContext):
        self.context = context

    async def run(self) -> None:
        current: Optional[Type[_BaseState]] = _ThinkingState
        try:
            while current is not None:
                state_name = current.__name__
                _BaseState.log_stage(
                    self.context, "state_enter", state_name)
                self.context.log_stream_event(
                    event="state_enter",
                    phase=state_name,
                    controller_state=state_name,
                    reasoning_boundary=(
                        self.context.algorithm_state.reasoning_boundary),
                )
                current = await current.handle(self.context)
        except asyncio.CancelledError:
            self.context.algorithm_state.completion_status = "cancelled"
            raise
        except Exception as exc:
            import traceback

            self.context.algorithm_state.completion_status = "error"
            self.context.orchestration_error = str(exc)
            print(
                "TB_ERROR event=orchestration "
                f"request_id={self.context.request_id} error={exc}",
                flush=True,
            )
            print(traceback.format_exc(), flush=True)
        finally:
            print(
                "TB_PROFILE "
                f"request_id={self.context.request_id} "
                f"profile={self.context.timing.snapshot()}",
                flush=True,
            )
            await self.context.stream_output(None, phase="controller_end")


# Rolling-upgrade aliases.  They do not add states; both names refer to the
# single ThinkingState in the reviewed three-state model.
_InitialThinkState = _ThinkingState
_IterativeThinkState = _ThinkingState
_ConfidenceCheckState = _ConfidenceProbeState
_AnswerState = _FinalAnswerState
