"""No-model gates for the default streaming observation/abort-drain path."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from dtb.budgeting.budget_resolver import DtbBudgetResolver
from dtb.config.loader import load_dtb_service_config
from dtb.reasoning.deferred_observation import _DeferredInterventionState
from dtb.reasoning.generation_states import GUIDED_FINAL_CUE_V1, _ConfidenceProbeState, _FinalAnswerState
from dtb.reasoning.request_state import FinalAnswerReason
from dtb.reasoning.state_machine import DtbStateMachine
from test_dtb_state_machine import THINK_END_ID, TOOL_CALL_BEGIN_ID, make_context, result


def delta(text="abcde", ids=None):
    return result(text, [10, 11, 12, 13, 14] if ids is None else ids, finish_reason=None)


class AbortEngine:
    def __init__(self, before, tail, children=()):
        self.before, self.tail, self.children = before, tail, list(children)
        self.calls, self.aborts, self.closed = [], [], []
        self.abort_error = None
        self.hang = False

    async def abort(self, request_id):
        self.aborts.append(request_id)
        if self.abort_error:
            raise self.abort_error

    def generate(self, prompt, params, request_id, **kwargs):
        first = not self.calls
        self.calls.append((prompt, params, request_id, kwargs))

        async def run():
            exhausted = False
            try:
                script = self.before if first else self.children.pop(0)
                for item in script:
                    if isinstance(item, BaseException):
                        raise item
                    yield item
                if first:
                    if self.hang:
                        await asyncio.Event().wait()
                    for item in self.tail:
                        yield item
                exhausted = True
            finally:
                self.closed.append((request_id, exhausted))
        return run()


def setup(before, tail=(), children=(), *, phase=True):
    context, _ = make_context([])
    native = {"prompt_token_ids": [60], "prompt": "<think>"}
    decision = context.request_state.budget_decision
    context.request_state.budget_decision = None
    resolver = Mock(return_value=decision)
    context = replace(context, engine_prompt=native, deferred_observation=True,
                      intervention_threshold=5, activate_budget=resolver,
                      phase_recorder=context.phase_recorder if phase else None)
    original_params = context.request.to_sampling_params

    def sampling(*args):
        params = original_params(*args)
        params.stop, params.min_tokens = [], 0
        return params

    context.request.to_sampling_params = sampling
    engine = AbortEngine(before, tail, children)
    context.serving_chat.engine_client = engine
    context.tokenizer.decode = Mock(side_effect=AssertionError("decode forbidden"))
    return context, engine, resolver, native


def queued(context):
    values = []
    while not context.result_queue.empty():
        value = context.result_queue.get_nowait()
        if value is not None:
            values.append(value)
    return values


def set_direct_budget(context, resolver):
    service = load_dtb_service_config()
    config = replace(context.config, fixed_check_length=context.intervention_threshold,
                     fixed_budget_tokens=context.intervention_threshold)
    budget_resolver = DtbBudgetResolver(service)
    threshold = budget_resolver.intervention_threshold("test", config)
    resolver.return_value = budget_resolver.resolve("test", config, None, threshold=threshold)
    context.config = config


@pytest.mark.parametrize("phase", [False, True])
@pytest.mark.parametrize("tail_ids", [[], [15, 16, 17]])
def test_equal_budget_and_threshold_goes_to_cued_final_without_probe(phase, tail_ids):
    async def run():
        final = result("answer", [30, 31], finish_reason="stop")
        context, engine, resolver, native = setup(
            [delta()], [result("tail", tail_ids, finish_reason="abort")], [[final]], phase=phase)
        set_direct_budget(context, resolver)
        # Only observation + final capacity is required, even when probe cannot fit.
        context.config = replace(context.config, max_internal_calls=2)
        with patch.object(_ConfidenceProbeState, "handle", side_effect=AssertionError("probe forbidden")):
            await DtbStateMachine(context).run()
        state = context.request_state
        assert state.completion_status == "success", context.orchestration_error
        assert state.final_answer_reason is FinalAnswerReason.BUDGET_EXHAUSTED
        assert state.probe_round == state.hidden_probe_tokens == 0
        assert len(engine.calls) == 2 and len(engine.aborts) == 1
        cue = context.tokenizer.encode(GUIDED_FINAL_CUE_V1, add_special_tokens=False)
        assert engine.calls[1][0]["prompt_token_ids"] == [60, 10, 11, 12, 13, 14, *tail_ids, *cue, THINK_END_ID, 10, 10]
        assert engine.calls[1][1].max_tokens == 100 - 5 - len(tail_ids) - 1
        assert native["prompt_token_ids"] == [60]
        ids = [t for output in queued(context) for t in output.outputs[0].token_ids]
        assert ids == [10, 11, 12, 13, 14, *tail_ids, THINK_END_ID, 30, 31]
        assert state.final_terminal_count == 1
        if phase:
            assert state.intervention_telemetry["post_drain_branch"] == "budget_final"
            assert all(p["phase"] != "confidence_probe" for p in context.phase_recorder.phases)
        else:
            assert state.intervention_telemetry is None
    asyncio.run(run())


def test_direct_budget_without_final_capacity_stays_native():
    async def run():
        terminal = result("done", [30], finish_reason="length")
        context, engine, resolver, _ = setup([delta(), terminal])
        set_direct_budget(context, resolver)
        context.request_state.completion_token_limit = 6  # No room for boundary + answer.
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success"
        assert not engine.aborts and len(engine.calls) == 1
        assert queued(context)[-1] is terminal
        assert context.request_state.probe_round == 0
    asyncio.run(run())


def test_nonstream_equal_budget_and_first_segment_also_skips_probe():
    async def run():
        context, engine = make_context([
            [result("abcde", [10, 11, 12, 13, 14])],
            [result("answer", [30, 31], finish_reason="stop")],
        ], hard_limit=5)
        context.request.stream = False
        with patch.object(_ConfidenceProbeState, "handle", side_effect=AssertionError("probe forbidden")):
            await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success", context.orchestration_error
        assert context.request_state.final_answer_reason is FinalAnswerReason.BUDGET_EXHAUSTED
        assert context.request_state.probe_round == context.request_state.hidden_probe_tokens == 0
        assert len(engine.calls) == 2
        assert all(p["phase"] != "confidence_probe" for p in context.phase_recorder.phases)
    asyncio.run(run())


@pytest.mark.parametrize("boundary", [THINK_END_ID, TOOL_CALL_BEGIN_ID])
@pytest.mark.parametrize("width", [0, 3, 8])
def test_short_same_child_disarms_even_when_boundary_crosses_threshold(boundary, width):
    async def run():
        first = delta("thought boundary", [10]*width+[boundary, 21])
        terminal = result("body", [30], finish_reason="stop")
        context, engine, resolver, native = setup([first, terminal])
        with patch.object(type(context.reasoning_protocol), "find_first_boundary",
                          wraps=context.reasoning_protocol.find_first_boundary) as classify:
            await DtbStateMachine(context).run()
            assert classify.call_count == 1
        assert queued(context) == [first, terminal]
        assert len(engine.calls) == 1 and not engine.aborts
        assert engine.calls[0][0] is native and context.engine_prompt is native
        assert engine.calls[0][1].max_tokens == 100
        assert engine.calls[0][1].stop == []
        assert context.request_state.thinking_tokens == width
        assert context.request_state.final_answer_tokens == 3
        assert context.request_state.final_terminal_count == 1
        resolver.assert_not_called()
        context.tokenizer.decode.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("tail", [
    [result("", [], finish_reason="abort")],
    [result("flush", [], finish_reason="abort")],
    [delta("more", [15]), result("merged", [16, 17], finish_reason="abort")],
    [result("", [15, 16], finish_reason="abort")],
])
def test_drain_accepts_all_payload_and_resumes_from_full_prefix(tail):
    async def run():
        context, engine, resolver, native = setup([delta()], tail)
        next_state = await _DeferredInterventionState.handle(context)
        ids = [10, 11, 12, 13, 14] + [t for r in tail for t in r.outputs[0].token_ids]
        assert next_state is _ConfidenceProbeState
        assert context.engine_prompt["prompt_token_ids"] == [60, *ids]
        assert native["prompt_token_ids"] == [60]
        assert "prompt" not in context.engine_prompt
        assert engine.calls[0][0] is native
        assert engine.aborts == [engine.calls[0][2]]
        assert engine.closed == [(engine.calls[0][2], True)]
        assert resolver.call_count == 1
        assert context.request_state.thinking_tokens == len(ids)
        assert context.request_state.emitted_completion_tokens == len(ids)
        assert context.request_state.thinking_segments == 1
        outputs = queued(context)
        assert "".join(o.outputs[0].text for o in outputs) == "abcde"+"".join(o.outputs[0].text for o in tail)
        assert all(o.outputs[0].finish_reason is None and not o.finished for o in outputs)
        assert tail[-1].outputs[0].finish_reason == "abort"
        assert context.request_state.intervention_telemetry["restart_prefix_tokens"] == len(ids)
    asyncio.run(run())


@pytest.mark.parametrize("ids,reason", [
    ([THINK_END_ID, 21], FinalAnswerReason.MODEL_THINK_END),
    ([TOOL_CALL_BEGIN_ID, 21, 22], FinalAnswerReason.MODEL_TOOL_CALL),
    ([THINK_END_ID, TOOL_CALL_BEGIN_ID, 21], FinalAnswerReason.MODEL_THINK_END),
])
@pytest.mark.parametrize("direct_budget", [False, True])
def test_drained_boundary_goes_directly_to_final_without_cue_or_duplicate(ids, reason, direct_budget):
    async def run():
        tail = result("boundary+partial arguments", ids, finish_reason="abort")
        final = result("rest", [30, 31], finish_reason="stop")
        context, engine, resolver, _ = setup([delta()], [tail], [[final]])
        if direct_budget:
            set_direct_budget(context, resolver)
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success"
        assert context.request_state.final_answer_reason is reason
        assert len(engine.calls) == 2 and context.request_state.probe_round == 0
        assert engine.calls[1][0]["prompt_token_ids"] == [60, 10, 11, 12, 13, 14, *ids]
        assert engine.calls[1][3]["reasoning_ended"] is True
        assert engine.calls[1][3]["priority"] == context.request.priority
        assert engine.calls[1][1].max_tokens == 100-5-len(ids)
        outputs = queued(context)
        assert len(outputs) == 3 and outputs[-1] is final
        assert [t for r in outputs for t in r.outputs[0].token_ids] == [10,11,12,13,14,*ids,30,31]
        assert context.request_state.thinking_tokens == 5
        assert context.request_state.final_answer_tokens == len(ids)+2
        assert context.request_state.final_terminal_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("finish", ["stop", "length"])
@pytest.mark.parametrize("direct_budget", [False, True])
def test_native_terminal_wins_abort_race_without_new_child(finish, direct_budget):
    async def run():
        tail = [delta("tail", [20]), result("done", [THINK_END_ID, 30], finish_reason=finish)]
        context, engine, resolver, native = setup([delta()], tail)
        if direct_budget:
            set_direct_budget(context, resolver)
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success"
        assert len(engine.calls) == 1 and len(engine.aborts) == 1
        assert queued(context)[-2:] == tail
        assert context.engine_prompt is native
        assert context.request_state.intervention_telemetry["abort_lost_to_native_terminal"]
    asyncio.run(run())


@pytest.mark.parametrize("mutate", [
    lambda c,p: setattr(p,"stop",["STOP"]),
    lambda c,p: setattr(p,"min_tokens",5),
    lambda c,p: setattr(p,"presence_penalty",1),
    lambda c,p: setattr(p,"structured_outputs",{"json": "schema"}),
    lambda c,p: setattr(c,"reasoning_ended",True),
    lambda c,p: setattr(c,"reasoning_protocol",None),
    lambda c,p: c.engine_prompt.update(multi_modal_data=object()),
])
def test_unsupported_switch_shapes_remain_native(mutate):
    async def run():
        terminal = result("done", [30], finish_reason="stop")
        context, engine, resolver, native = setup([delta(), terminal])
        original = context.request.to_sampling_params
        def params(*args):
            p = original(*args)
            mutate(context,p)
            return p
        context.request.to_sampling_params = params
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success"
        assert len(engine.calls) == 1 and not engine.aborts
        resolver.assert_not_called()
        assert queued(context)[-1] is terminal
        assert context.engine_prompt is native
    asyncio.run(run())


@pytest.mark.parametrize("before,tail", [
    ([delta("short",[10])], []),
    ([delta()], []),
    ([result("bad",[],finish_reason="abort")], []),
    ([result("error",[],finish_reason="error")], []),
    ([delta()], [result("",[],finish_reason="abort"), delta()]),
    ([delta()], [result("",[],finish_reason="abort",stop_reason=7)]),
])
def test_invalid_lifecycle_is_error_never_probe(before,tail):
    async def run():
        context, engine, _, _ = setup(before,tail)
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "error"
        assert context.orchestration_error
        assert len(engine.calls) == 1
    asyncio.run(run())


def test_abort_error_timeout_and_cancel_cleanup():
    async def run(kind):
        context, engine, _, _ = setup([delta()])
        if kind == "error":
            engine.abort_error = RuntimeError("send failed")
        else:
            engine.hang = True
        with patch("dtb.reasoning.deferred_observation.ABORT_DRAIN_TIMEOUT_S", 0.02):
            task = asyncio.create_task(DtbStateMachine(context).run())
            if kind == "cancel":
                while not engine.aborts:
                    await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert context.request_state.completion_status == "cancelled"
            else:
                await task
                assert context.request_state.completion_status == "error"
        assert len(engine.closed) == 1 and len(engine.calls) == 1
    for kind in ("error", "timeout", "cancel"):
        asyncio.run(run(kind))


def test_phase_off_has_no_observation_timing_or_recorder():
    async def run():
        context, _, _, _ = setup([delta("short",[10]),result("end",[30],finish_reason="stop")], phase=False)
        with patch("dtb.reasoning.deferred_observation.perf_counter", side_effect=AssertionError("off timer")):
            await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success"
        assert context.request_state.intervention_telemetry is None
    asyncio.run(run())


def test_threshold_snapshot_is_lightweight_and_not_clamped_by_context():
    service = load_dtb_service_config()
    resolver = DtbBudgetResolver(service)
    config = replace(service.algorithm, fixed_check_length=850, fixed_budget_tokens=2000)
    with patch.object(resolver, "_match_scene", side_effect=AssertionError("fixed routing")):
        snapshot = resolver.intervention_threshold("prompt", config)
    assert snapshot.tokens == 850
    config = replace(config, fixed_check_length=0, fixed_budget_tokens=0)
    with patch.object(resolver, "_match_scene", return_value={"scene_matched":True,"scene":"code_generation"}) as match:
        snapshot = resolver.intervention_threshold("prompt",config)
        decision = resolver.resolve("prompt",config,None,threshold=snapshot)
        assert match.call_count == 1
    assert snapshot.tokens == service.scenes["code_generation"]["check_length"]
    assert decision.budget.check_length == snapshot.tokens
    with pytest.raises(ValueError, match="fixed_check_length"):
        replace(config, fixed_check_length=100, fixed_budget_tokens=50)


@pytest.mark.parametrize("accepted", [True, False])
def test_long_path_probe_and_optional_stride_keep_identity_and_accounting(accepted):
    async def run():
        children = [[result("trial", [50], confidence=-0.01 if accepted else -8.0)]]
        if not accepted:
            children.append([result("stride", [51, THINK_END_ID], finish_reason="stop", stop_reason=THINK_END_ID)])
        children.append([result("answer", [52], finish_reason="stop")])
        context, engine, _, _ = setup([delta()], [result("", [], finish_reason="abort")], children)
        context.lora_request = "lora"
        context.data_parallel_rank = 3
        context.trace_headers = {"traceparent":"trace"}
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success"
        assert context.request_state.hidden_probe_tokens == 1
        assert context.request_state.thinking_segments == (1 if accepted else 2)
        if not accepted:
            assert engine.calls[2][1].max_tokens == 3  # stride, not another check_length
        for _, _, _, kwargs in engine.calls:
            assert kwargs["lora_request"] == "lora"
            assert kwargs["data_parallel_rank"] == 3
            assert kwargs["trace_headers"] == {"traceparent":"trace"}
        ids = [t for r in queued(context) for t in r.outputs[0].token_ids]
        assert 50 not in ids and len(ids) == context.request_state.emitted_completion_tokens
        assert context.request_state.final_terminal_count == 1
    asyncio.run(run())


def test_capacity_disarms_before_abort_and_rechecks_after_drain():
    async def before():
        context, engine, resolver, _ = setup([delta(), result("done",[30],finish_reason="stop")])
        context.config = replace(context.config, max_internal_calls=2)
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "success"
        assert resolver.call_count == 1 and not engine.aborts
    async def after():
        context, engine, _, _ = setup([delta()], [result("long",[20]*95,finish_reason="abort")])
        await DtbStateMachine(context).run()
        assert context.request_state.completion_status == "error"
        assert "continuation capacity" in context.orchestration_error
        assert context.request_state.emitted_completion_tokens == 100
        assert len(engine.calls) == 1
    asyncio.run(before())
    asyncio.run(after())


def test_forward_error_closes_generator_and_preserves_error():
    async def run():
        context, engine, resolver, _ = setup([delta()])
        original = context.stream_output
        async def fail(value, **kwargs):
            if value is not None:
                raise RuntimeError("forward failed")
            await original(value, **kwargs)
        context.stream_output = fail
        await DtbStateMachine(context).run()
        assert context.orchestration_error == "forward failed"
        assert len(engine.closed) == 1 and not engine.aborts
        resolver.assert_not_called()
    asyncio.run(run())


def test_native_observation_extractor_has_no_fabricated_decision():
    from tools.extract_dtb_phase_metrics import summarize_requests
    rows = summarize_requests([{
        "schema":3, "parent_request_id":"p", "phase":"native_passthrough",
        "generated_tokens":9, "wall_ms":12,
        "intervention":{"observed_reasoning_tokens":5,"observed_final_tokens":4,
                        "unclassified_completion_tokens":0,"native_same_child_completed":True},
    }])
    assert rows[0]["visible_think_tokens"] == 5
    assert rows[0]["final_answer_tokens"] == 4
    assert rows[0]["total_model_generated_tokens"] == 9
    assert rows[0]["budget_resolution"] == "not_required_native"
    assert rows[0]["budget_decision"] is None
