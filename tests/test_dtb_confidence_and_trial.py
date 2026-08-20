from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from dtb.reasoning.confidence import (
    cal_confidence,
)
from dtb.reasoning.confidence_gates import (
    assess_trial_confidence,
)
from dtb.reasoning.request_state import (
    TrialObservation,
)
from dtb.reasoning.generation_states import _BaseState
from dtb.reasoning.token_filter import (
    is_structural_token,
)


def logprob(token_id: int, value: float, text: str) -> dict:
    return {
        token_id: SimpleNamespace(
            logprob=value,
            decoded_token=text,
            token_id=token_id,
        )
    }


def output(
    text: str,
    token_ids: list[int],
    logprobs=None,
    *,
    finish_reason=None,
    stop_reason=None,
):
    return SimpleNamespace(outputs=[SimpleNamespace(
        text=text,
        token_ids=token_ids,
        logprobs=logprobs,
        finish_reason=finish_reason,
        stop_reason=stop_reason,
    )])


async def chunks(*values):
    for value in values:
        yield value


class TrialCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_delta_collector_keeps_full_text_and_all_token_count(self):
        first = output("A", [1], [logprob(1, -0.1, "A")])
        second = output(
            "BC",
            [2, 3],
            [logprob(2, -0.2, "B")],
            finish_reason="length",
        )
        observation = await _BaseState.consume_trial_output(
            chunks(first, second), output_kind="DELTA")

        self.assertEqual(observation.full_text, "ABC")
        self.assertEqual(observation.all_token_ids, [1, 2, 3])
        self.assertEqual(observation.generated_tokens, 3)
        self.assertEqual(len(observation.sample_logprobs), 3)
        self.assertIsNone(observation.sample_logprobs[2])
        self.assertEqual(observation.finish_reason, "length")

    async def test_cumulative_collector_replaces_previous_snapshot(self):
        observation = await _BaseState.consume_trial_output(
            chunks(
                output("A", [1], [logprob(1, -0.1, "A")]),
                output(
                    "AB",
                    [1, 2],
                    [
                        logprob(1, -0.1, "A"),
                        logprob(2, -0.2, "B"),
                    ],
                    finish_reason="stop",
                ),
            ),
            output_kind="CUMULATIVE",
        )
        self.assertEqual(observation.full_text, "AB")
        self.assertEqual(observation.all_token_ids, [1, 2])
        self.assertEqual(observation.generated_tokens, 2)

    async def test_final_only_and_delta_produce_same_confidence_input(self):
        delta = await _BaseState.consume_trial_output(
            chunks(
                output("A", [1], [logprob(1, -0.1, "A")]),
                output("B", [2], [logprob(2, -0.2, "B")]),
            ),
            output_kind="DELTA",
        )
        final = await _BaseState.consume_trial_output(
            chunks(output(
                "AB",
                [1, 2],
                [
                    logprob(1, -0.1, "A"),
                    logprob(2, -0.2, "B"),
                ],
            )),
            output_kind="FINAL_ONLY",
        )
        left = assess_trial_confidence(
            delta,
            enable_value_min_confidence=True,
            value_min_bottom_k=2,
        )
        right = assess_trial_confidence(
            final,
            enable_value_min_confidence=True,
            value_min_bottom_k=2,
        )
        self.assertEqual(delta.full_text, final.full_text)
        self.assertAlmostEqual(left.score, right.score)
        self.assertEqual(left.method, "value_min")


class ConfidenceTests(unittest.TestCase):
    def test_reference_none_denominator_is_preserved(self):
        self.assertAlmostEqual(
            cal_confidence([-1.0, None]),
            math.exp(-1.0) / 2,
        )

    def test_value_min_degrades_to_average_for_incomplete_bottom_k(self):
        observation = TrialObservation(
            full_text="A",
            all_token_ids=[1, 2],
            sample_logprobs=[logprob(1, -0.1, "A"), None],
            sampled_token_ids=[1, 2],
            generated_tokens=2,
        )
        result = assess_trial_confidence(
            observation,
            enable_value_min_confidence=True,
            value_min_bottom_k=5,
        )
        self.assertTrue(result.available)
        self.assertEqual(result.method, "average_fallback")
        self.assertAlmostEqual(result.score, math.exp(-0.1) / 2)
        self.assertAlmostEqual(result.value_min_raw, -0.1)

    def test_disabled_value_min_still_reports_counterfactual_raw(self):
        observation = TrialObservation(
            full_text="AB",
            all_token_ids=[1, 2],
            sample_logprobs=[
                logprob(1, -0.1, "A"),
                logprob(2, -0.5, "B"),
            ],
            sampled_token_ids=[1, 2],
            generated_tokens=2,
        )
        result = assess_trial_confidence(
            observation,
            enable_value_min_confidence=False,
            value_min_bottom_k=2,
        )

        self.assertEqual(result.method, "average_fallback")
        self.assertAlmostEqual(
            result.score,
            (math.exp(-0.1) + math.exp(-0.5)) / 2,
        )
        self.assertAlmostEqual(result.value_min_raw, -0.3)
        self.assertEqual(result.value_pair_count, 2)

    def test_no_logprobs_is_explicit_fail_open_signal(self):
        result = assess_trial_confidence(
            TrialObservation(
                full_text="answer",
                all_token_ids=[1],
                sample_logprobs=[None],
                sampled_token_ids=[1],
                generated_tokens=1,
            ),
            enable_value_min_confidence=True,
            value_min_bottom_k=5,
        )
        self.assertFalse(result.available)
        self.assertIsNone(result.score)
        self.assertEqual(
            result.method, "confidence_unavailable_fail_open")

    def test_reference_structural_tokens_and_business_keys(self):
        for token in ("=", "+", "{", "faultNumber", "<|im_start|>", " "):
            with self.subTest(token=token):
                self.assertTrue(is_structural_token(token))
        self.assertFalse(is_structural_token("true"))
        self.assertFalse(is_structural_token("</think>"))


if __name__ == "__main__":
    unittest.main()
