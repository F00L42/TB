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


def _configure_final_answer_stop(params: Any) -> None:
    current_stop = getattr(params, "stop", None)
    stops = [current_stop] if isinstance(current_stop, str) else list(
        current_stop or [])
    if THINK_END_TAG not in stops:
        stops.append(THINK_END_TAG)
    params.stop = stops
    params.include_stop_str_in_output = False


def _last_chunk_ends_with_think_tag(last_chunk: object) -> bool:
    try:
        return (
            (getattr(last_chunk.outputs[0], "text", "") or "")
            .rstrip()
            .endswith(THINK_END_TAG)
        )
    except (AttributeError, IndexError, TypeError):
        return False


async def _emit_think_end_once(context: RequestStateContext) -> bool:
    """Emit the one visible close marker without deep-copying request metrics."""
    if context.think_end_emitted:
        return False
    last_chunk = context.last_chunk
    if last_chunk is None:
        return False
    if _last_chunk_ends_with_think_tag(last_chunk):
        context.think_end_emitted = True
        return False
    result = copy.copy(last_chunk)
    output = copy.copy(last_chunk.outputs[0])
    result.outputs = [output, *last_chunk.outputs[1:]]
    output.text = THINK_END_TAG
    output.token_ids = list(context.encode_prompt_suffix(THINK_END_TAG))
    output.finish_reason = None
    output.stop_reason = None
    await context.stream_output(result)
    context.think_end_emitted = True
    return True


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
        output_kind: Any = None,
        collect_reasoning_logprobs: bool = False,
    ) -> tuple[list[int], Optional[RequestOutput], str, object]:
        """Collect and forward a visible thought without post-hoc truncation."""
        del collect_reasoning_logprobs
        kind = _output_kind_name(
            output_kind
            if output_kind is not None
            else ("DELTA" if getattr(context.request, "stream", False)
                  else "FINAL_ONLY")
        )
        full_text = ""
        all_token_ids: list[int] = []
        last_output = None
        terminal_reason = None
        async for result in gen:
            source_output = result.outputs[0]
            chunk_text = source_output.text or ""
            chunk_token_ids = list(source_output.token_ids or [])
            if THINK_END_TAG in chunk_text:
                context.algorithm_state.attempted_early_stop = True
                chunk_text = chunk_text.split(THINK_END_TAG, 1)[0]
                chunk_token_ids = list(
                    context.tokenizer.encode(
                        chunk_text, add_special_tokens=False))

            full_text, all_token_ids = _replace_or_append(
                kind,
                full_text,
                all_token_ids,
                chunk_text,
                chunk_token_ids,
            )
            if source_output.finish_reason:
                terminal_reason = (
                    source_output.stop_reason
                    or source_output.finish_reason
                )

            visible_result = copy.copy(result)
            visible_output = copy.copy(source_output)
            visible_result.outputs = [
                visible_output,
                *result.outputs[1:],
            ]
            visible_output.text = chunk_text
            visible_output.token_ids = chunk_token_ids
            visible_output.finish_reason = None
            visible_output.stop_reason = None
            if chunk_text or chunk_token_ids:
                await context.stream_output(visible_result)
                context.last_chunk = visible_result
            last_output = result

        if terminal_reason == THINK_END_TAG:
            context.algorithm_state.attempted_early_stop = True
        return all_token_ids, last_output, full_text, terminal_reason

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
            return _FinalAnswerState
        remaining_budget = _BaseState.remaining_think_budget(context)
        remaining_context = _BaseState.remaining_context(context)
        close_reserve = len(
            context.encode_prompt_suffix(THINK_END_TAG)) + 1
        available_think_context = remaining_context - close_reserve
        if remaining_budget <= 0 or available_think_context <= 0:
            return _FinalAnswerState
        if not _BaseState.has_call_room(context, 2):
            return _FinalAnswerState

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
            return _FinalAnswerState

        phase = "initial_think" if first_segment else "iterative_think"
        phase_started = perf_counter()
        controller_started = perf_counter()
        prompt_tokens = len(context.engine_prompt["prompt_token_ids"])
        params = context.request.to_sampling_params(
            maximum, context.default_sampling_params)
        params.stop = [THINK_END_TAG]
        params.include_stop_str_in_output = False
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
                last_output,
                generated_text,
                stop_reason,
            ) = await _BaseState.consume_output(
                generator,
                context,
                output_kind=output_kind,
            )
        if last_output is None:
            raise RuntimeError(f"{phase} completed without a vLLM output")

        controller_started = perf_counter()
        context.append_thinking(
            generated_token_ids,
            generated_text,
            last_output,
        )
        generated_tokens = len(generated_token_ids)
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
        )

        if generated_tokens <= 0:
            return _FinalAnswerState
        if stop_reason == THINK_END_TAG:
            state.attempted_early_stop = True
        if (
            state.attempted_early_stop
            and state.thinking_tokens
            < context.budget_plan.budget.min_think_budget
        ):
            return _ThinkingState
        if (
            _BaseState.remaining_think_budget(context) <= 0
            or _BaseState.remaining_context(context) <= close_reserve
        ):
            return _FinalAnswerState
        # After the final failed probe, the required stride is generated and
        # then the fresh final answer starts without another probe.
        if (
            state.probe_round
            >= context.budget_plan.budget.num_thinking
        ):
            return _FinalAnswerState
        if not _BaseState.has_call_room(context, 2):
            return _FinalAnswerState
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
            return _FinalAnswerState
        if not _BaseState.has_call_room(context, 2):
            return _FinalAnswerState
        if safety_gate_check(
            context.elapsed,
            context.config.baseline_time_limit,
            context.config.enable_safety_gate,
        ):
            return _FinalAnswerState

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
            return _FinalAnswerState
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
        return _FinalAnswerState if accepted else _ThinkingState


