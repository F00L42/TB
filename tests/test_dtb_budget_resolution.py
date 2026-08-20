from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from dtb.budgeting.budget_decision import DecisionBranch
from dtb.budgeting.budget_resolver import DtbBudgetResolver
from dtb.config.loader import load_dtb_service_config
from dtb.config.schema import DtbAlgorithmConfig
from dtb.integration.vllm.request_adapter import visible_user_text


def route(scene: str = "sample", matched: bool = True) -> dict:
    return {
        "scene": scene if matched else "general",
        "scene_matched": matched,
        "candidate_scene": scene,
        "typical_tokens": 1000,
        "top_score": 6.0 if matched else 2.0,
        "second_score": 0.0,
        "score_margin": 6.0 if matched else 2.0,
        "reject_reason": None if matched else "below_min_score",
    }


class DtbBudgetResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        service = load_dtb_service_config()
        scenes = dict(service.scenes)
        scenes["sample"] = {
            "check_length": 100,
            "min_think_tokens": 50,
            "num_thinking": 3,
            "think_stride": 20,
            "max_think_tokens": 1000,
            "prob_th": 0.90,
        }
        self.resolver = DtbBudgetResolver(replace(service, scenes=scenes))

    def test_fixed_budget_skips_dynamic_modules(self) -> None:
        config = DtbAlgorithmConfig(
            fixed_budget_tokens=120,
            fixed_num_thinking=7,
            fixed_check_length=30,
            max_thinking_tokens=1000,
        )
        trace: dict = {}
        with patch.object(
            self.resolver, "_match_scene", return_value=route()
        ), patch(
            "dtb.budgeting.budget_resolver.estimate_difficulty"
        ) as difficulty:
            decision = self.resolver.resolve(
                "prompt", config, None,
                hard_token_limit=500,
                decision_trace=trace,
            )
        self.assertEqual(decision.decision_branch, DecisionBranch.FIXED)
        self.assertEqual(decision.budget.check_length, 30)
        self.assertEqual(decision.budget.min_think_budget, 120)
        self.assertEqual(decision.budget.num_thinking, 7)
        self.assertEqual(decision.hard_think_token_limit, 120)
        self.assertEqual(trace["route"]["scene"], "sample")
        difficulty.assert_not_called()

    def test_scene_and_difficulty_are_averaged_half_up(self) -> None:
        config = DtbAlgorithmConfig(
            max_thinking_tokens=1000,
            enable_difficulty_estimator=True,
        )
        with patch.object(
            self.resolver, "_match_scene", return_value=route()
        ), patch(
            "dtb.budgeting.budget_resolver.estimate_difficulty",
            return_value={"difficulty": 0.5},
        ), patch(
            "dtb.budgeting.budget_resolver.compute_budget_from_difficulty",
            return_value={
                "check_length": 200,
                "min_think_tokens": 100,
                "num_thinking": 4,
                "think_stride": 40,
            },
        ):
            decision = self.resolver.resolve(
                "statistics", config, None, hard_token_limit=1000
            )
        self.assertEqual(
            decision.budget.to_dict(),
            {
                "check_length": 150,
                "min_think_budget": 75,
                "num_thinking": 4,
                "think_stride": 30,
            },
        )
        self.assertEqual(
            decision.source_by_field["num_thinking"]["rounding"], "half_up"
        )
        self.assertEqual(decision.confidence_threshold, 0.90)

    def test_rejected_scene_uses_history_only_when_enabled(self) -> None:
        config = DtbAlgorithmConfig(
            history_stride_percentile=0.60,
            max_thinking_tokens=1000,
            enable_history_budget=True,
        )
        history = {
            "check_length": 90,
            "min_think_tokens": 45,
            "num_thinking": 6,
            "think_stride": 15,
            "prob_th": 0.93,
            "budget_mode": "warm_start",
            "history_snapshot_id": "general:2:90",
        }
        with patch.object(
            self.resolver,
            "_match_scene",
            return_value=route("planning", matched=False),
        ), patch(
            "dtb.history.budget_policy.compute_history_budget",
            return_value=history,
        ) as compute_history:
            decision = self.resolver.resolve(
                "unknown", config, None, hard_token_limit=1000
            )
        self.assertEqual(decision.decision_branch, DecisionBranch.HISTORY)
        self.assertEqual(decision.budget.think_stride, 15)
        self.assertEqual(decision.percentile_step, 0.60)
        compute_history.assert_called_once()

    def test_rejected_scene_defaults_when_history_is_disabled(self) -> None:
        config = DtbAlgorithmConfig(max_thinking_tokens=1000)
        trace: dict = {}
        with patch.object(
            self.resolver,
            "_match_scene",
            return_value=route("planning", matched=False),
        ):
            decision = self.resolver.resolve(
                "unknown", config, None,
                hard_token_limit=1000,
                decision_trace=trace,
            )
        self.assertEqual(decision.budget, config.default_budget)
        self.assertEqual(trace["history"]["budget_mode"], "history_disabled")

    def test_fixed_override_and_hard_limit_apply_last(self) -> None:
        config = DtbAlgorithmConfig(
            fixed_think_stride=17,
            fixed_prob_th=0.88,
            max_thinking_tokens=1000,
        )
        with patch.object(
            self.resolver, "_match_scene", return_value=route()
        ):
            decision = self.resolver.resolve(
                "code", config, None, hard_token_limit=80
            )
        self.assertEqual(decision.budget.check_length, 80)
        self.assertEqual(decision.budget.think_stride, 17)
        self.assertEqual(decision.confidence_threshold, 0.88)
        self.assertEqual(
            decision.source_by_field["check_length"]["clamped_from"], 100
        )

    def test_visible_user_text_uses_latest_user_message(self) -> None:
        text, source = visible_user_text(
            [
                {"role": "system", "content": "classify all requests"},
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "answer"},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "排查告警"}],
                },
            ],
            "fallback",
        )
        self.assertEqual(text, "排查告警")
        self.assertEqual(source, "latest_user_message")


if __name__ == "__main__":
    unittest.main()
