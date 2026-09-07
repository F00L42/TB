"""Input-only refactor gates: no full decode, stable IDs, untouched deltas."""
from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from dtb.config.loader import load_dtb_service_config
from dtb.config.schema import DtbObservabilityConfig, DtbTelemetryMode
from dtb.integration.vllm.request_adapter import extract_prompt_text_views
from dtb.integration.vllm.response_adapter import with_initial_prompt_text
from dtb.observability.event_writer import DtbEventWriter
from dtb.observability.phase_recorder import DtbPhaseRecorder
from dtb.reasoning.state_machine import DtbStateMachine
from dtb.request_controller import DtbRequestController
from test_dtb_state_machine import (
    GUIDED_FINAL_CUE_V1,
    GUIDED_FINAL_SEPARATOR,
    THINK_END_ID,
    TOOL_CALL_BEGIN_ID,
    TRIAL_PROMPT,
    fake_reasoning_protocol,
    make_context,
    result,
    run_and_drain_outputs,
)


@pytest.mark.parametrize("initial", [None, "", "  ", "<user>native</user>"])
def test_text_views_select_once_and_preserve_initial_snapshot(initial):
    conversation = [
        {"role": "user", "content": "old"},
        SimpleNamespace(role="user", content=[
            " leading ", {"type": "text", "text": "text"},
            {"type": "image_url", "image_url": "ignored"},
            SimpleNamespace(text="tail "),
        ]),
        {"role": "tool", "content": "NOT the classification input"},
    ]
    views = extract_prompt_text_views(conversation, initial)
    assert views.classification_text == "leading \ntext\ntail"
    assert views.classification_source == "latest_user_message"
    assert views.initial_text is initial
    assert views.initial_text_source == ("missing" if initial is None else "serving_prompt")
    with pytest.raises(FrozenInstanceError):
        views.initial_text = "mutated"


@pytest.mark.parametrize("conversation", [[], None, [
    {"role": "system", "content": "not a user"},
    {"role": "user", "content": [{"type": "image_url", "image_url": "image"}]},
]])
@pytest.mark.parametrize("initial,expected,source", [
    (None, "", "missing"), ("", "", "missing"), ("  ", "", "missing"),
    ("<user>native</user>", "<user>native</user>", "serving_prompt_fallback"),
])
def test_explicit_fallback_sources_without_tokenizer(conversation, initial, expected, source):
    views = extract_prompt_text_views(conversation, initial)
    assert (views.classification_text, views.classification_source) == (expected, source)
    assert views.initial_text is initial


def test_ledger_and_submissions_own_tokens_but_share_native_payloads():
    context, _ = make_context([])
    native = {"prompt": "native", "prompt_token_ids": [1, 2],
              "multi_modal_data": object(), "cache_salt": "salt"}
    # Dataclass reconstruction exercises exactly the real context constructor.
    context = replace(context, engine_prompt=native, prompt_text_views=None)
    first = context.engine_input()
    fork = context.child_prompt("trial")
    context.append_prompt_token_ids([THINK_END_ID])
    continuation = context.engine_input()
    context.append_prompt_suffix("cue")
    assert native["prompt_token_ids"] == first["prompt_token_ids"] == [1, 2]
    assert first["prompt"] == "native"
    assert context.initial_prompt_text == "native"
    assert continuation["prompt_token_ids"] == [1, 2, THINK_END_ID]
    assert fork["prompt_token_ids"] == [1, 2, *map(ord, "trial")]
    fork["prompt_token_ids"].append(-1)
    assert -1 not in context.engine_prompt["prompt_token_ids"]
    for child in (fork, continuation, context.engine_input()):
        assert "prompt" not in child
        assert context.engine_request_kwargs(child)["prompt_text"] is None
        assert child["multi_modal_data"] is native["multi_modal_data"]
        assert child["cache_salt"] == "salt"
    assert context.engine_request_kwargs(first)["prompt_text"] == "native"


@pytest.mark.parametrize("boundary", [THINK_END_ID, TOOL_CALL_BEGIN_ID])
@pytest.mark.parametrize("tail,ids", [("", []), ("retained tail", [10, 11])])
def test_token_only_boundary_preserves_both_paths_without_decode(boundary, tail, ids):
    async def run():
        source = result(tail, [*ids, boundary], finish_reason="stop", stop_reason=boundary)
        context, engine = make_context([
            [source], [result("body", [30], finish_reason="stop", stop_reason="eos")],
        ])
        context.tokenizer.decode = Mock(side_effect=AssertionError("unexpected decode"))
        # Retain the real dictionaries like an asynchronous engine may do.
        submissions = []
        generate = engine.generate

        def retain(prompt, *args, **kwargs):
            submissions.append(prompt)
            return generate(prompt, *args, **kwargs)

        engine.generate = retain
        outputs = await run_and_drain_outputs(context)
        assert [(o.text, o.token_ids) for o in outputs] == [
            (tail, [*ids, boundary]), ("body", [30]),
        ]
        assert engine.calls[-1]["prompt_token_ids"] == [60, *ids, boundary]
        assert submissions[0]["prompt_token_ids"] == [60]
        assert "prompt" not in submissions[1]
        assert engine.calls[-1]["kwargs"]["prompt_text"] is None
        assert source.outputs[0].stop_reason == boundary
        assert source.outputs[0].finish_reason == "stop"
        assert outputs[0].stop_reason is None  # internal terminal remains hidden
        assert outputs[-1].stop_reason == "eos"
        assert context.request_state.child_generate_calls == 2
        context.tokenizer.decode.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("accept", [False, True])
