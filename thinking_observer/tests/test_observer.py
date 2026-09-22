"""CPU protocol tests: fake engine, real async lifecycle, no model dependency."""
import asyncio
import copy
from enum import Enum
import os
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from thinking_observer import (
    CONTINUE, BoundaryProtocol, NoopDetector, ObserverConfig, ObserverContext,
    StopAndAnswer, ThinkingObserver, TokenLimitDetector,
)
from thinking_observer.integration import (
    VllmObserver, aggregate_deltas, bind_qwen3, capability_rejection,
    close_with_response,
)


class Kind(Enum):
    DELTA = 1
    FINAL_ONLY = 2


class Tokenizer:
    all_special_ids = [90, 91, 92, 93]

    def encode(self, text, add_special_tokens=False):
        if text == "</think>":
            return [90]
        if text == "<tool_call>":
            return [91]
        if text == "<|im_end|>":
            return [92]
        return [ord(c) + 100 for c in text]

    def get_vocab(self):
        return {"</think>": 90, "<tool_call>": 91}


def result(text="", ids=(), finish=None, stop=None):
    return NS(request_id="unused", prompt="<think>", prompt_token_ids=[93],
              prompt_logprobs=None, num_cached_tokens=1, metrics=None,
              finished=finish is not None, outputs=[NS(
                  index=0, text=text, token_ids=list(ids), finish_reason=finish,
                  stop_reason=stop, logprobs=None, cumulative_logprob=None,
              )])


class Engine:
    def __init__(self, before=(), tail=(), final=()):
        self.before, self.tail, self.final = before, tail, final
        self.calls, self.aborts, self.closed = [], [], []
        self.abort_error = None

    async def abort(self, request_id):
        self.aborts.append(request_id)
        if self.abort_error:
            raise self.abort_error

    def generate(self, prompt, params, request_id, **kwargs):
        first = not self.calls
        self.calls.append((prompt, params, request_id, kwargs))

        async def run():
            try:
                for sequence in ((self.before, self.tail) if first else (self.final,)):
                    for item in sequence:
                        if isinstance(item, BaseException):
                            raise item
                        if isinstance(item, asyncio.Event):
                            await item.wait()
                            continue
                        output = copy.deepcopy(item)
                        output.request_id = request_id
                        yield output
            finally:
                self.closed.append(request_id)
        return run()


class RecordingDetector:
    def __init__(self, action=lambda d: CONTINUE):
        self.action, self.states = action, []

    def start(self, context):
        owner = self

        class State:
            def __init__(self):
                self.context, self.deltas, self.outcomes = context, [], []

            def observe(self, delta):
                self.deltas.append(delta)
                return owner.action(delta)

            def close(self, outcome):
                self.outcomes.append(outcome)

        state = State()
        self.states.append(state)
        return state


def setup(engine, detector=None, *, limit=40, capacity=300, config=None, request_id="r"):
    detector = detector or RecordingDetector()
    config = config or ObserverConfig(enabled=True, answer_cue="cue", callback_limit_ms=1000)
    controller = ThinkingObserver(detector, config)
    prompt = {"prompt": "<think>", "prompt_token_ids": [93], "cache_salt": "tenant"}
    params = NS(output_kind=Kind.DELTA, max_tokens=limit, n=1)
    context = ObserverContext(request_id, "test-model", BoundaryProtocol(90, 91),
                              limit, capacity, 1, Tokenizer())
    kwargs = dict(priority=7, lora_request=object(), trace_headers={"trace": "a"},
                  data_parallel_rank=3, reasoning_ended=False,
                  reasoning_parser_kwargs={"chat_template_kwargs": {"enable_thinking": True}})
    return controller.stream(engine, prompt, params, context, kwargs), detector, prompt, params, kwargs


async def collect(source):
    return [item async for item in source]


def ids(outputs):
    return [token for item in outputs for token in item.outputs[0].token_ids]


