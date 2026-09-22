"""DELTA observation, owned abort/drain, and one direct-answer continuation.

Engine objects are duck typed; importing the SDK needs neither vLLM nor DTB.
No producer task or unbounded output queue is created by this layer.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import sys
from dataclasses import asdict
from time import perf_counter
from typing import Any

from .api import (
    CONTINUE, ChildRecord, Detector, ObserverContext, Outcome, ReasoningDelta,
    StopAndAnswer,
)
from .config import ObserverConfig

LOG = logging.getLogger(__name__)


def synchronous(value):
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("async detector callbacks are not supported")
    return value


def validate_result(result, child_id):
    if result.request_id != child_id or len(result.outputs) != 1:
        raise RuntimeError("observer source identity/choice changed")
    output = result.outputs[0]
    if output.index != 0 or bool(result.finished) != (output.finish_reason is not None):
        raise RuntimeError("observer source terminal/choice is incoherent")
    if output.finish_reason not in (None, "stop", "length", "abort"):
        raise RuntimeError(f"observer engine failed: {output.finish_reason}")
    return output.finish_reason


def copy_output(result, *, text=None, ids=None, finish=..., stop=...):
    result = copy.copy(result)
    output = copy.copy(result.outputs[0])
    result.outputs = [output]
    if text is not None:
        output.text = text
    if ids is not None:
        output.token_ids = list(ids)
        output.logprobs = None
        output.cumulative_logprob = None
    if finish is not ...:
        output.finish_reason = finish
        result.finished = finish is not None
    if stop is not ...:
        output.stop_reason = stop
    return result


class ThinkingObserver:
    def __init__(self, detector: Detector, config: ObserverConfig | None = None):
        self.detector = detector
        self.config = config or ObserverConfig()

    async def stream(self, engine, prompt, params, context: ObserverContext,
                     engine_kwargs: dict[str, Any]):
        """Yield parser-facing DELTAs. Callers MUST close this async generator.

        The serving adapter performs capability checks before entering here.
        Direct integrations must use the same checks and DELTA output kind.
        """
        if not self.config.enabled:
            raise ValueError("disabled observer must use native serving")
        kind = getattr(getattr(params, "output_kind", None), "name", "")
        if kind != "DELTA" or params.max_tokens != context.completion_limit:
            raise ValueError("observer requires DELTA and the external completion limit")
        if len(prompt["prompt_token_ids"]) != context.prompt_tokens:
            raise ValueError("context and prompt length differ")

        config, protocol = self.config, context.protocol
        state = None
        detector_error = skipped = decision_reason = None
        detected_at = repeat_start = handoff = emitted_at_detection = None
        answer_limit = abort_ms = handoff_ms = None
        cue_count = 0
        callback_ms = 0.0
        emitted = reasoning = 0
        status, finish_reason = "error", None
        armed, reasoning_open = True, True
        prefix: list[int] = []
        children: list[ChildRecord] = []
        generator = None
        active_id = None
        exhausted = True
        abort_requested = False
        child_generated = 0
        child_started = None
        child_prompt_len = 0
        phase = "observation"
        source_finish = None
        initial_cache_tokens = None

        def outcome():
            return Outcome(
                status, finish_reason, emitted, reasoning, detected_at, repeat_start,
                handoff, emitted_at_detection,
                None if handoff is None else handoff - detected_at,
                decision_reason, detector_error, skipped, callback_ms, abort_ms,
                handoff_ms, cue_count, answer_limit, tuple(children),
            )

        def record_child():
            if active_id is not None:
                children.append(ChildRecord(
                    active_id, phase, child_prompt_len, child_generated, source_finish,
                    None if child_started is None else (perf_counter() - child_started) * 1000,
                ))

        def external(result):
            nonlocal emitted, initial_cache_tokens
            count = len(result.outputs[0].token_ids)
            if emitted + count > context.completion_limit:
                raise RuntimeError("parser-facing completion limit exceeded")
            emitted += count
            adapted = copy.copy(result)
            adapted.request_id = context.request_id
            adapted.prompt = prompt.get("prompt")
            adapted.prompt_token_ids = prompt["prompt_token_ids"]
            if phase == "observation" and initial_cache_tokens is None:
                initial_cache_tokens = getattr(result, "num_cached_tokens", None)
            adapted.num_cached_tokens = initial_cache_tokens
            # A continuation's prefill/cache metrics are not external prompt usage.
            if phase != "observation":
                adapted.prompt_logprobs = None
                for name in ("metrics", "kv_transfer_params"):
                    if hasattr(adapted, name):
                        setattr(adapted, name, None)
            return adapted

        def accept(result, *, observe=True, retain=False):
            nonlocal armed, reasoning_open, reasoning, detector_error, callback_ms
            ids = result.outputs[0].token_ids
            was_open = reasoning_open
            hit = protocol.find(ids) if was_open else None
            if armed or retain:
                prefix.extend(ids)
            start = reasoning
            if was_open:
                reasoning += hit[0] if hit else len(ids)
            if hit:
                reasoning_open = False
            result = external(result)
            decision = CONTINUE
            if observe and armed and was_open and state is not None:
                text = result.outputs[0].text
                if hit:
                    marker = "</think>" if hit[1] == "think_end" else "<tool_call>"
                    text = text.split(marker, 1)[0] if marker in text else None
                delta = ReasoningDelta(
                    tuple(ids[:hit[0]] if hit else ids), text, start, reasoning,
                    emitted, hit[1] if hit else None, result.finished,
                )
                started = perf_counter()
                try:
                    decision = synchronous(state.observe(delta))
                    if decision is not CONTINUE:
                        if not isinstance(decision, StopAndAnswer):
                            raise TypeError("invalid detector decision")
                        if (not isinstance(decision.reason, str) or not decision.reason
                                or len(decision.reason) > 256):
                            raise ValueError("invalid decision reason")
                        if (type(decision.detected_at) is not int
                                or not 0 <= decision.detected_at <= delta.end):
                            raise ValueError("invalid detection position")
                        if (decision.repeat_start is not None
                                and (type(decision.repeat_start) is not int
                                     or not 0 <= decision.repeat_start <= decision.detected_at)):
                            raise ValueError("invalid repeat position")
                except Exception as exc:
                    detector_error = type(exc).__name__
                    armed, decision = False, CONTINUE
                finally:
                    elapsed = (perf_counter() - started) * 1000
                    callback_ms += elapsed
                if elapsed > config.callback_limit_ms:
                    detector_error = "callback_cost_exceeded"
                    armed, decision = False, CONTINUE
            if hit or result.finished:
                armed, decision = False, CONTINUE
            if not armed and not retain:
                prefix.clear()
            return result, decision

        def encode_cue(decision):
            cue = config.answer_cue if decision.answer_cue is None else decision.answer_cue
            if not isinstance(cue, str) or len(cue) > 4096:
                raise ValueError("invalid answer cue")
            ids = list(context.tokenizer.encode(cue, add_special_tokens=False))
            # Reject structural injections, including model-specific role markers.
            special = set(getattr(context.tokenizer, "all_special_ids", ()))
            special.update((protocol.think_end_id, protocol.tool_start_id))
            if len(ids) > config.max_cue_tokens or special.intersection(ids):
                raise ValueError("answer cue is oversized or contains structural tokens")
            return ids

        try:
            started = perf_counter()
            try:
                state = synchronous(self.detector.start(context))
                if not callable(getattr(state, "observe", None)) or not callable(getattr(state, "close", None)):
                    raise TypeError("start must return a detector state")
            except Exception as exc:
                detector_error, armed = type(exc).__name__, False
            callback_ms += (perf_counter() - started) * 1000
            if callback_ms > config.callback_limit_ms:
                detector_error, armed = "callback_cost_exceeded", False

            active_id = context.request_id + "_observer"
            child_prompt_len = context.prompt_tokens
            child_started = perf_counter() if config.telemetry == "summary" else None
            generator = engine.generate(prompt, params, active_id, **engine_kwargs)
            exhausted = False
            terminal = None
            selected = None
            cue_ids = []
            async for result in generator:
                if terminal is not None:
                    raise RuntimeError("source emitted output after terminal")
                source_finish = validate_result(result, active_id)
                child_generated += len(result.outputs[0].token_ids)
                if source_finish == "abort":
                    raise RuntimeError("unowned abort")
                if source_finish is not None:
                    terminal = result
                    continue
                forwarded, decision = accept(result)
                yield forwarded
                if decision is CONTINUE:
                    continue
                detected_at, repeat_start = decision.detected_at, decision.repeat_start
                decision_reason, emitted_at_detection = decision.reason, emitted
                # Extension failures before abort safely disarm this request.
                try:
                    cue_ids = encode_cue(decision)
                except Exception as exc:
                    detector_error, armed = type(exc).__name__, False
                    prefix.clear()
                    continue
                # Reserve one closure ID plus at least one answer ID.
                room = context.max_model_len - context.prompt_tokens - len(prefix)
                if context.completion_limit - emitted < 2 or room < len(cue_ids) + 2:
                    skipped, armed = "continuation_capacity", False
                    prefix.clear()
                    continue
                selected = decision
                break

            if selected is None:
                exhausted = True
                if terminal is None:
                    raise RuntimeError("observation ended without terminal")
                forwarded, _ = accept(terminal)
                status, finish_reason = "native_complete", source_finish
                yield forwarded
                return

            # Buffer only the bounded abort tail; all accepted IDs join the prompt.
            tail, terminal = [], None
            started = perf_counter()
            async with asyncio.timeout(config.abort_timeout_s):
                abort_requested = True
                await engine.abort(active_id)
                async for result in generator:
                    if terminal is not None:
                        raise RuntimeError("source emitted output after drain terminal")
                    source_finish = validate_result(result, active_id)
                    child_generated += len(result.outputs[0].token_ids)
                    if child_generated > context.completion_limit:
                        raise RuntimeError("drain exceeded completion limit")
                    tail.append(result)
                    if len(tail) > config.max_drain_chunks:
                        raise RuntimeError("abort drain chunk limit exceeded")
                    if source_finish is not None:
                        terminal = result
                exhausted = True
                if terminal is None:
                    raise RuntimeError("abort drain ended without terminal")
            abort_ms = (perf_counter() - started) * 1000
            started = perf_counter()
            for item in tail:
                if item is terminal and source_finish == "abort":
                    if item.outputs[0].stop_reason is not None:
                        raise RuntimeError("owned abort has nonempty stop_reason")
                    item = copy_output(item, finish=None, stop=None)
                forwarded, _ = accept(item, observe=False, retain=True)
                yield forwarded
            if source_finish != "abort":
                status, finish_reason = "native_won_race", source_finish
                return

            handoff = reasoning
            record_child()
            active_id = None
            # A natural boundary in the drained tail wins over cue/forced close.
            suffix = [] if not reasoning_open else [*cue_ids, protocol.think_end_id]
            cue_count = len(cue_ids) if reasoning_open else 0
            new_ids = [*prompt["prompt_token_ids"], *prefix, *suffix]
            answer_limit = min(context.max_model_len - len(new_ids),
                               context.completion_limit - emitted - int(reasoning_open))
            if answer_limit < 1:
                raise RuntimeError("no continuation capacity after abort drain")
            if reasoning_open:
                closure = copy_output(terminal, text="</think>", ids=[protocol.think_end_id],
                                      finish=None, stop=None)
                yield external(closure)
                reasoning_open = False

            final_prompt = dict(prompt, prompt_token_ids=new_ids)
            final_prompt.pop("prompt", None)  # Never attach stale text to edited IDs.
            final_params = copy.copy(params)
            final_params.max_tokens = answer_limit
            final_kwargs = dict(engine_kwargs, reasoning_ended=True)
            if "prompt_text" in final_kwargs:
                final_kwargs["prompt_text"] = None
            phase = "final_answer"
            active_id = context.request_id + "_observer_final"
            child_prompt_len, child_generated, source_finish = len(new_ids), 0, None
            child_started = perf_counter() if config.telemetry == "summary" else None
            abort_requested, exhausted = False, False
            generator = engine.generate(final_prompt, final_params, active_id, **final_kwargs)
            handoff_ms = (perf_counter() - started) * 1000
            terminal = None
            async for result in generator:
                if terminal is not None:
                    raise RuntimeError("final child emitted output after terminal")
                source_finish = validate_result(result, active_id)
                child_generated += len(result.outputs[0].token_ids)
                if source_finish == "abort":
                    raise RuntimeError("unowned final abort")
                if source_finish is not None:
                    terminal = result
                else:
                    yield external(result)
            exhausted = True
            if terminal is None:
                raise RuntimeError("final child ended without terminal")
            forwarded = external(terminal)
            status, finish_reason = "handed_off", source_finish
            yield forwarded
        except (asyncio.CancelledError, GeneratorExit):
            status = "cancelled"
            raise
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                if generator is not None and not exhausted:
                    try:
                        async with asyncio.timeout(config.abort_timeout_s):
                            try:
                                if not abort_requested:
                                    await engine.abort(active_id)
                            finally:
                                await generator.aclose()
                    except BaseException:
                        if not active_error:
                            status = "error"
                            raise
                        LOG.exception("observer engine cleanup failed: %s", active_id)
            finally:
                record_child()
                if state is not None and callable(getattr(state, "close", None)):
                    try:
                        synchronous(state.close(outcome()))
                    except Exception as exc:
                        detector_error = detector_error or ("close_" + type(exc).__name__)
                if config.telemetry == "summary":
                    LOG.info("THINKING_OBSERVER_SUMMARY %s", json.dumps({
                        "schema": 1, "request_id": context.request_id,
                        **asdict(outcome()),
                    }, ensure_ascii=False))