class _FinalAnswerState(_BaseState):
    """Close reasoning once and generate a fresh answer from the main prompt."""

    @staticmethod
    async def handle(
        context: RequestStateContext,
    ) -> Optional[Type[_BaseState]]:
        state = context.algorithm_state
        phase_started = perf_counter()
        controller_started = perf_counter()
        if state.reasoning_state is ReasoningState.OPEN:
            await _emit_think_end_once(context)
            context.close_reasoning()

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

        with context.timing.measure("engine.final_answer"):
            async for result in context.engine_client.generate(
                context.engine_prompt,
                params,
                child_request_id,
                **context.engine_request_kwargs(reasoning_ended=True),
            ):
                if not context.think_end_emitted:
                    # A hard limit can enter FinalAnswer before any visible
                    # thought chunk exists.  Use the first final output only as
                    # a shallow RequestOutput template for the close marker.
                    context.last_chunk = result
                    await _emit_think_end_once(context)
                chunk_tokens = len(result.outputs[0].token_ids or [])
                if output_kind == "DELTA":
                    generated_tokens += chunk_tokens
                else:
                    generated_tokens = chunk_tokens
                last_output = result
                await context.stream_output(result)
        if last_output is None:
            raise RuntimeError(
                "final_answer completed without a vLLM output")

        state.final_answer_tokens = generated_tokens
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
            try:
                if (
                    self.context.algorithm_state.reasoning_state
                    is ReasoningState.OPEN
                ):
                    await _emit_think_end_once(self.context)
                    self.context.close_reasoning()
            except Exception:
                print(
                    "TB_ERROR event=emit_think_end_failed "
                    f"request_id={self.context.request_id}",
                    flush=True,
                )
        finally:
            print(
                "TB_PROFILE "
                f"request_id={self.context.request_id} "
                f"profile={self.context.timing.snapshot()}",
                flush=True,
            )
            await self.context.stream_output(None)


# Rolling-upgrade aliases.  They do not add states; both names refer to the
# single ThinkingState in the reviewed three-state model.
_InitialThinkState = _ThinkingState
_IterativeThinkState = _ThinkingState
_ConfidenceCheckState = _ConfidenceProbeState
_AnswerState = _FinalAnswerState
