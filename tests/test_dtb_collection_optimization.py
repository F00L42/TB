"""Output ownership and algorithm equivalence for collection/CPU optimizations."""
from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from unittest.mock import patch

import pytest

from dtb.reasoning.confidence_gates import assess_trial_confidence
from dtb.reasoning.generation_states import _BaseState
from dtb.reasoning.request_state import TrialObservation
from dtb.reasoning.token_filter import extract_value_logprobs
from test_dtb_state_machine import (
    THINK_END_ID,
    TOOL_CALL_BEGIN_ID,
    make_context,
    result,
    run_and_drain_outputs,
    sampled,
)


async def source_chunks(items):
    for item in items:
        yield item


@pytest.mark.parametrize("width", [1, 3, 64])
@pytest.mark.parametrize("boundary_id,marker", [
    (THINK_END_ID, ""),
    (TOOL_CALL_BEGIN_ID, "<tool_call>"),
])
def test_delta_partition_preserves_text_ids_and_ownership(width, boundary_id, marker):
    async def run():
        context, _ = make_context([], completion_limit=1000)
        chunks = [
            result("x" * min(width, 257 - start),
                   [42] * min(width, 257 - start), finish_reason=None)
            for start in range(0, 257, width)
        ]
        chunks += [result("", [], finish_reason=None),
                   result("tail", [], finish_reason=None),
                   result(marker, [boundary_id], finish_reason="stop",
                          stop_reason=boundary_id)]
        original = [(c.outputs[0].text, list(c.outputs[0].token_ids),
                     c.outputs[0].finish_reason, c.outputs[0].stop_reason)
                    for c in chunks]
        ids, count, last, text, stop, boundary = await _BaseState.consume_output(
            source_chunks(chunks), context, phase="iterative_think",
            child_request_id="child", output_kind="DELTA")
        assert ids == [42] * 257 + [boundary_id]
        assert count == 258
        assert text == "x" * 257 + "tail" + marker
        assert last is chunks[-1]
        assert stop == boundary_id
        assert boundary is not None
        forwarded = []
        while not context.result_queue.empty():
            forwarded.append(context.result_queue.get_nowait().outputs[0])
        expected = [(t, tokens) for t, tokens, _, _ in original if t or tokens]
        assert [(o.text, o.token_ids) for o in forwarded] == expected
        assert all(o.finish_reason is None and o.stop_reason is None
                   for o in forwarded)
        # Growing the collector must never grow an already-forwarded delta or
        # mutate the source object used by stream audit.
        ids.append(12345)
        assert [(o.text, o.token_ids) for o in forwarded] == expected
        assert [(c.outputs[0].text, c.outputs[0].token_ids,
                 c.outputs[0].finish_reason, c.outputs[0].stop_reason)
                for c in chunks] == original

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["DELTA", "CUMULATIVE", "FINAL_ONLY"])
def test_visible_collector_keeps_output_kind_semantics(kind):
    async def run():
        context, _ = make_context([])
        context.request.stream = False
        chunks = [result("old", [8], finish_reason=None),
                  result("new", [9, 10], finish_reason="length")]
        ids, count, last, text, stop, boundary = await _BaseState.consume_output(
            source_chunks(chunks), context, phase="initial_think",
            child_request_id="child", output_kind=kind)
        assert ids == ([8, 9, 10] if kind == "DELTA" else [9, 10])
        assert text == ("oldnew" if kind == "DELTA" else "new")
        assert count == len(ids)
        assert last is chunks[-1] and stop == "length" and boundary is None
        assert context.result_queue.get_nowait().outputs[0].token_ids == [8]
        ids.append(99)
        assert chunks[-1].outputs[0].token_ids == [9, 10]
        assert context.result_queue.get_nowait().outputs[0].token_ids == [9, 10]

    asyncio.run(run())


