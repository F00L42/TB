from __future__ import annotations

import unittest
from types import SimpleNamespace

from think_budget_vllm_adapter.budget_controller import BudgetController
from think_budget_vllm_adapter.deer_think_budget.context import (
    RequestStateContext,
    ThinkBudgetConfig,
)
from think_budget_vllm_adapter.deer_think_budget.state import (
    _BaseState,
    _emit_think_end_once,
)


class FakeTokenizer:
    def __init__(self) -> None:
        self.encode_calls = 0

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        self.encode_calls += 1
        return [ord(char) for char in text]

    def decode(self, token_ids, skip_special_tokens: bool = False) -> str:
        return "".join(chr(token_id) for token_id in token_ids)


def make_context() -> RequestStateContext:
    tokenizer = FakeTokenizer()
    request = SimpleNamespace(priority=7)
    serving = SimpleNamespace(engine_client=object(), default_sampling_params={})
    return RequestStateContext(
        request=request,
        tokenizer=tokenizer,
        config=ThinkBudgetConfig(),
        serving_chat=serving,
        request_id="parent",
        engine_prompt={
            "prompt": "base",
            "prompt_token_ids": [1, 2],
            "multi_modal_data": object(),
        },
        lora_request="lora",
        data_parallel_rank=3,
        trace_headers={"traceparent": "x"},
        prompt_text="base",
        reasoning_ended=False,
        reasoning_parser_kwargs={"chat_template_kwargs": {"x": 1}},
    )


class PromptCopyTests(unittest.TestCase):
    def test_child_prompt_copies_only_dict_and_token_list(self) -> None:
        context = make_context()
        shared_mm = context.engine_prompt["multi_modal_data"]

        first = context.child_prompt("xy")
        second = context.child_prompt("xy")

        self.assertIsNot(first, context.engine_prompt)
        self.assertIs(first["multi_modal_data"], shared_mm)
        self.assertEqual(first["prompt_token_ids"], [1, 2, ord("x"), ord("y")])
        self.assertEqual(context.engine_prompt["prompt_token_ids"], [1, 2])
        self.assertEqual(first["prompt"], "basexy")
        self.assertEqual(second, first)
        self.assertEqual(context.tokenizer.encode_calls, 1)

    def test_engine_kwargs_preserve_native_route_context(self) -> None:
        context = make_context()
        kwargs = context.engine_request_kwargs(
            prompt_text="branch", reasoning_ended=True)
        self.assertEqual(kwargs["priority"], 7)
        self.assertEqual(kwargs["lora_request"], "lora")
        self.assertEqual(kwargs["data_parallel_rank"], 3)
        self.assertEqual(kwargs["trace_headers"], {"traceparent": "x"})
        self.assertEqual(kwargs["prompt_text"], "branch")
        self.assertIs(kwargs["reasoning_ended"], True)
        self.assertEqual(
            kwargs["reasoning_parser_kwargs"],
            {"chat_template_kwargs": {"x": 1}},
        )


class ThinkEndCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_emit_think_end_does_not_deepcopy_or_mutate_source(self) -> None:
        context = make_context()
        original_output = SimpleNamespace(text="reasoning", token_ids=[10, 11])
        other_output = SimpleNamespace(text="other", token_ids=[12])
        original = SimpleNamespace(
            outputs=[original_output, other_output],
            prompt_token_ids=list(range(1000)),
            metrics=SimpleNamespace(queued_ts=1.0),
        )
        context.last_chunk = original

        emitted = await _emit_think_end_once(context)
        queued = await context.result_queue.get()

        self.assertTrue(emitted)
        self.assertIsNot(queued, original)
        self.assertIsNot(queued.outputs[0], original_output)
        self.assertIs(queued.outputs[1], other_output)
        self.assertIs(queued.prompt_token_ids, original.prompt_token_ids)
        self.assertEqual(queued.outputs[0].text, "</think>")
        self.assertEqual(original_output.text, "reasoning")

    async def test_consumer_preserves_terminal_reason_for_phase_metrics(self) -> None:
        context = make_context()
        completion = SimpleNamespace(
            text="delta",
            token_ids=[10],
            logprobs=None,
            finish_reason="length",
            stop_reason=None,
        )
        chunk = SimpleNamespace(outputs=[completion])

        async def outputs():
            yield chunk

        token_ids, last_chunk, text, stop_reason = await _BaseState.consume_output(
            outputs(), context)

        self.assertEqual(token_ids, [10])
        self.assertIs(last_chunk, chunk)
        self.assertEqual(text, "delta")
        self.assertEqual(stop_reason, "length")
        self.assertIsNone(completion.finish_reason)


class ResponseContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_formatter_receives_native_reasoning_context(self) -> None:
        captured = {}

        class Serving:
            def chat_completion_stream_generator(self, *args, **kwargs):
                captured.update(kwargs)
                return "stream"

        response = await BudgetController._format_response(
            Serving(),
            SimpleNamespace(stream=True),
            SimpleNamespace(),
            "request",
            "model",
            [],
            FakeTokenizer(),
            SimpleNamespace(),
            reasoning_parser="parser",
            response_parser="response-parser",
            chat_template_kwargs={"key": "value"},
        )

        self.assertEqual(response, "stream")
        self.assertEqual(captured["reasoning_parser"], "parser")
        self.assertEqual(captured["chat_template_kwargs"], {"key": "value"})

    async def test_full_formatter_receives_native_response_parser(self) -> None:
        captured = {}

        class Serving:
            async def chat_completion_full_generator(self, *args, **kwargs):
                captured.update(kwargs)
                return "full"

        response = await BudgetController._format_response(
            Serving(),
            SimpleNamespace(stream=False),
            SimpleNamespace(),
            "request",
            "model",
            [],
            FakeTokenizer(),
            SimpleNamespace(),
            reasoning_parser="reasoning-parser",
            response_parser="response-parser",
            chat_template_kwargs={"key": "value"},
        )

        self.assertEqual(response, "full")
        self.assertEqual(captured, {"parser": "response-parser"})


if __name__ == "__main__":
    unittest.main()
