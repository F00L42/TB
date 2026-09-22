"""Final-state contract: terminals, delayed boundaries, failures and counters."""
from __future__ import annotations

import asyncio

import pytest

from dtb.reasoning.generation_states import _FinalAnswerState
from dtb.reasoning.request_state import FinalAnswerReason, ReasoningState
from test_dtb_state_machine import make_context, result


def final_context(chunks, *, stream=True, kind="DELTA", phase=False, delayed=False):
    context, engine = make_context([chunks], completion_limit=1000)
    context.request.stream = stream
    context.request_state.final_answer_reason = (
        FinalAnswerReason.BUDGET_EXHAUSTED if delayed
        else FinalAnswerReason.MODEL_THINK_END
    )
    if not delayed:
        context.request_state.reasoning_state = ReasoningState.CLOSED
        context.reasoning_boundary_emitted = True
    if not phase:
        context.phase_recorder = None
    sampling = context.request.to_sampling_params

    def params(*args):
        value = sampling(*args)
        value.output_kind.name = kind
        return value

    context.request.to_sampling_params = params
    return context, engine


def queued(context):
    values = []
    while not context.result_queue.empty():
        values.append(context.result_queue.get_nowait())
    return values


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("phase", [False, True])
@pytest.mark.parametrize("kind", ["DELTA", "CUMULATIVE", "FINAL_ONLY"])
@pytest.mark.parametrize("delayed", [False, True])
def test_final_keeps_identity_order_limits_and_phase(stream, phase, kind, delayed):
    async def run():
        chunks = [result("a", [11], finish_reason=None),
                  result("", [], finish_reason=None),
                  result("尾", [], finish_reason=None),
                  result("answer", [12, 13], finish_reason="stop", stop_reason="EOS")]
        context, engine = final_context(
            chunks, stream=stream, kind=kind, phase=phase, delayed=delayed)
        state = context.request_state
        assert await _FinalAnswerState.handle(context) is None
        forwarded = queued(context)
        assert all(actual is expected for actual, expected in zip(forwarded[-4:], chunks))
        assert len(forwarded) == 4 + int(delayed)
        emitted = sum(len(item.outputs[0].token_ids) for item in forwarded)
        boundary_tokens = len(forwarded[0].outputs[0].token_ids) if delayed else 0
        assert state.emitted_completion_tokens == emitted == 3 + boundary_tokens
        assert state.final_answer_tokens == (3 if kind == "DELTA" else 2)
        assert state.completion_status == "success"
        assert len(engine.calls) == 1
        call = engine.calls[0]
        assert call["max_tokens"] == min(
            1000 - len(call["prompt_token_ids"]), 1000 - boundary_tokens)
        assert call["kwargs"]["reasoning_ended"] is True
        assert call["kwargs"]["priority"] == context.request.priority
        if phase:
            record, = context.phase_recorder.phases
            assert record["output"] is chunks[-1]
            assert record["generated_tokens"] == state.final_answer_tokens
            assert record["emitted_completion_tokens_before"] == 0
            assert record["pending_boundary_tokens"] == boundary_tokens
            assert record["external_remaining_tokens"] == 1000 - boundary_tokens
            assert record["answer_limit"] == call["max_tokens"]

    asyncio.run(run())


@pytest.mark.parametrize("phase", [False, True])
@pytest.mark.parametrize("case,message", [
    ("empty", "final_answer completed without a vLLM output"),
    ("unterminated", "final_answer completed without a terminal output"),
    ("double_terminal", "final answer child emitted multiple terminals"),
    ("after_terminal", "final answer child emitted output after terminal"),
])
def test_final_rejects_invalid_stream_without_emitting_success(case, message, phase):
    async def run():
        ordinary = result("x", [1], finish_reason=None)
        terminal = result("y", [2], finish_reason="stop")
        chunks = {
            "empty": [], "unterminated": [ordinary],
            "double_terminal": [ordinary, terminal, terminal],
            "after_terminal": [ordinary, terminal, ordinary],
        }[case]
        context, _ = final_context(chunks, phase=phase)
        with pytest.raises(RuntimeError, match=f"^{message}$"):
            await _FinalAnswerState.handle(context)
        assert all(item.outputs[0].finish_reason is None for item in queued(context))
        assert context.request_state.final_answer_tokens == 0
        if phase:
            assert context.phase_recorder.phases == []

    asyncio.run(run())


@pytest.mark.parametrize("delayed", [False, True])
@pytest.mark.parametrize("text,ids", [("", []), ("broken", [9])])
def test_final_error_terminal_is_forwarded_without_success(delayed, text, ids):
    async def run():
        error = result(text, ids, finish_reason="error", stop_reason="engine")
        context, _ = final_context([error], phase=True, delayed=delayed)
        await _FinalAnswerState.handle(context)
        forwarded = queued(context)
        assert forwarded[-1] is error
        assert len(forwarded) == 1 + int(delayed)
        assert context.request_state.completion_status == "error"
        assert context.request_state.final_answer_tokens == 0
        assert context.phase_recorder.phases == []

    asyncio.run(run())


@pytest.mark.parametrize("after_terminal", [False, True])
@pytest.mark.parametrize("failure", ["exception", "cancel"])
def test_final_propagates_generator_failure_and_cancellation(after_terminal, failure):
    async def run():
        chunk = result("a", [1], finish_reason="stop" if after_terminal else None)
        context, engine = final_context([], phase=True)
        reached = asyncio.Event()
        release = asyncio.Event()

        async def generate(*args, **kwargs):
            yield chunk
            reached.set()
            await release.wait()
            raise ValueError("engine failure")

        engine.generate = generate
        task = asyncio.create_task(_FinalAnswerState.handle(context))
        await asyncio.wait_for(reached.wait(), 2)
        if failure == "cancel":
            task.cancel()
            expected = asyncio.CancelledError
        else:
            release.set()
            expected = ValueError
        with pytest.raises(expected):
            await task
        assert queued(context) == ([] if after_terminal else [chunk])
        assert context.request_state.final_answer_tokens == 0
        assert context.phase_recorder.phases == []

    asyncio.run(run())


def test_final_propagates_consumer_failure():
    async def run():
        context, _ = final_context([result("a", [1], finish_reason=None)])

        async def broken_put(item):
            raise RuntimeError("consumer failure")

        context.result_queue.put = broken_put
        with pytest.raises(RuntimeError, match="consumer failure"):
            await _FinalAnswerState.handle(context)
        assert context.request_state.final_answer_tokens == 0

    asyncio.run(run())