@pytest.mark.parametrize("values,value_indices", [
    ([], []),
    ([(None, None)], []),
    ([(-0.1, "{"), (-0.2, " ")], []),
    ([(-0.1, "A"), (None, None), (-0.7, "B"), (-0.2, "{")], [0, 2]),
    ([(-0.3, None), (-0.2, "faultNumber"), (-0.4, "</think>")], [2]),
    ([(math.log(0.93), "A")], [0]),
])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("bottom_k", [1, 2, 5])
def test_confidence_matches_reference_with_and_without_diagnostics(
    values, value_indices, enabled, bottom_k,
):
    observation = TrialObservation(
        sampled_token_ids=list(range(len(values))),
        sample_logprobs=[None if p is None else sampled(i, p, text)
                         for i, (p, text) in enumerate(values)],
    )
    valid = [p for p, _ in values if p is not None]
    average = sum(math.exp(p) for p in valid) / len(values) if values else 0.0
    ordered = sorted(values[i][0] for i in value_indices)
    raw = sum(ordered[:bottom_k]) / min(bottom_k, len(ordered)) if ordered else None
    use_value = enabled and len(ordered) >= bottom_k
    expected = min(math.exp(raw), 1.0) if use_value else average
    for diagnostics in (False, True):
        assessment = assess_trial_confidence(
            observation, enable_value_min_confidence=enabled,
            value_min_bottom_k=bottom_k, collect_diagnostics=diagnostics)
        assert assessment.available == bool(valid)
        assert assessment.score == (expected if valid else None)
        assert assessment.average_confidence == average
        assert assessment.pair_count == len(valid)
        assert assessment.method == (
            "confidence_unavailable_fail_open" if not valid else
            "value_min" if use_value else "average_fallback")
        if diagnostics:
            assert assessment.value_min_raw == raw
            assert assessment.value_pair_count == len(ordered)
            assert assessment.perplexity == (
                math.exp(-sum(valid) / len(valid)) if valid else 0.0)
        else:
            assert assessment.perplexity is None


def test_average_fast_path_omits_diagnostics_and_value_path_filters_once():
    observation = TrialObservation(
        sampled_token_ids=[1], sample_logprobs=[sampled(1, -0.1, "A")])
    with patch("dtb.reasoning.confidence_gates.cal_perplexity",
               side_effect=AssertionError("unused perplexity")):
        with patch("dtb.reasoning.confidence_gates.extract_value_logprobs",
                   side_effect=AssertionError("disabled Value-Min")):
            assess_trial_confidence(observation, enable_value_min_confidence=False,
                                    value_min_bottom_k=1, collect_diagnostics=False)
        with patch("dtb.reasoning.confidence_gates.extract_value_logprobs",
                   wraps=extract_value_logprobs) as filtering:
            assess_trial_confidence(observation, enable_value_min_confidence=True,
                                    value_min_bottom_k=1, collect_diagnostics=False)
            assert filtering.call_count == 1


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("scenario", ["accepted", "rejected", "minimum", "missing"])
def test_phase_and_off_keep_same_generation_trace(enabled, scenario):
    async def run(phase):
        confidence = None if scenario == "missing" else (
            -2.0 if scenario == "rejected" else -0.01)
        scripts = [[result("thought", [10])],
                   [result("trial", [20], confidence=confidence)]]
        if scenario in ("rejected", "minimum"):
            scripts += [[result("more", [11, 12, 13])],
                        [result("trial2", [21], confidence=-0.01)]]
        scripts += [[result("answer", [30], finish_reason="stop", stop_reason="eos")]]
        context, engine = make_context(
            scripts, min_think_budget=4 if scenario == "minimum" else 1)
        context.config = replace(context.config, enable_value_min_confidence=enabled)
        recorder = context.phase_recorder
        if not phase:
            context.phase_recorder = None
        outputs = await run_and_drain_outputs(context)
        state = context.request_state
        assert state.completion_status == "success"
        assert state.child_generate_calls == (5 if scenario in ("rejected", "minimum") else 3)
        if phase:
            probes = [p for p in recorder.phases if p["phase"] == "confidence_probe"]
            assert probes[0]["accept_reason"] == {
                "accepted": "value_min" if enabled else "average_fallback",
                "rejected": "below_threshold", "minimum": "min_think_budget",
                "missing": "confidence_unavailable_fail_open",
            }[scenario]
            if scenario != "missing":
                assert probes[0]["value_min_raw"] == confidence
                assert probes[0]["value_pair_count"] == 1
        return (engine.calls,
                [(o.text, o.token_ids, o.finish_reason, o.stop_reason) for o in outputs],
                state.thinking_tokens, state.hidden_probe_tokens, state.final_answer_tokens,
                state.emitted_completion_tokens, state.final_answer_reason, state.probe_round)

    assert asyncio.run(run(False)) == asyncio.run(run(True))