def test_probe_and_final_golden_ids_do_not_depend_on_text_snapshot(stream, accept):
    async def run():
        traces = []
        for initial in (None, "", "<think>"):
            scripts = [[result("thought", [10])],
                       [result("hidden", [20], confidence=-0.01 if accept else -5.0)]]
            if not accept:
                scripts.append([result("stride", [11])])
            scripts.append([result("answer", [30], finish_reason="stop")])
            context, engine = make_context(scripts, num_thinking=1)
            context.request.stream = stream
            native = {"prompt_token_ids": [60]}
            if initial is not None:
                native["prompt"] = initial
            context = replace(context, engine_prompt=native, prompt_text_views=None)
            context.tokenizer.decode = Mock(side_effect=AssertionError("unexpected decode"))
            outputs = await run_and_drain_outputs(context)
            expected_prefix = [60, 10] + ([] if accept else [11])
            suffix = [*map(ord, GUIDED_FINAL_CUE_V1),
                      *([THINK_END_ID] if stream else list(map(ord, "</think>"))),
                      *map(ord, GUIDED_FINAL_SEPARATOR)]
            assert engine.calls[1]["prompt_token_ids"] == [60, 10, *map(ord, TRIAL_PROMPT)]
            assert engine.calls[-1]["prompt_token_ids"] == expected_prefix + suffix
            assert all(20 not in c["prompt_token_ids"] for c in engine.calls)
            assert all(c["kwargs"]["prompt_text"] is None for c in engine.calls[1:])
            assert context.initial_prompt_text is initial
            assert native["prompt_token_ids"] == [60]
            assert context.request_state.completion_status == "success"
            context.tokenizer.decode.assert_not_called()
            # Text metadata is intentionally optional; compare every other input.
            for call in engine.calls:
                call["kwargs"].pop("prompt_text")
            traces.append((engine.calls, [(o.text, o.token_ids, o.finish_reason) for o in outputs]))
        assert traces[0] == traces[1] == traces[2]
    asyncio.run(run())


