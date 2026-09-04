"""Three-state DTB execution on vLLM child ``generate`` calls."""
from __future__ import annotations

import copy
from abc import ABC
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any, Optional, Type, TYPE_CHECKING

from dtb.reasoning.confidence_gates import (
    assess_trial_confidence,
)
from dtb.reasoning.request_context import DtbRequestContext
from dtb.reasoning.parser_boundary import (
    THINK_END_TAG,
    align_stop_boundary_delta,
    configure_thinking_stop,
    detect_reasoning_boundary,
    emit_reasoning_boundary_once,
)
from dtb.reasoning.request_state import (
    FinalAnswerReason,
    ReasoningBoundary,
    ReasoningState,
    TrialObservation,
)
from dtb.reasoning.safety_gate import (
    safety_gate_check,
)

if TYPE_CHECKING:
    from vllm.outputs import RequestOutput


GUIDED_FINAL_CUE_V1 = (
    "Considering the limited time by the user, I have to give the solution "
    "based on the thinking directly now.\n"
)
GUIDED_FINAL_SEPARATOR = "\n\n"
GUIDED_FINAL_PROMPT_V1 = (
    GUIDED_FINAL_CUE_V1 + THINK_END_TAG + GUIDED_FINAL_SEPARATOR
)
# Compatibility alias for callers that still use the former reason-specific name.
BUDGET_EXHAUSTED_FINAL_PROMPT = GUIDED_FINAL_PROMPT_V1
TRIAL_PROMPT = (
    "I have to give the answer based on the thinking directly "
    "without explanation now.\n**Final Answer**\n"
)


def controller_final_suffix(
    reason: FinalAnswerReason,
) -> str | None:
    """Resolve only controller-forced reasoning exits to the verified cue."""
    if reason in {
        FinalAnswerReason.CONFIDENCE_ACCEPTED,
        FinalAnswerReason.BUDGET_EXHAUSTED,
    }:
        return GUIDED_FINAL_PROMPT_V1
    return None


def controller_final_cue(
    reason: FinalAnswerReason,
) -> str | None:
    """Return the cue text without embedding a reasoning boundary marker."""
    if reason in {
        FinalAnswerReason.CONFIDENCE_ACCEPTED,
        FinalAnswerReason.BUDGET_EXHAUSTED,
    }:
        return GUIDED_FINAL_CUE_V1
    return None


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


