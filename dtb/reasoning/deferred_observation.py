"""Native streaming observation followed by an owned abort-and-drain handoff."""
from __future__ import annotations

import asyncio
import copy
import logging
import sys
from time import perf_counter
from typing import Any

from dtb.reasoning.generation_states import (
    GUIDED_FINAL_CUE_V1, GUIDED_FINAL_SEPARATOR, TRIAL_PROMPT,
    _BaseState, _ConfidenceProbeState, _output_kind_name,
)
from dtb.reasoning.request_context import DtbRequestContext
from dtb.reasoning.request_state import FinalAnswerReason, ReasoningBoundary, ReasoningState


# Fault bound, not a grace window or an engine/scheduler acknowledgement wait.
ABORT_DRAIN_TIMEOUT_S = 10.0
INTERVENTION_POLICY_VERSION = "drain-prefix-v1"
_LOGGER = logging.getLogger(__name__)


def switching_rejection(context: DtbRequestContext, params: Any) -> str | None:
    """Conservative continuation capabilities; unsupported shapes stay native."""
    if context.reasoning_ended is True:
        return "reasoning_already_ended"
    if context.reasoning_protocol is None:
        return "unbound_protocol"
    if _output_kind_name(params) != "DELTA":
        return "non_delta"
    if getattr(params, "stop", None):
        return "user_string_stop"
    if getattr(params, "min_tokens", 0):
        return "min_tokens_state"
    for name, default in (("presence_penalty", 0), ("frequency_penalty", 0),
                          ("repetition_penalty", 1)):
        if getattr(params, name, default) != default:
            return "penalty_state"
    if (getattr(params, "structured_outputs", None)
            or getattr(params, "logits_processors", None)
            or getattr(params, "extra_args", None)):
        return "sampling_state"
    if any(key in context.engine_prompt for key in (
        "multi_modal_data", "multi_modal_inputs", "mm_features", "prompt_embeds",
        "encoder_prompt", "encoder_prompt_token_ids",
    )):
        return "non_text_prompt"
    if not callable(getattr(context.engine_client, "abort", None)):
        return "missing_abort"
    return None


def _validate(result: Any, child_id: str) -> str | None:
    if len(result.outputs) != 1:
        raise RuntimeError("observation requires one source choice")
    output = result.outputs[0]
    if getattr(output, "index", 0) != 0:
        raise RuntimeError("observation source choice changed")
    if getattr(result, "request_id", child_id) != child_id:
        raise RuntimeError("observation source request ID changed")
    reason = output.finish_reason
    if bool(result.finished) != (reason is not None):
        raise RuntimeError("observation received incoherent terminal")
    if reason not in (None, "stop", "length", "abort"):
        raise RuntimeError(f"observation source failed: {reason}")
    return reason


def _abort_payload(result: Any) -> Any:
    """Retain all delta text/IDs, never mutate the source audit object."""
    if result.outputs[0].stop_reason is not None:
        raise RuntimeError("owned abort must have stop_reason=None")
    forwarded = copy.copy(result)
    output = copy.copy(result.outputs[0])
    output.finish_reason = output.stop_reason = None
    # vLLM's empty-token abort can carry logprobs[-0:] (the whole old list).
    if output.logprobs is not None:
        count = len(output.token_ids)
        if count and len(output.logprobs) < count:
            raise RuntimeError("abort payload has incomplete token logprobs")
        output.logprobs = output.logprobs[:count] if count else None
    forwarded.outputs = [output]
    forwarded.finished = False
    for field in ("metrics", "kv_transfer_params"):
        if hasattr(forwarded, field):
            setattr(forwarded, field, None)
    return forwarded


def _room(context: DtbRequestContext, prefix_length: int = 0) -> tuple[bool, bool]:
    """Return (can final, can probe+final); encode suffixes only at activation."""
    room = _BaseState.remaining_context(context) - prefix_length
    completion = _BaseState.remaining_completion_budget(context)
    if context.request_state.reasoning_state is ReasoningState.CLOSED:
        return room >= 1 and completion >= 1 and _BaseState.has_call_room(context, 1), False
    boundary = len(context.require_reasoning_protocol().forced_close_token_ids)
    final_reserve = (len(context.encode_prompt_suffix(GUIDED_FINAL_CUE_V1))
                     + boundary + len(context.encode_prompt_suffix(GUIDED_FINAL_SEPARATOR)) + 1)
    can_final = (room >= final_reserve and completion >= boundary + 1
                 and _BaseState.has_call_room(context, 1))
    can_probe = (can_final and room > len(context.encode_prompt_suffix(TRIAL_PROMPT))
                 and _BaseState.remaining_think_budget(context) > 0
                 and _BaseState.has_call_room(context, 2))
    return can_final, can_probe


