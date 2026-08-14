from __future__ import annotations

import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from think_budget_vllm_adapter.budget_controller import BudgetController
from think_budget_vllm_adapter.context import (
    BudgetPlan,
    BudgetVector,
    DecisionBranch,
    PerformancePolicy,
)
from think_budget_vllm_adapter.deer_think_budget.context import (
    FinalAnswerReason,
    RequestAlgorithmState,
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

    def get_vocab(self) -> dict[str, int]:
        return {
            "<tool_call>": 1000,
            "</tool_call>": 1001,
        }


class StructuralTokenizer(FakeTokenizer):
    def decode(self, token_ids, skip_special_tokens: bool = False) -> str:
        pieces = {
            1000: "" if skip_special_tokens else "<tool_call>",
            1001: "" if skip_special_tokens else "</tool_call>",
        }
        return "".join(pieces.get(token_id, chr(token_id)) for token_id in token_ids)


def make_context() -> RequestStateContext:
    tokenizer = FakeTokenizer()
    request = SimpleNamespace(priority=7)
    serving = SimpleNamespace(engine_client=object(), default_sampling_params={})
    plan = BudgetPlan(
        scene="general",
        scene_matched=False,
        decision_branch=DecisionBranch.HISTORY,
        budget=BudgetVector(10, 1, 2, 4),
        confidence_threshold=0.9,
        percentile_step=0.75,
        source_by_field={},
        policy_version="think-budget-performance-v1",
        hard_think_token_limit=20,
    )
    return RequestStateContext(
        request=request,
        tokenizer=tokenizer,
        config=ThinkBudgetConfig(),
        policy=PerformancePolicy(),
        serving_chat=serving,
        request_id="parent",
        engine_prompt={
            "prompt": "base",
            "prompt_token_ids": [1, 2],
            "multi_modal_data": object(),
        },
        algorithm_state=RequestAlgorithmState(budget_plan=plan),
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

    def test_close_reasoning_accepts_one_explicit_close_suffix(self) -> None:
        context = make_context()
        context.algorithm_state.final_answer_reason = (
            FinalAnswerReason.BUDGET_EXHAUSTED)
        suffix = "limited\n</think>\n\n"

        self.assertTrue(context.close_reasoning(suffix))
        self.assertTrue(context.prompt_text.endswith(suffix))
        self.assertFalse(context.close_reasoning("</think>"))


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

    async def test_consumer_preserves_terminal_reason_without_mutating_source(
        self,
    ) -> None:
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

        (
            token_ids,
            last_chunk,
            text,
            stop_reason,
            reasoning_boundary,
        ) = await _BaseState.consume_output(
            outputs(),
            context,
            phase="initial_think",
            child_request_id="child",
        )

        self.assertEqual(token_ids, [10])
        self.assertIs(last_chunk, chunk)
        self.assertEqual(text, "delta")
        self.assertEqual(stop_reason, "length")
        self.assertIsNone(reasoning_boundary)
        self.assertEqual(completion.finish_reason, "length")
        visible = await context.result_queue.get()
        self.assertIsNone(visible.outputs[0].finish_reason)

    async def test_final_answer_does_not_restore_missing_tool_marker_text(
        self,
    ) -> None:
        context = make_context()
        context.tokenizer = StructuralTokenizer()
        context.request.tools = [{"type": "function"}]
        source_output = SimpleNamespace(
            text="f",
            token_ids=[1000, ord("\n"), ord("<"), ord("f")],
            finish_reason=None,
            stop_reason=None,
        )
        source = SimpleNamespace(outputs=[source_output])

        await context.stream_output(
            source,
            phase="final_answer",
            child_request_id="final-1",
        )
        queued = await context.result_queue.get()

        self.assertIs(queued, source)
        self.assertIs(queued.outputs[0], source_output)
        self.assertEqual(queued.outputs[0].text, "f")
        self.assertEqual(queued.outputs[0].token_ids, source_output.token_ids)
        self.assertEqual(source_output.text, "f")

    async def test_regular_final_answer_delta_is_not_rewritten(self) -> None:
        context = make_context()
        context.tokenizer = StructuralTokenizer()
        context.request.tools = [{"type": "function"}]
        source = SimpleNamespace(outputs=[SimpleNamespace(
            text="ordinary answer",
            token_ids=[ord(character) for character in "ordinary answer"],
            finish_reason="stop",
            stop_reason=None,
        )])

        await context.stream_output(source, phase="final_answer")

        self.assertIs(await context.result_queue.get(), source)

    async def test_stream_debug_logs_raw_and_forwarded_digests(self) -> None:
        context = make_context()
        context.tokenizer = StructuralTokenizer()
        context.request.tools = [{"type": "function"}]
        context.stream_debug_enabled = True
        source = SimpleNamespace(outputs=[SimpleNamespace(
            text="f",
            token_ids=[1000, ord("\n"), ord("<"), ord("f")],
            finish_reason=None,
            stop_reason=None,
        )])
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            await context.stream_output(
                source,
                phase="final_answer",
                child_request_id="final-1",
            )

        line = stdout.getvalue().strip()
        self.assertTrue(line.startswith("TB_TOOL_STREAM "))
        record = json.loads(line.split(" ", 1)[1])
        self.assertEqual(record["schema"], 2)
        self.assertEqual(record["source"], "controller_pre_parser")
        self.assertEqual(record["raw_text"], "f")
        self.assertEqual(
            record["raw_text_sha256"], record["forwarded_text_sha256"])
        self.assertEqual(
            record["source_token_ids_sha256"],
            record["forwarded_token_ids_sha256"],
        )

    async def test_terminal_flush_is_empty_and_emitted_once(self) -> None:
        context = make_context()
        source_output = SimpleNamespace(
            text="answer",
            token_ids=[10],
            logprobs=["lp"],
            finish_reason="stop",
            stop_reason="eos",
        )
        source = SimpleNamespace(
            outputs=[source_output],
            finished=True,
        )

        await context.stream_terminal(
            source,
            phase="final_answer",
            child_request_id="final-1",
        )
        terminal = await context.result_queue.get()

        self.assertEqual(terminal.outputs[0].text, "")
        self.assertEqual(terminal.outputs[0].token_ids, [])
        self.assertEqual(terminal.outputs[0].finish_reason, "stop")
        self.assertEqual(terminal.outputs[0].stop_reason, "eos")
        self.assertEqual(context.algorithm_state.terminal_flush_count, 1)
        with self.assertRaisesRegex(RuntimeError, "already emitted terminal"):
            await context.stream_terminal(
                source,
                phase="final_answer",
                child_request_id="final-1",
            )


class ResponseContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonstream_aggregation_preserves_final_stop_reason(
        self,
    ) -> None:
        queue = asyncio.Queue()
        context = SimpleNamespace(orchestration_error=None)

        def chunk(
            text: str,
            token_ids: list[int],
            finish_reason=None,
            stop_reason=None,
        ):
            return SimpleNamespace(
                outputs=[SimpleNamespace(
                    text=text,
                    token_ids=token_ids,
                    finish_reason=finish_reason,
                    stop_reason=stop_reason,
                )],
                finished=finish_reason is not None,
            )

        await queue.put(chunk("thought", [1]))
        await queue.put(chunk("</think>", [2]))
        await queue.put(chunk("answer", [3]))
        await queue.put(chunk(
            "", [], finish_reason="length", stop_reason="max_tokens"))
        await queue.put(None)

        items = [
            item async for item in BudgetController._queue_as_async_gen(
                queue, context)
        ]
        output = items[0].outputs[0]
        self.assertEqual(output.text, "thought</think>answer")
        self.assertEqual(output.token_ids, [1, 2, 3])
        self.assertEqual(output.finish_reason, "length")
        self.assertEqual(output.stop_reason, "max_tokens")

    async def test_controller_rejects_multiple_candidates(self) -> None:
        class Serving:
            @staticmethod
            def create_error_response(message):
                return message

        response = await BudgetController().handle_request(
            Serving(),
            [],
            [],
            SimpleNamespace(n=2),
            "request",
            FakeTokenizer(),
            SimpleNamespace(),
        )
        self.assertEqual(response, "Think Budget currently supports only n=1")

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
