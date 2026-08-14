from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from think_budget_vllm_adapter.context import (
    AlgorithmConfig,
    BudgetPlan,
    BudgetVector,
    DecisionBranch,
    PerformancePolicy,
)
from think_budget_vllm_adapter.deer_think_budget.context import (
    FinalAnswerReason,
    ReasoningState,
    RequestAlgorithmState,
    RequestStateContext,
)
from think_budget_vllm_adapter.deer_think_budget.state import (
    AsyncReasoningOrchestrator,
    BUDGET_EXHAUSTED_FINAL_PROMPT,
)
from think_budget_vllm_adapter.telemetry import TimingCollector


class FakeTokenizer:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return [ord(character) for character in text]

    def decode(self, token_ids, skip_special_tokens: bool = False) -> str:
        return "".join(
            chr(token_id) for token_id in token_ids
            if 0 <= token_id <= 0x10FFFF
        )


class FakeRequest:
    stream = True
    priority = 4
    max_tokens = 100
    max_completion_tokens = None

    def to_sampling_params(self, max_tokens, default_sampling_params):
        return SimpleNamespace(
            max_tokens=max_tokens,
            stop=[],
            logprobs=None,
            include_stop_str_in_output=False,
            output_kind=SimpleNamespace(name="DELTA"),
        )


def sampled(token_id: int, value: float, text: str) -> dict:
    return {
        token_id: SimpleNamespace(
            logprob=value,
            decoded_token=text,
            token_id=token_id,
        )
    }


def result(
    text: str,
    token_ids: list[int],
    *,
    confidence: float | None = None,
    finish_reason: str = "length",
    stop_reason=None,
):
    logprobs = None
    if confidence is not None:
        logprobs = [
            sampled(token_id, confidence, text[index:index + 1] or "v")
            for index, token_id in enumerate(token_ids)
        ]
    return SimpleNamespace(
        outputs=[SimpleNamespace(
            text=text,
            token_ids=token_ids,
            logprobs=logprobs,
            finish_reason=finish_reason,
            stop_reason=stop_reason,
        )],
        metrics=None,
        num_cached_tokens=None,
        finished=finish_reason is not None,
    )


class ScriptedEngine:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def generate(self, prompt, params, request_id, **kwargs):
        if not self.scripts:
            raise AssertionError(f"unexpected generate call {request_id}")
        script = self.scripts.pop(0)
        self.calls.append({
            "request_id": request_id,
            "max_tokens": params.max_tokens,
            "stop": list(params.stop or []),
            "include_stop_str_in_output": (
                params.include_stop_str_in_output),
            "prompt_token_ids": list(prompt["prompt_token_ids"]),
            "kwargs": kwargs,
        })

        async def generator():
            for item in script:
                yield item

        return generator()


def make_context(
    scripts,
    *,
    num_thinking: int = 2,
    min_think_budget: int = 1,
    hard_limit: int = 30,
) -> tuple[RequestStateContext, ScriptedEngine]:
    tokenizer = FakeTokenizer()
    engine = ScriptedEngine(scripts)
    serving = SimpleNamespace(
        engine_client=engine,
        default_sampling_params={},
        model_config=SimpleNamespace(max_model_len=1000),
    )
    plan = BudgetPlan(
        scene="code_generation",
        scene_matched=True,
        decision_branch=DecisionBranch.SCENE_DIFFICULTY,
        budget=BudgetVector(
            check_length=5,
            min_think_budget=min_think_budget,
            num_thinking=num_thinking,
            think_stride=3,
        ),
        confidence_threshold=0.9,
        percentile_step=0.75,
        source_by_field={},
        policy_version="think-budget-performance-v1",
        hard_think_token_limit=hard_limit,
    )
    context = RequestStateContext(
        request=FakeRequest(),
        tokenizer=tokenizer,
        config=AlgorithmConfig(
            value_min_bottom_k=1,
            max_internal_calls=20,
            final_answer_max_tokens=20,
        ),
        policy=PerformancePolicy(),
        serving_chat=serving,
        request_id="parent",
        engine_prompt={"prompt": "<think>", "prompt_token_ids": [60]},
        algorithm_state=RequestAlgorithmState(budget_plan=plan),
        prompt_text="<think>",
        timing=TimingCollector(
            emit_phase_metrics=False,
            policy_version="think-budget-performance-v1",
        ),
    )
    return context, engine


async def run_and_drain(context: RequestStateContext):
    await AsyncReasoningOrchestrator(context).run()
    visible = []
    while True:
        value = await context.result_queue.get()
        if value is None:
            break
        visible.append(value.outputs[0].text)
    return visible


async def run_and_drain_outputs(context: RequestStateContext):
    await AsyncReasoningOrchestrator(context).run()
    outputs = []
    while True:
        value = await context.result_queue.get()
        if value is None:
            break
        outputs.append(value.outputs[0])
    return outputs