def text(outputs):
    return "".join(item.outputs[0].text for item in outputs)


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_trigger_preserves_deltas_one_child_and_terminal(self):
        script = [result("思", [1]), result("", [2]), result("考", []),
                  result("</think>answer", [90, 3], "stop")]
        engine = Engine(script)
        source, detector, prompt, params, _ = setup(engine)
        output = await collect(source)
        self.assertEqual(ids(output), [1, 2, 90, 3])
        self.assertEqual(text(output), "思考</think>answer")
        self.assertEqual(len(engine.calls), 1)
        self.assertIs(engine.calls[0][0], prompt)
        self.assertIs(engine.calls[0][1], params)
        self.assertEqual(engine.aborts, [])
        self.assertEqual(sum(o.finished for o in output), 1)
        self.assertEqual(detector.states[0].outcomes[0].status, "native_complete")
        self.assertEqual(detector.states[0].deltas[2].start, 2)
        self.assertEqual(detector.states[0].deltas[-1].boundary, "think_end")

    async def test_custom_cue_handoff_batch_and_drain_overshoot(self):
        engine = Engine([result("abcde", [1, 2, 3, 4, 5])],
                        [result("f", [6]), result("g", [7], "abort")],
                        [result("OK", [8, 9], "stop")])
        detector = RecordingDetector(lambda d: StopAndAnswer("external", 3, 1, "edit"))
        source, _, prompt, params, kwargs = setup(engine, detector, limit=12)
        output = await collect(source)
        self.assertEqual(ids(output), [1, 2, 3, 4, 5, 6, 7, 90, 8, 9])
        self.assertEqual(text(output), "abcdefg</think>OK")
        self.assertEqual(engine.calls[1][0]["prompt_token_ids"],
                         [93, 1, 2, 3, 4, 5, 6, 7, *Tokenizer().encode("edit"), 90])
        self.assertNotIn("prompt", engine.calls[1][0])
        self.assertEqual(engine.calls[1][0]["cache_salt"], "tenant")
        self.assertEqual(engine.calls[1][1].max_tokens, 4)
        self.assertEqual(params.max_tokens, 12)
        self.assertEqual(prompt["prompt_token_ids"], [93])
        for key in ("priority", "lora_request", "trace_headers", "data_parallel_rank", "reasoning_parser_kwargs"):
            self.assertIs(engine.calls[1][3][key], kwargs[key])
        self.assertTrue(engine.calls[1][3]["reasoning_ended"])
        self.assertEqual(engine.aborts, ["r_observer"])
        outcome = detector.states[0].outcomes[0]
        self.assertEqual((outcome.detected_at, outcome.repeat_start, outcome.actual_handoff,
                          outcome.emitted_at_detection, outcome.overshoot_tokens), (3, 1, 7, 5, 4))
        self.assertEqual(outcome.status, "handed_off")
        self.assertEqual([c.generated_tokens for c in outcome.children], [7, 2])
        self.assertEqual(len(detector.states[0].deltas), 1)
        self.assertTrue(all(o.prompt_token_ids == [93] for o in output))

    async def test_same_batch_boundary_wins_decision_and_no_final_in_detector(self):
        engine = Engine([result("a</think>answer", [1, 90, 2]), result("done", [3], "stop")])
        detector = RecordingDetector(lambda d: StopAndAnswer("threshold", 1))
        source, *_ = setup(engine, detector)
        output = await collect(source)
        self.assertEqual(ids(output), [1, 90, 2, 3])
        self.assertEqual(engine.aborts, [])
        self.assertEqual(detector.states[0].deltas[0].token_ids, (1,))
        self.assertEqual(detector.states[0].deltas[0].text, "a")
        self.assertEqual(len(detector.states[0].deltas), 1)

    async def test_terminal_wins_even_without_boundary(self):
        engine = Engine([result("abc", [1, 2, 3], "length")])
        source, *_ = setup(engine, TokenLimitDetector(1))
        output = await collect(source)
        self.assertEqual(engine.aborts, [])
        self.assertEqual(output[-1].outputs[0].finish_reason, "length")

    async def test_natural_terminal_during_abort_wins(self):
        engine = Engine([result("a", [1])], [result("</think>OK", [90, 2], "stop")])
        detector = RecordingDetector(lambda d: StopAndAnswer("threshold", 1))
        output = await collect(setup(engine, detector)[0])
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(ids(output), [1, 90, 2])
        self.assertEqual(detector.states[0].outcomes[0].status, "native_won_race")

    async def test_tool_boundary_in_abort_tail_continues_exact_prefix(self):
        engine = Engine([result("a", [1])],
                        [result("<tool_call><function=f>", [91, 2], "abort")],
                        [result("</function></tool_call>", [3, 4], "stop")])
        output = await collect(setup(engine, TokenLimitDetector(1))[0])
        self.assertEqual(ids(output), [1, 91, 2, 3, 4])
        self.assertNotIn("</think>", text(output))
        self.assertEqual(engine.calls[1][0]["prompt_token_ids"], [93, 1, 91, 2])

    async def test_empty_abort_text_tail_preserved(self):
        engine = Engine([result("a", [1])], [result("尾", [], "abort")],
                        [result("好", [2], "stop")])
        output = await collect(setup(engine, TokenLimitDetector(1))[0])
        self.assertEqual(text(output), "a尾</think>好")
        self.assertEqual(ids(output), [1, 90, 2])

    async def test_nonstream_aggregate_uses_text_boundary_and_single_terminal(self):
        engine = Engine([result("a", [1])], [result("b", [2], "abort")],
                        [result("answer", [3, 4], "stop")])
        output = await collect(aggregate_deltas(setup(engine, TokenLimitDetector(1))[0]))
        self.assertEqual(len(output), 1)
        self.assertEqual(text(output), "ab</think>answer")
        self.assertEqual(ids(output), [1, 2, 90, 3, 4])
        self.assertTrue(output[0].finished)

    async def test_capacity_rejects_before_abort(self):
        for limit, capacity in ((2, 100), (40, 4)):
            engine = Engine([result("a", [1]), result("b", [2], "length")])
            detector = RecordingDetector(lambda d: StopAndAnswer("threshold", 1))
            await collect(setup(engine, detector, limit=limit, capacity=capacity)[0])
            self.assertEqual(engine.aborts, [])
            self.assertEqual(detector.states[0].outcomes[0].skipped_reason, "continuation_capacity")

    async def test_capacity_consumed_by_drain_fails_without_fake_answer(self):
        engine = Engine([result("a", [1])], [result("bc", [2, 3], "abort")])
        detector = RecordingDetector(lambda d: StopAndAnswer("threshold", 1))
        with self.assertRaisesRegex(RuntimeError, "capacity after abort"):
            await collect(setup(engine, detector, limit=3)[0])
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(detector.states[0].outcomes[0].status, "error")

    async def test_detector_exception_disarms_and_closes_once(self):
        def fail(delta):
            raise ValueError("algorithm failure")
        detector = RecordingDetector(fail)
        engine = Engine([result("a", [1]), result("b", [2]), result("c", [3], "stop")])
        output = await collect(setup(engine, detector)[0])
        self.assertEqual(ids(output), [1, 2, 3])
        self.assertEqual(engine.aborts, [])
        self.assertEqual(len(detector.states[0].deltas), 1)
        self.assertEqual(len(detector.states[0].outcomes), 1)
        self.assertEqual(detector.states[0].outcomes[0].detector_error, "ValueError")

    async def test_invalid_decision_or_structural_cue_falls_back(self):
        for decision in (None, StopAndAnswer("bad", 200), StopAndAnswer("bad", 1, 2),
                         StopAndAnswer("bad", 1, answer_cue="</think>"),
                         StopAndAnswer("bad", 1, answer_cue="<|im_end|>")):
            engine = Engine([result("a", [1]), result("b", [2], "stop")])
            detector = RecordingDetector(lambda d: decision)
            await collect(setup(engine, detector)[0])
            self.assertEqual(engine.aborts, [])
            self.assertIsNotNone(detector.states[0].outcomes[0].detector_error)

    async def test_async_callback_is_rejected_without_awaiting(self):
        async def action(delta):
            raise AssertionError("must not execute")
        detector = RecordingDetector(action)
        engine = Engine([result("a", [1]), result("b", [2], "stop")])
        await collect(setup(engine, detector)[0])
        self.assertEqual(detector.states[0].outcomes[0].detector_error, "TypeError")

    async def test_callback_cost_disables_before_abort(self):
        engine = Engine([result("a", [1]), result("b", [2], "stop")])
        detector = RecordingDetector(lambda d: StopAndAnswer("threshold", 1))
        # Deterministic timer: start 1 ms, observe 100 ms, budget 10 ms.
        with patch("thinking_observer.controller.perf_counter", side_effect=[0, .001, .01, .11]):
            await collect(setup(engine, detector, config=ObserverConfig(enabled=True))[0])
        self.assertEqual(engine.aborts, [])
        self.assertEqual(detector.states[0].outcomes[0].detector_error, "callback_cost_exceeded")

    async def test_cancel_propagates_and_cleans_active_child(self):
        engine = Engine([result("a", [1]), asyncio.Event()])
        source, detector, *_ = setup(engine)
        await anext(source)
        task = asyncio.create_task(anext(source))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(engine.aborts, ["r_observer"])
        self.assertEqual(engine.closed, ["r_observer"])
        self.assertEqual(detector.states[0].outcomes[0].status, "cancelled")

    async def test_detector_cancellation_is_not_fail_open(self):
        def cancel(delta):
            raise asyncio.CancelledError()
        engine = Engine([result("a", [1])])
        source, detector, *_ = setup(engine, RecordingDetector(cancel))
        with self.assertRaises(asyncio.CancelledError):
            await collect(source)
        self.assertEqual(engine.aborts, ["r_observer"])
        self.assertEqual(detector.states[0].outcomes[0].status, "cancelled")

    async def test_response_close_closes_underlying_engine(self):
        engine = Engine([result("a", [1]), result("b", [2])])
        source, detector, *_ = setup(engine)
        async def response():
            async for item in source:
                yield item
        wrapped = close_with_response(response(), source)
        await anext(wrapped)
        await wrapped.aclose()
        self.assertEqual(engine.aborts, ["r_observer"])
        self.assertEqual(len(detector.states[0].outcomes), 1)

    async def test_abort_timeout_error_and_missing_terminal(self):
        for tail in ([asyncio.Event()], [], [RuntimeError("engine error")]):
            engine = Engine([result("a", [1])], tail)
            config = ObserverConfig(enabled=True, answer_cue="", abort_timeout_s=.01,
                                    callback_limit_ms=1000)
            source, detector, *_ = setup(engine, RecordingDetector(lambda d: StopAndAnswer("x", 1)), config=config)
            with self.assertRaises((RuntimeError, TimeoutError)):
                await collect(source)
            self.assertEqual(engine.closed, ["r_observer"])
            self.assertEqual(len(detector.states[0].outcomes), 1)

    async def test_abort_failure_propagates(self):
        engine = Engine([result("a", [1])])
        engine.abort_error = RuntimeError("abort failure")
        with self.assertRaisesRegex(RuntimeError, "abort failure"):
            await collect(setup(engine, TokenLimitDetector(1))[0])
        self.assertEqual(engine.closed, ["r_observer"])

    async def test_final_cancel_cleans_final_child(self):
        engine = Engine([result("a", [1])], [result(finish="abort")],
                        [result("answer", [2]), asyncio.Event()])
        source, detector, *_ = setup(engine, RecordingDetector(lambda d: StopAndAnswer("x", 1)))
        async for item in source:
            if item.outputs[0].text == "answer":
                break
        await source.aclose()
        self.assertEqual(engine.aborts, ["r_observer", "r_observer_final"])
        self.assertEqual(len(detector.states[0].outcomes), 1)

    async def test_engine_contract_errors_do_not_restart(self):
        scripts = ([], [result("a", [1])], [result(finish="abort")],
                   [result(finish="error")], [result(finish="stop"), result("late", [1])])
        for script in scripts:
            engine = Engine(script)
            with self.assertRaises(RuntimeError):
                await collect(setup(engine)[0])
            self.assertEqual(len(engine.calls), 1)

    async def test_start_and_close_failures_keep_native_answer(self):
        class BadStart:
            def start(self, context):
                raise ValueError("start failed")
        engine = Engine([result("answer", [1], "stop")])
        output = await collect(setup(engine, BadStart())[0])
        self.assertEqual(text(output), "answer")
        class BadClose:
            count = 0
            def start(self, context): return self
            def observe(self, delta): return CONTINUE
            def close(self, outcome):
                self.count += 1
                raise ValueError("close failed")
        detector = BadClose()
        output = await collect(setup(Engine([result("answer", [1], "stop")]), detector)[0])
        self.assertEqual(text(output), "answer")
        self.assertEqual(detector.count, 1)

    async def test_final_error_or_missing_terminal_propagates(self):
        for final in ([], [result("x", [2])], [result(finish="error")],
                      [result(finish="stop"), result("late", [2])]):
            engine = Engine([result("a", [1])], [result(finish="abort")], final)
            detector = RecordingDetector(lambda d: StopAndAnswer("test", 1))
            with self.assertRaises(RuntimeError):
                await collect(setup(engine, detector)[0])
            self.assertEqual(len(engine.calls), 2)
            self.assertEqual(detector.states[0].outcomes[0].status, "error")

    async def test_drain_chunk_bound_prevents_unbounded_tail(self):
        engine = Engine([result("a", [1])], [result(), result(), result(finish="abort")])
        config = ObserverConfig(enabled=True, max_drain_chunks=1, callback_limit_ms=1000)
        with self.assertRaisesRegex(RuntimeError, "chunk limit"):
            await collect(setup(engine, TokenLimitDetector(1), config=config)[0])
        self.assertEqual(engine.closed, ["r_observer"])

    async def test_completion_contract_overflow_is_failure(self):
        engine = Engine([result("abc", [1, 2, 3], "stop")])
        with self.assertRaisesRegex(RuntimeError, "completion limit"):
            await collect(setup(engine, limit=2)[0])

    async def test_telemetry_is_opt_in_and_child_accounting_separate(self):
        for mode in ("off", "summary"):
            engine = Engine([result("a", [1])], [result(finish="abort")],
                            [result("answer", [2], "stop")])
            with patch("thinking_observer.controller.LOG.info") as log:
                await collect(setup(engine, TokenLimitDetector(1), config=ObserverConfig(
                    enabled=True, telemetry=mode, callback_limit_ms=1000))[0])
            self.assertEqual(log.call_count, int(mode == "summary"))
            if mode == "summary":
                import json
                data = json.loads(log.call_args.args[1])
                self.assertEqual(data["schema"], 1)
                self.assertEqual(data["emitted_tokens"], 3)  # includes synthetic close
                self.assertEqual(sum(c["generated_tokens"] for c in data["children"]), 2)
                self.assertGreater(data["cue_tokens"], 0)
                self.assertNotIn("text", data)

    async def test_concurrent_request_states_are_isolated(self):
        detector = RecordingDetector()
        engines = [Engine([result(str(i), [i], "stop")]) for i in range(20)]
        await asyncio.gather(*(collect(setup(e, detector, request_id=f"r{i}")[0])
                               for i, e in enumerate(engines)))
        self.assertEqual(len({id(s) for s in detector.states}), 20)
        self.assertEqual({s.context.request_id for s in detector.states}, {f"r{i}" for i in range(20)})
        self.assertTrue(all(len(s.outcomes) == 1 for s in detector.states))

    async def test_hidden_boundary_text_is_not_guessed(self):
        engine = Engine([result("mixed", [1, 90, 2]), result(finish="stop")])
        source, detector, *_ = setup(engine)
        await collect(source)
        self.assertIsNone(detector.states[0].deltas[0].text)
        self.assertEqual(detector.states[0].deltas[0].token_ids, (1,))