@pytest.mark.parametrize("initial", [None, "", "native"])
def test_external_metadata_adapter_does_not_mutate_parser_or_source(initial):
    source = result("tail", [10, THINK_END_ID])
    source.prompt = "INTERNAL PROMPT"
    source.prompt_token_ids = [1, 2, 3]
    adapted = with_initial_prompt_text(source, initial)
    assert adapted is not source
    assert adapted.prompt is initial
    assert source.prompt == "INTERNAL PROMPT"
    assert adapted.outputs is source.outputs
    assert adapted.prompt_token_ids is source.prompt_token_ids


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("return_text", [False, True])
@pytest.mark.parametrize("initial", [None, "", "native prompt"])
@pytest.mark.parametrize("has_user", [False, True])
@pytest.mark.parametrize("phase", [False, True])
def test_facade_never_decodes_and_preserves_native_response_metadata(
    stream, return_text, initial, has_user, phase,
):
    async def run():
        source = result("tail" if stream else "tail</think>",
                        [10, THINK_END_ID] if stream else [10, *map(ord, "</think>")],
                        finish_reason="stop", stop_reason=THINK_END_ID if stream else "</think>")
        context, engine = make_context([[source], [result("body", [30], finish_reason="stop")]])
        context.request.stream = stream
        context.request.return_prompt_text = return_text
        serving = context.serving_chat
        serving.create_error_response = lambda message: {"error": message}
        native = {"prompt_token_ids": [60]}
        if initial is not None:
            native["prompt"] = initial
        submissions = []
        generate = engine.generate

        def with_metadata(prompt, *args, **kwargs):
            submissions.append(prompt)
            async def produce():
                async for item in generate(prompt, *args, **kwargs):
                    item.prompt = prompt.get("prompt")
                    item.prompt_token_ids = prompt["prompt_token_ids"]
                    yield item
            return produce()

        engine.generate = with_metadata
        seen = []

        async def formatter(_request, outputs, *args, **kwargs):
            async for item in outputs:
                seen.append(item)
                yield item

        async def full_formatter(*args, **kwargs):
            return [item async for item in formatter(*args, **kwargs)]

        serving.chat_completion_stream_generator = formatter
        serving.chat_completion_full_generator = full_formatter
        context.request._dtb_route_identity = {
            "model_name": "model", "lora_request": "lora", "data_parallel_rank": 2,
            "trace_headers": {"traceparent": "test"}, "prompt_text": initial,
            "completion_token_limit": 100,
        }
        config = load_dtb_service_config()
        config = replace(config, algorithm=replace(
            config.algorithm, budget_enabled=True, enable_history_budget=False,
            fixed_budget_tokens=30, fixed_check_length=5,
        ), observability=DtbObservabilityConfig(
            mode=DtbTelemetryMode.PHASE if phase else DtbTelemetryMode.OFF))
        controller = DtbRequestController(service_config=config)
        context.tokenizer.decode = Mock(side_effect=AssertionError("unexpected decode"))
        conversation = [{"role": "user", "content": "latest user"}] if has_user else []
        expected_source = ("latest_user_message" if has_user else
                           "serving_prompt_fallback" if initial else "missing")
        contexts = []

        class CapturedMachine(DtbStateMachine):
            def __init__(self, ctx):
                contexts.append(ctx)
                super().__init__(ctx)

        with patch("dtb.request_controller.bind_reasoning_boundary_protocol",
                   return_value=fake_reasoning_protocol()), patch(
                       "dtb.request_controller.DtbStateMachine", CapturedMachine):
            response = await controller.handle_request(
                serving, conversation, [native], context.request, "request", context.tokenizer,
                SimpleNamespace(),
            )
            items = [item async for item in response] if stream else response
        assert len(engine.calls) == (1 if stream else 2)
        assert contexts[0].prompt_text_views.classification_source == expected_source
        assert contexts[0].question_text == ("latest user" if has_user else initial or "")
        assert contexts[0].request_state.completion_status == "success"
        if return_text:
            assert all(item.prompt is initial for item in items)
        if stream:
            assert submissions[0] is native
            assert contexts[0].request_state.budget_decision is None
            assert engine.calls[0]["kwargs"]["prompt_text"] is initial
        else:
            assert "prompt" not in submissions[-1]
            assert engine.calls[-1]["kwargs"]["prompt_text"] is None
        assert native["prompt_token_ids"] == [60]
        assert engine.calls[-1]["kwargs"]["data_parallel_rank"] == 2
        assert engine.calls[-1]["kwargs"]["trace_headers"] == {"traceparent": "test"}
        context.tokenizer.decode.assert_not_called()
        if phase:
            recorder = contexts[0].phase_recorder
            assert recorder is not None
            modules = recorder.module_snapshot()
            assert modules["controller.entry_to_first_submit"]["calls"] == 1
            assert modules["decision.select_text"]["calls"] == 1
            assert "decision.decode_prompt" not in modules
        else:
            assert controller.observability.writer is None
        controller.observability.close()
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_managed_lifecycle_preserves_error_and_cancellation(stream, failure):
    async def run():
        context, engine = make_context([])
        context.request.stream = stream
        context.request.return_prompt_text = True
        context.phase_recorder = None
        context.tokenizer.decode = Mock(side_effect=AssertionError("unexpected decode"))
        suspended = asyncio.Event()
        closed = asyncio.Event()
        submissions = []

        def generate(prompt, *args, **kwargs):
            submissions.append(prompt)
            async def produce():
                try:
                    yield result("partial", [10], finish_reason=None)
                    if failure == "error":
                        raise RuntimeError("scripted engine failure")
                    suspended.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            return produce()

        engine.generate = generate
        config = load_dtb_service_config()
        controller = DtbRequestController(service_config=replace(
            config, algorithm=replace(config.algorithm, enable_history_budget=False),
            observability=DtbObservabilityConfig(),
        ))
        outputs = controller._managed_generator(DtbStateMachine(context), context, context.request)
        if failure == "error":
            with pytest.raises(RuntimeError, match="DTB orchestration failed"):
                _ = [item async for item in outputs]
            assert context.request_state.completion_status == "error"
            assert context.orchestration_error == "scripted engine failure"
        elif stream:
            first = await anext(outputs)
            assert first.prompt == "<think>"
            await outputs.aclose()
            assert context.request_state.completion_status == "cancelled"
        else:
            consumer = asyncio.create_task(anext(outputs))
            await asyncio.wait_for(suspended.wait(), timeout=2)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert context.request_state.completion_status == "cancelled"
        await outputs.aclose()
        assert closed.is_set()
        assert submissions[0]["prompt_token_ids"] == [60]
        context.tokenizer.decode.assert_not_called()
    asyncio.run(run())


def test_entry_to_first_submit_is_an_overlapping_module_not_an_engine_phase():
    async def run():
        context, _ = make_context([
            [result("", [THINK_END_ID], finish_reason="stop", stop_reason=THINK_END_ID)],
            [result("answer", [30], finish_reason="stop")],
        ])
        lines = []
        writer = DtbEventWriter(32, lines.append)
        recorder = DtbPhaseRecorder(writer, context.policy.version)
        context.phase_recorder = recorder
        with patch("dtb.observability.phase_recorder.perf_counter", return_value=2.0):
            context.controller_entry_started_at = 1.0
            await run_and_drain_outputs(context)
        measured = recorder.module_snapshot()["controller.entry_to_first_submit"]
        assert measured["calls"] == 1
        assert measured["duration_ms"] == 1000.0
        assert "decision.decode_prompt" not in recorder.module_snapshot()
        assert writer.flush()
        writer.close()
        assert len(lines) == 2  # initial_think + final_answer only
    asyncio.run(run())