def call_phases(engine: ScriptedEngine) -> list[str]:
    known = (
        "initial_think",
        "confidence_probe",
        "iterative_think",
        "final_answer",
    )
    return [
        next(
            phase for phase in known
            if f"_{phase}_" in call["request_id"]
        )
        for call in engine.calls
    ]


class StateMachineGoldenTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_body_and_terminal_are_separate(self):
        context, _ = make_context([
            [result("thought", [10])],
            [result("trial", [20], confidence=-0.01)],
            [result(
                "answer", [30], finish_reason="stop",
                stop_reason="eos")],
        ])

        outputs = await run_and_drain_outputs(context)
        terminals = [
            output for output in outputs
            if output.finish_reason is not None
        ]
        answer = next(output for output in outputs if output.text == "answer")

        self.assertIsNone(answer.finish_reason)
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0].text, "")
        self.assertEqual(terminals[0].token_ids, [])
        self.assertEqual(terminals[0].finish_reason, "stop")
        self.assertEqual(terminals[0].stop_reason, "eos")

    async def test_first_probe_accepts_then_fresh_final(self):
        context, engine = make_context([
            [result("thought", [10])],
            [result("trial", [20], confidence=-0.01)],
            [result(
                "answer", [30], finish_reason="stop",
                stop_reason="eos")],
        ])
        recorded_phases = []
        original_record_phase = context.timing.record_phase

        def capture_phase(**kwargs):
            recorded_phases.append(kwargs)
            return original_record_phase(**kwargs)

        context.timing.record_phase = capture_phase
        visible = await run_and_drain(context)

        self.assertEqual(
            [call["request_id"].split("_")[-2] for call in engine.calls],
            ["think", "probe", "answer"],
        )
        self.assertEqual(engine.calls[0]["max_tokens"], 5)
        self.assertEqual(engine.calls[1]["max_tokens"], 50)
        self.assertNotIn(20, engine.calls[2]["prompt_token_ids"])
        self.assertEqual(visible.count("</think>"), 1)
        self.assertNotIn("trial", visible)
        self.assertNotIn(
            "Considering the limited time by the user",
            context.prompt_text,
        )
        self.assertTrue(context.prompt_text.endswith("</think>"))
        close_tokens = context.tokenizer.encode(
            "</think>", add_special_tokens=False)
        self.assertEqual(
            engine.calls[-1]["prompt_token_ids"][-len(close_tokens):],
            close_tokens,
        )
        self.assertEqual(
            context.algorithm_state.final_answer_reason,
            FinalAnswerReason.CONFIDENCE_ACCEPTED,
        )
        self.assertEqual(
            context.algorithm_state.reasoning_state,
            ReasoningState.CLOSED,
        )
        self.assertEqual(
            context.algorithm_state.completion_status, "success")
        self.assertEqual(context.prompt_text.count("</think>"), 1)
        probe = next(
            phase for phase in recorded_phases
            if phase["phase"] == "confidence_probe"
        )
        self.assertEqual(probe["confidence_method"], "value_min")
        self.assertAlmostEqual(probe["average_confidence"], 0.99005, places=5)
        self.assertEqual(probe["value_min_raw"], -0.01)
        self.assertEqual(probe["pair_count"], 1)
        self.assertEqual(probe["value_pair_count"], 1)
        self.assertEqual(probe["accept_reason"], "value_min")
        final = next(
            phase for phase in recorded_phases
            if phase["phase"] == "final_answer"
        )
        self.assertEqual(
            final["final_answer_reason"], "confidence_accepted")

    async def test_second_probe_accepts_after_one_stride(self):
        context, engine = make_context([
            [result("check", [10])],
            [result("bad", [20], confidence=-5.0)],
            [result("stride", [11])],
            [result("good", [21], confidence=-0.01)],
            [result("answer", [30], finish_reason="stop")],
        ])
        await run_and_drain(context)

        phases = call_phases(engine)
        self.assertEqual(
            phases,
            [
                "initial_think",
                "confidence_probe",
                "iterative_think",
                "confidence_probe",
                "final_answer",
            ],
        )
        self.assertEqual(context.algorithm_state.probe_round, 2)
        self.assertNotIn(20, context.engine_prompt["prompt_token_ids"])
        self.assertNotIn(21, context.engine_prompt["prompt_token_ids"])

    async def test_all_failed_probes_still_generate_last_stride(self):
        context, engine = make_context([
            [result("check", [10])],
            [result("bad1", [20], confidence=-5.0)],
            [result("stride1", [11])],
            [result("bad2", [21], confidence=-5.0)],
            [result("last-stride", [12])],
            [result("answer", [30], finish_reason="stop")],
        ])
        await run_and_drain(context)

        phases = call_phases(engine)
        self.assertEqual(
            phases[-2:], ["iterative_think", "final_answer"])
        self.assertIn(12, engine.calls[-1]["prompt_token_ids"])
        self.assertEqual(
            context.algorithm_state.final_answer_reason,
            FinalAnswerReason.BUDGET_EXHAUSTED,
        )
        self.assertTrue(
            context.prompt_text.endswith(BUDGET_EXHAUSTED_FINAL_PROMPT))
        expected_suffix = context.tokenizer.encode(
            BUDGET_EXHAUSTED_FINAL_PROMPT,
            add_special_tokens=False,
        )
        self.assertEqual(
            engine.calls[-1]["prompt_token_ids"][-len(expected_suffix):],
            expected_suffix,
        )

    async def test_hard_think_budget_exhaustion_uses_limited_time_prompt(
        self,
    ):
        expected_prompt = (
            "Considering the limited time by the user, I have to give the "
            "solution based on the thinking directly now.\n</think>\n\n"
        )
        self.assertEqual(BUDGET_EXHAUSTED_FINAL_PROMPT, expected_prompt)
        context, engine = make_context(
            [
                [result("check", [10])],
                [result("answer", [30], finish_reason="stop")],
            ],
            hard_limit=1,
        )
        visible = await run_and_drain(context)

        self.assertEqual(
            call_phases(engine), ["initial_think", "final_answer"])
        self.assertEqual(
            context.algorithm_state.final_answer_reason,
            FinalAnswerReason.BUDGET_EXHAUSTED,
        )
        self.assertTrue(
            context.prompt_text.endswith(expected_prompt))
        self.assertEqual(visible.count("</think>"), 1)
        self.assertNotIn(
            "Considering the limited time by the user",
            "".join(visible),
        )

    async def test_model_think_end_enters_free_final_even_before_minimum(self):
        boundary = "</think>"
        context, engine = make_context(
            [
                [result(
                    boundary,
                    [ord(character) for character in boundary],
                    finish_reason="stop",
                    stop_reason="</think>")],
                [result("answer", [30], finish_reason="stop")],
            ],
            min_think_budget=2,
        )
        visible = await run_and_drain(context)

        phases = call_phases(engine)
        self.assertEqual(
            phases,
            ["initial_think", "final_answer"],
        )
        self.assertEqual(context.prompt_text.count("<think>"), 1)
        self.assertEqual(context.prompt_text.count("</think>"), 1)
        self.assertEqual(visible.count("</think>"), 1)
        self.assertEqual(
            context.algorithm_state.final_answer_reason,
            FinalAnswerReason.MODEL_THINK_END,
        )
        self.assertIn("<tool_call>", engine.calls[0]["stop"])
        self.assertIn("</think>", engine.calls[0]["stop"])
        self.assertTrue(engine.calls[0]["include_stop_str_in_output"])

    async def test_model_tool_boundary_continues_without_synthetic_think_end(
        self,
    ):
        first = "thought<tool_call>\n<f"
        context, engine = make_context([
            [result(
                first,
                [ord(character) for character in first],
                finish_reason="stop",
                stop_reason="<tool_call>",
            )],
            [result(
                "<function=bash></function></tool_call>",
                [31, 32],
                finish_reason="stop",
                stop_reason="eos",
            )],
        ])

        visible = await run_and_drain(context)

        self.assertEqual(
            call_phases(engine), ["initial_think", "final_answer"])
        self.assertEqual("".join(visible).count("<tool_call>"), 1)
        self.assertNotIn("</think>", "".join(visible))
        self.assertEqual(context.prompt_text.count("<tool_call>"), 1)
        self.assertEqual(
            context.algorithm_state.final_answer_reason,
            FinalAnswerReason.MODEL_TOOL_CALL,
        )
        self.assertEqual(context.algorithm_state.probe_round, 0)
        self.assertEqual(context.algorithm_state.terminal_flush_count, 1)
        self.assertTrue(engine.calls[-1]["kwargs"]["reasoning_ended"])
        self.assertEqual(
            engine.calls[-1]["prompt_token_ids"][-len(first):],
            [ord(character) for character in first],
        )
        ledger = context.timing.snapshot()["ledger"]
        self.assertEqual(ledger["visible_think_tokens"], len("thought"))
        self.assertEqual(
            ledger["final_answer_tokens"],
            len("<tool_call>\n<f") + 2,
        )

    async def test_empty_logprobs_fail_open_after_first_probe(self):
        context, engine = make_context([
            [result("check", [10])],
            [result("trial", [20], confidence=None)],
            [result("answer", [30], finish_reason="stop")],
        ])
        await run_and_drain(context)

        self.assertEqual(len(engine.calls), 3)
        self.assertIsNone(context.algorithm_state.last_confidence)


if __name__ == "__main__":
    unittest.main()