class IntegrationTests(unittest.TestCase):
    def test_disabled_does_not_import_detector(self):
        with patch.dict(os.environ, {"THINKING_OBSERVER_ENABLED": "0",
                                    "THINKING_OBSERVER_FACTORY": "missing:Detector"}):
            self.assertFalse(VllmObserver.from_env().enabled)

    def test_default_enabled_factory_is_noop(self):
        with patch.dict(os.environ, {"THINKING_OBSERVER_ENABLED": "1"}, clear=True):
            adapter = VllmObserver.from_env()
        self.assertIsInstance(adapter.observer.detector, NoopDetector)

    def test_rejections_cover_stateful_shapes(self):
        request, prompt, params, engine = NS(), {"prompt_token_ids": [93]}, NS(), Engine()
        self.assertIsNone(capability_rejection(request, prompt, params, engine, False))
        for field, value in (("n", 2), ("stop", ["x"]), ("min_tokens", 1),
                             ("presence_penalty", .1), ("repetition_penalty", 1.1),
                             ("structured_outputs", object()), ("logprobs", 0),
                             ("prompt_logprobs", 0), ("detokenize", False)):
            self.assertIsNotNone(capability_rejection(request, prompt, NS(**{field: value}), engine, False), field)
        for field in ("multi_modal_data", "prompt_embeds", "encoder_prompt"):
            self.assertIsNotNone(capability_rejection(request, dict(prompt, **{field: None}), params, engine, False))

    def test_parser_binding_checks_active_ids_and_types(self):
        Qwen = type("Qwen3ReasoningParser", (), {
            "end_token_id": 90,
            "is_reasoning_end_streaming": lambda self, old, delta: delta[0] in (90, 91),
        })
        parser = Qwen()
        response = NS(reasoning_parser=Qwen(), tool_parser=None)
        self.assertEqual(bind_qwen3(Tokenizer(), parser, response), BoundaryProtocol(90, 91))
        response.reasoning_parser.end_token_id = 80
        with self.assertRaises(ValueError):
            bind_qwen3(Tokenizer(), parser, response)

    def test_unsupported_route_never_starts_detector(self):
        detector = RecordingDetector()
        adapter = VllmObserver(ThinkingObserver(detector, ObserverConfig(enabled=True)))
        args = dict(request=NS(enable_thinking_observer=True), prompt={"prompt_token_ids": [93]},
                    params=NS(n=1, max_tokens=128), engine=Engine(), reasoning_ended=False,
                    tokenizer=Tokenizer(), reasoning_parser=None, response_parser=None,
                    request_id="r", model="x", max_model_len=4096)
        with self.assertLogs("thinking_observer.integration", level="WARNING") as logs:
            self.assertIsNone(adapter.prepare(**args))
        self.assertIn("unsupported_reasoning_parser", logs.output[0])
        args["engine"].vllm_config = NS(parallel_config=NS(data_parallel_size=2))
        with self.assertLogs("thinking_observer.integration", level="WARNING") as logs:
            self.assertIsNone(adapter.prepare(**args))
        self.assertIn("unpinned_data_parallel_rank", logs.output[0])
        self.assertEqual(detector.states, [])

    def test_import_has_no_dtb_or_vllm_dependencies(self):
        code = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in ('dtb', 'deer', 'vllm', 'torch', 'yaml'):
            raise AssertionError('forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Block())
import thinking_observer
import thinking_observer.integration
assert thinking_observer.NoopDetector()
print('independent import OK')
"""
        result = subprocess.run([sys.executable, "-c", code], text=True,
                                capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