class _DeferredInterventionState(_BaseState):
    @staticmethod
    async def handle(context: DtbRequestContext):
        state = context.request_state
        recorder = context.phase_recorder
        started = perf_counter() if recorder is not None else None
        trace = None
        if recorder is not None:
            trace = state.intervention_telemetry = {
                "intervention_policy_version": INTERVENTION_POLICY_VERSION,
                "minimum_intervention_tokens": context.intervention_threshold,
                "threshold_source": context.intervention_threshold_source,
                "intervention_status": "observing", "abort_count": 0,
                "abort_terminal_seen": False, "abort_lost_to_native_terminal": False,
                "native_same_child_completed": False,
                "completion_token_limit": state.completion_token_limit,
                "handoff_text_only_chars": 0,
                "observer_forward_wall_ms": 0.0,
            }
        params = context.observation_params
        if params is None:
            params = context.request.to_sampling_params(
                state.completion_token_limit, context.default_sampling_params)
        rejection = switching_rejection(context, params)
        armed = rejection is None
        classified = armed
        if context.reasoning_ended is True:
            state.reasoning_state = ReasoningState.CLOSED
        if trace is not None:
            trace["activation_skipped_reason"] = rejection
        prefix: list[int] = []
        prompt_length = len(context.engine_prompt["prompt_token_ids"])
        child_id = _BaseState.next_engine_request_id(context, "native_observation")
        # Original input is read-only. No full prompt clone on short requests.
        child_prompt = context.engine_prompt
        context.record_first_engine_submission()
        generator = context.engine_client.generate(
            child_prompt, params, child_id, **context.engine_request_kwargs(child_prompt))
        terminal = None
        exhausted = False
        decision_ms = 0.0
        source_tokens = 0

        async def accept(result, *, owned_abort=False, retain=False, final=False):
            nonlocal armed, classified, source_tokens
            forward_started = perf_counter() if recorder is not None else None
            source_output = result.outputs[0]
            ids = source_output.token_ids or ()
            source_tokens += len(ids)
            hit = (context.require_reasoning_protocol().find_first_boundary(ids)
                   if classified and state.reasoning_state is ReasoningState.OPEN else None)
            if armed or retain:
                prefix.extend(ids)
            if hit is not None:
                index, boundary = hit
                state.thinking_tokens += index
                state.final_answer_tokens += len(ids) - index
                state.reasoning_state = ReasoningState.CLOSED
                state.reasoning_boundary = boundary
                context.reasoning_boundary_emitted = True
                armed = classified = False
            elif state.reasoning_state is ReasoningState.CLOSED:
                state.final_answer_tokens += len(ids)
            elif classified:
                state.thinking_tokens += len(ids)
            else:
                state.unclassified_completion_tokens += len(ids)
            context.last_chunk = result
            if owned_abort and trace is not None and not ids:
                trace["handoff_text_only_chars"] += len(source_output.text or "")
            if final:
                await context.stream_final_terminal(
                    result, phase="native_passthrough", child_request_id=child_id)
            else:
                forwarded = _abort_payload(result) if owned_abort else result
                await context.stream_output(
                    forwarded, source_result=result, phase="native_passthrough",
                    child_request_id=child_id, event="abort_payload" if owned_abort else "delta",
                    reasoning_boundary=state.reasoning_boundary,
                    terminal_flush=owned_abort)
            if trace is not None:
                trace["observer_forward_wall_ms"] += (perf_counter()-forward_started)*1000
            return hit

        async def abort_and_drain():
            nonlocal exhausted
            tail = []
            finish = None
            async with asyncio.timeout(ABORT_DRAIN_TIMEOUT_S):
                abort_started = perf_counter() if recorder is not None else None
                await context.engine_client.abort(child_id)
                if trace is not None:
                    trace["abort_wall_ms"] = (perf_counter() - abort_started) * 1000
                    trace["abort_count"] = 1
                drain_started = perf_counter() if recorder is not None else None
                async for result in generator:
                    if finish is not None:
                        raise RuntimeError("observation output after drain terminal")
                    finish = _validate(result, child_id)
                    tail.append(result)
                exhausted = True
                if trace is not None:
                    trace["abort_drain_wall_ms"] = (perf_counter() - drain_started) * 1000
                if finish is None:
                    raise RuntimeError("observation drain ended without terminal")
            return tail, finish

        try:
            async for result in generator:
                if terminal is not None:
                    raise RuntimeError("observation output after terminal")
                reason = _validate(result, child_id)
                if reason == "abort":
                    raise RuntimeError("observation received unowned abort")
                if reason is not None:
                    terminal = result
                    continue
                hit = await accept(result)
                if hit is not None:
                    prefix.clear()
                    if trace is not None:
                        trace["intervention_status"] = "disarmed_boundary"
                        trace["boundary_won_same_delta"] = source_tokens >= context.intervention_threshold
                if not armed or state.thinking_tokens < context.intervention_threshold:
                    continue
                decision_started = perf_counter() if recorder is not None else None
                if state.budget_decision is None:
                    if context.activate_budget is None:
                        raise RuntimeError("missing deferred budget resolver")
                    state.budget_decision = context.activate_budget()
                if recorder is not None:
                    decision_ms += (perf_counter() - decision_started) * 1000
                can_final, can_probe = _room(context, len(prefix))
                # A hard think budget equal to the intervention threshold is a
                # direct-final policy. It needs final capacity, not probe room.
                budget_exhausted = _BaseState.remaining_think_budget(context) <= 0
                if not can_probe and not (budget_exhausted and can_final):
                    armed = classified = False
                    prefix.clear()
                    if trace is not None:
                        trace["intervention_status"] = "disarmed_no_capacity"
                        trace["activation_skipped_reason"] = "continuation_capacity"
                    continue
                trigger_tokens = len(prefix)
                if trace is not None:
                    trace.update(intervention_status="abort_draining", trigger_prefix_tokens=trigger_tokens,
                                 threshold_overshoot_tokens=trigger_tokens-context.intervention_threshold)
                tail, finish = await abort_and_drain()
                terminal = tail[-1]
                handoff_started = perf_counter() if recorder is not None else None
                for item in tail:
                    is_terminal = item is terminal
                    await accept(item, owned_abort=is_terminal and finish == "abort",
                                 retain=finish == "abort", final=is_terminal and finish != "abort")
                if trace is not None:
                    trace.update(
                        abort_terminal_seen=finish == "abort",
                        abort_lost_to_native_terminal=finish != "abort",
                        drained_tail_tokens=source_tokens-trigger_tokens,
                        accepted_drain_tokens=source_tokens-trigger_tokens,
                        drain_overshoot_tokens=source_tokens-trigger_tokens,
                        drain_boundary_kind=(state.reasoning_boundary.value if state.reasoning_boundary else None),
                    )
                if finish != "abort":
                    state.completion_status = "success"
                    if trace is not None:
                        trace.update(native_same_child_completed=True, post_drain_branch="native_complete")
                    return None
                context.append_prompt_token_ids(prefix)
                state.thinking_segments = 1  # Already counted and emitted; do not append_thinking().
                can_final, can_probe = _room(context)
                if not can_final:
                    raise RuntimeError("no safe continuation capacity after abort drain")
                if state.reasoning_state is ReasoningState.CLOSED:
                    reason = (FinalAnswerReason.MODEL_TOOL_CALL
                              if state.reasoning_boundary is ReasoningBoundary.TOOL_CALL
                              else FinalAnswerReason.MODEL_THINK_END)
                    next_state = _BaseState.enter_final_answer(context, reason)
                    branch = "model_boundary_final"
                elif can_probe:
                    next_state, branch = _ConfidenceProbeState, "confidence_probe"
                else:
                    next_state = _BaseState.enter_final_answer(context, FinalAnswerReason.BUDGET_EXHAUSTED)
                    branch = "budget_final"
                if trace is not None:
                    trace.update(intervention_status="triggered", post_drain_branch=branch,
                                 restart_prefix_tokens=len(prefix),
                                 handoff_wall_ms=(perf_counter()-handoff_started)*1000)
                return next_state
            exhausted = True
            if terminal is None:
                raise RuntimeError("observation ended without terminal")
            await accept(terminal, final=True)
            state.completion_status = "success"
            if trace is not None:
                trace.update(native_same_child_completed=True, post_drain_branch="native_complete")
            return None
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                if not exhausted:
                    try:
                        await generator.aclose()
                    except BaseException:
                        if not active_error:
                            raise
                        _LOGGER.exception("observation cleanup failed request_id=%s", child_id)
            finally:
                if recorder is not None:
                    trace["observed_reasoning_tokens"] = state.thinking_tokens
                    trace["observed_final_tokens"] = state.final_answer_tokens
                    trace["unclassified_completion_tokens"] = state.unclassified_completion_tokens
                    trace["handoff_safety_failure"] = active_error
                    trace["source_terminal_passthrough_count"] = state.final_terminal_count
                    recorder.record_phase(
                        parent_request_id=context.request_id, child_request_id=child_id,
                        phase="native_passthrough", round_id=0,
                        wall_ms=(perf_counter()-started)*1000-decision_ms,
                        controller_cpu_ms=None, prompt_tokens=prompt_length,
                        generated_tokens=source_tokens, visible=True, output=terminal,
                        intervention=trace,
                    )