class _BaseState(ABC):
    @staticmethod
    async def handle(
        context: DtbRequestContext,
    ) -> Optional[Type["_BaseState"]]:
        raise NotImplementedError

    @staticmethod
    def next_engine_request_id(
        context: DtbRequestContext,
        stage: str,
    ) -> str:
        state = context.request_state
        maximum = context.config.max_internal_calls
        if maximum > 0 and state.child_generate_calls >= maximum:
            raise RuntimeError(
                "DTB max_internal_calls reached for "
                f"{context.request_id}: {maximum}")
        state.child_generate_calls += 1
        return f"{context.request_id}_{stage}_{state.child_generate_calls}"

    @staticmethod
    def has_call_room(
        context: DtbRequestContext,
        required_calls: int,
    ) -> bool:
        maximum = context.config.max_internal_calls
        return maximum <= 0 or (
            context.request_state.child_generate_calls + required_calls
            <= maximum
        )

    @staticmethod
    def enter_final_answer(
        context: DtbRequestContext,
        reason: FinalAnswerReason,
    ) -> Type["_FinalAnswerState"]:
        """Record why FinalAnswer was selected before changing state."""
        context.request_state.final_answer_reason = reason
        return _FinalAnswerState

    @staticmethod
    def remaining_think_budget(context: DtbRequestContext) -> int:
        return max(
            0,
            context.budget_decision.hard_think_token_limit
            - context.request_state.thinking_tokens,
        )

    @staticmethod
    def remaining_completion_budget(context: DtbRequestContext) -> int:
        state = context.request_state
        return max(
            0,
            state.completion_token_limit - state.emitted_completion_tokens,
        )

    @staticmethod
    def remaining_context(context: DtbRequestContext) -> int:
        return max(
            0,
            int(context.serving_chat.model_config.max_model_len)
            - len(context.engine_prompt.get("prompt_token_ids", [])),
        )

    @staticmethod
    async def consume_output(
        gen: AsyncGenerator,
        context: DtbRequestContext,
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

            chunk_boundary = detect_reasoning_boundary(
                context,
                chunk_text,
                chunk_token_ids,
                source_output.stop_reason,
            )
            (
                chunk_text,
                chunk_token_ids,
                stop_boundary_aligned,
            ) = align_stop_boundary_delta(
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
            detected_boundary = detect_reasoning_boundary(
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
        context: DtbRequestContext,
    ) -> Optional[Type[_BaseState]]:
        state = context.request_state
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
        if getattr(context.request, "stream", False):
            protocol = context.require_reasoning_protocol()
            forced_final_reserve = (
                len(context.encode_prompt_suffix(GUIDED_FINAL_CUE_V1))
                + len(protocol.forced_close_token_ids)
                + len(context.encode_prompt_suffix(GUIDED_FINAL_SEPARATOR))
                + 1
            )
            forced_boundary_tokens = len(protocol.forced_close_token_ids)
        else:
            forced_final_reserve = len(context.encode_prompt_suffix(
                GUIDED_FINAL_PROMPT_V1)) + 1
            forced_boundary_tokens = len(context.encode_prompt_suffix(
                THINK_END_TAG
            ))
        available_think_context = remaining_context - forced_final_reserve
        forced_completion_reserve = forced_boundary_tokens + 1
        available_think_completion = (
            _BaseState.remaining_completion_budget(context)
            - forced_completion_reserve
        )
        if (
            remaining_budget <= 0
            or available_think_context <= 0
            or available_think_completion <= 0
        ):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        if not _BaseState.has_call_room(context, 2):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)

        first_segment = state.thinking_segments == 0
        requested_tokens = (
            context.budget_decision.budget.check_length
            if first_segment
            else context.budget_decision.budget.think_stride
        )
        maximum = min(
            requested_tokens,
            remaining_budget,
            available_think_context,
            available_think_completion,
        )
        if maximum <= 0:
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)

        phase = "initial_think" if first_segment else "iterative_think"
        recorder = context.phase_recorder
        phase_started = perf_counter() if recorder is not None else 0.0
        controller_started = perf_counter() if recorder is not None else 0.0
        prompt_tokens = len(context.engine_prompt["prompt_token_ids"])
        params = context.request.to_sampling_params(
            maximum, context.default_sampling_params)
        configure_thinking_stop(params, context)
        output_kind = getattr(params, "output_kind", None)
        child_request_id = _BaseState.next_engine_request_id(
            context, phase)
        child_prompt = context.engine_input()
        engine_kwargs = context.engine_request_kwargs(child_prompt)
        controller_cpu_ms = (
            (perf_counter() - controller_started) * 1000
            if recorder is not None else 0.0
        )

        context.record_first_engine_submission()
        generator = context.engine_client.generate(
            child_prompt,
            params,
            child_request_id,
            **engine_kwargs,
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

        controller_started = perf_counter() if recorder is not None else 0.0
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
        if recorder is not None:
            controller_cpu_ms += (
                perf_counter() - controller_started) * 1000
            wall_ms = (perf_counter() - phase_started) * 1000
            recorder.record_phase(
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
            or _BaseState.remaining_completion_budget(context)
            <= forced_completion_reserve
        ):
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.BUDGET_EXHAUSTED)
        # After the final failed probe, the required stride is generated and
        # then the fresh final answer starts without another probe.
        if (
            state.probe_round
            >= context.budget_decision.budget.num_thinking
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
        context: DtbRequestContext,
    ) -> Optional[Type[_BaseState]]:
        state = context.request_state
        if state.reasoning_state is ReasoningState.CLOSED:
            raise RuntimeError(
                "ConfidenceProbeState cannot run after FinalAnswerState")
        if (
            state.probe_round
            >= context.budget_decision.budget.num_thinking
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

        recorder = context.phase_recorder
        phase_started = perf_counter() if recorder is not None else 0.0
        controller_started = perf_counter() if recorder is not None else 0.0
        child_prompt = context.child_prompt(TRIAL_PROMPT)
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
        output_kind = getattr(params, "output_kind", None)
        child_request_id = _BaseState.next_engine_request_id(
            context, "confidence_probe")
        engine_kwargs = context.engine_request_kwargs(
            child_prompt, reasoning_ended=True)
        controller_cpu_ms = (
            (perf_counter() - controller_started) * 1000
            if recorder is not None else 0.0
        )

        context.record_first_engine_submission()
        generator = context.engine_client.generate(
            child_prompt,
            params,
            child_request_id,
            **engine_kwargs,
        )
        observation = await _BaseState.consume_trial_output(
            generator,
            output_kind=output_kind,
        )
        if observation.last_output is None:
            raise RuntimeError(
                "confidence_probe completed without a vLLM output")

        controller_started = perf_counter() if recorder is not None else 0.0
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
            < context.budget_decision.budget.min_think_budget
        ):
            accepted = False
            accept_reason = "min_think_budget"
        else:
            accepted = bool(
                assessment.score is not None
                and assessment.score
                >= context.budget_decision.confidence_threshold
            )
            accept_reason = (
                assessment.method if accepted else "below_threshold")

        if recorder is not None:
            controller_cpu_ms += (
                perf_counter() - controller_started) * 1000
            wall_ms = (perf_counter() - phase_started) * 1000
            recorder.record_phase(
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
        if accepted:
            return _BaseState.enter_final_answer(
                context, FinalAnswerReason.CONFIDENCE_ACCEPTED)
        return _ThinkingState


class _FinalAnswerState(_BaseState):
    """Continue freely after either a model or controller reasoning boundary."""

    @staticmethod
    async def handle(
        context: DtbRequestContext,
    ) -> Optional[Type[_BaseState]]:
        state = context.request_state
        recorder = context.phase_recorder
        phase_started = perf_counter() if recorder is not None else 0.0
        controller_started = perf_counter() if recorder is not None else 0.0
        reason = state.final_answer_reason
        if reason is None:
            raise RuntimeError(
                "FinalAnswerState requires an explicit final_answer_reason")
        prompt_suffix = controller_final_suffix(reason)
        if state.reasoning_state is ReasoningState.OPEN:
            if prompt_suffix is None:
                raise RuntimeError(
                    "model boundary final answer requires closed reasoning")
            await emit_reasoning_boundary_once(context)
            if getattr(context.request, "stream", False):
                prompt_cue = controller_final_cue(reason)
                if prompt_cue is None:
                    raise RuntimeError(
                        "controller-forced final answer requires a prompt cue"
                    )
                context.close_reasoning(
                    prompt_cue,
                    GUIDED_FINAL_SEPARATOR,
                )
            else:
                context.close_reasoning(prompt_suffix)
        elif not context.reasoning_boundary_emitted:
            raise RuntimeError(
                "closed reasoning has no parser-visible boundary")

        remaining_context = _BaseState.remaining_context(context)
        emitted_before_final = state.emitted_completion_tokens
        pending_boundary_tokens = (
            0
            if context.reasoning_boundary_emitted
            else (
                len(context.require_reasoning_protocol().forced_close_token_ids)
                if getattr(context.request, "stream", False)
                else len(context.encode_prompt_suffix(THINK_END_TAG))
            )
        )
        external_remaining = max(
            0,
            state.completion_token_limit
            - emitted_before_final
            - pending_boundary_tokens,
        )
        answer_limit = min(
            remaining_context,
            external_remaining,
        )
        if answer_limit <= 0:
            raise RuntimeError(
                "no context or completion token budget remains for the "
                "final answer")

        params = context.request.to_sampling_params(
            answer_limit, context.default_sampling_params)
        output_kind = _output_kind_name(
            getattr(params, "output_kind", None))
        prompt_tokens = len(context.engine_prompt["prompt_token_ids"])
        child_request_id = _BaseState.next_engine_request_id(
            context, "final_answer")
        child_prompt = context.engine_input()
        engine_kwargs = context.engine_request_kwargs(
            child_prompt, reasoning_ended=True)
        controller_cpu_ms = (
            (perf_counter() - controller_started) * 1000
            if recorder is not None else 0.0
        )
        generated_tokens = 0
        last_output = None
        terminal_output = None
        propagated_error = False

        context.record_first_engine_submission()
        async for result in context.engine_client.generate(
            child_prompt,
            params,
            child_request_id,
            **engine_kwargs,
        ):
            if not context.reasoning_boundary_emitted:
                # A hard limit can enter FinalAnswer before any visible
                # thought chunk exists.  Use the first final output only as
                # a shallow RequestOutput template for the close marker.
                context.last_chunk = result
                await emit_reasoning_boundary_once(context)
            chunk_tokens = len(result.outputs[0].token_ids or [])
            if output_kind == "DELTA":
                generated_tokens += chunk_tokens
            else:
                generated_tokens = chunk_tokens
            last_output = result
            output = result.outputs[0]
            if terminal_output is not None:
                raise RuntimeError(
                    "final answer child emitted multiple terminals"
                    if output.finish_reason is not None
                    else "final answer child emitted output after terminal")
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
            terminal_output = result
        if last_output is None:
            raise RuntimeError(
                "final_answer completed without a vLLM output")
        if propagated_error:
            state.completion_status = "error"
            return None
        if terminal_output is None:
            raise RuntimeError(
                "final_answer completed without a terminal output")

        await context.stream_final_terminal(
            terminal_output,
            phase="final_answer",
            child_request_id=child_request_id,
        )

        state.final_answer_tokens += generated_tokens
        state.completion_status = "success"
        if recorder is not None:
            wall_ms = (perf_counter() - phase_started) * 1000
            recorder.record_phase(
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
                completion_token_limit=state.completion_token_limit,
                emitted_completion_tokens_before=emitted_before_final,
                pending_boundary_tokens=pending_boundary_tokens,
                external_remaining_tokens=external_remaining,
                answer_limit=answer_limit,
            )
        return None
