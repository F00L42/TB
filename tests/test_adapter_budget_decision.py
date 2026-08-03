from __future__ import annotations

import unittest
from unittest.mock import patch

from think_budget_vllm_adapter.budget_controller import BudgetController
from think_budget_vllm_adapter.context import AlgorithmConfig, DecisionBranch
from think_budget_vllm_adapter.telemetry import TimingCollector


def route(
    scene: str = "code_generation",
    matched: bool = True,
) -> dict:
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


class BudgetDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timing = TimingCollector(emit_phase_metrics=False)
        self.config = AlgorithmConfig(
            check_length=40,
            min_think_budget=20,
            num_thinking=3,
            think_stride=10,
            max_thinking_tokens=1000,
        )

    @patch(
        "think_budget_vllm_adapter.budget_controller.estimate_difficulty")
    @patch(
        "think_budget_vllm_adapter.budget_controller.get_scene_budget")
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_scene",
        return_value=route(),
    )
    def test_fixed_budget_collects_route_but_skips_dynamic_budget(
        self,
        classifier_mock,
        scene_budget_mock,
        difficulty_mock,
    ) -> None:
        config = AlgorithmConfig(
            fixed_budget_tokens=120,
            fixed_num_thinking=7,
            fixed_check_length=30,
            max_thinking_tokens=1000,
        )
        trace: dict = {}
        plan = BudgetController._decide(
            "prompt",
            config,
            self.timing,
            hard_token_limit=500,
            decision_trace=trace,
        )

        self.assertEqual(plan.decision_branch, DecisionBranch.FIXED)
        self.assertEqual(plan.budget.check_length, 30)
        self.assertEqual(plan.budget.min_think_budget, 120)
        self.assertEqual(plan.budget.num_thinking, 7)
        self.assertEqual(plan.hard_think_token_limit, 120)
        self.assertEqual(trace["route"]["scene"], "code_generation")
        classifier_mock.assert_called_once()
        scene_budget_mock.assert_not_called()
        difficulty_mock.assert_not_called()

    @patch(
        "think_budget_vllm_adapter.budget_controller.compute_budget_from_difficulty",
        return_value={
            "check_length": 200,
            "min_think_tokens": 100,
            "num_thinking": 4,
            "think_stride": 40,
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.estimate_difficulty",
        return_value={"difficulty": 0.5},
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.get_scene_budget",
        return_value={
            "check_length": 100,
            "min_think_tokens": 50,
            "num_thinking": 3,
            "think_stride": 20,
            "prob_th": 0.90,
        },
    )
    @patch.object(BudgetController, "_match_scene", return_value=route())
    def test_matched_scene_averages_each_vector_field_half_up(
        self,
        _route_mock,
        _scene_budget_mock,
        difficulty_mock,
        _difficulty_budget_mock,
    ) -> None:
        plan = BudgetController._decide(
            "statistics", self.config, self.timing, hard_token_limit=1000)

        self.assertEqual(
            plan.decision_branch, DecisionBranch.SCENE_DIFFICULTY)
        self.assertEqual(
            plan.budget.to_dict(),
            {
                "check_length": 150,
                "min_think_budget": 75,
                "num_thinking": 4,
                "think_stride": 30,
            },
        )
        self.assertEqual(
            plan.source_by_field["num_thinking"]["rounding"], "half_up")
        self.assertEqual(plan.confidence_threshold, 0.90)
        self.assertEqual(
            plan.source_by_field["confidence_threshold"]["source"], "scene")
        difficulty_mock.assert_called_once()

    @patch(
        "think_budget_vllm_adapter.history.history_budget.compute_history_budget",
        return_value={
            "check_length": 90,
            "min_think_tokens": 45,
            "num_thinking": 6,
            "think_stride": 15,
            "prob_th": 0.93,
            "budget_mode": "warm_start",
            "history_snapshot_id": "general:2:90",
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.estimate_difficulty")
    @patch.object(
        BudgetController,
        "_match_scene",
        return_value=route("planning", matched=False),
    )
    def test_rejected_scene_uses_only_history(
        self,
        _route_mock,
        difficulty_mock,
        history_mock,
    ) -> None:
        config = AlgorithmConfig(
            history_stride_percentile=0.60,
            max_thinking_tokens=1000,
        )
        trace: dict = {}
        plan = BudgetController._decide(
            "unknown",
            config,
            self.timing,
            hard_token_limit=1000,
            decision_trace=trace,
        )

        self.assertEqual(plan.decision_branch, DecisionBranch.HISTORY)
        self.assertEqual(plan.scene, "general")
        self.assertFalse(plan.scene_matched)
        self.assertEqual(plan.budget.think_stride, 15)
        self.assertEqual(plan.confidence_threshold, 0.93)
        self.assertEqual(plan.percentile_step, 0.60)
        self.assertEqual(
            trace["history"]["history_snapshot_id"], "general:2:90")
        difficulty_mock.assert_not_called()
        history_mock.assert_called_once()
        self.assertEqual(history_mock.call_args.kwargs["scene"], "general")

    @patch(
        "think_budget_vllm_adapter.budget_controller.compute_budget_from_difficulty",
        return_value={
            "check_length": 200,
            "min_think_tokens": 100,
            "num_thinking": 4,
            "think_stride": 40,
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.estimate_difficulty",
        return_value={"difficulty": 0.5},
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.get_scene_budget",
        return_value={
            "check_length": 100,
            "min_think_tokens": 50,
            "num_thinking": 3,
            "think_stride": 20,
        },
    )
    @patch.object(BudgetController, "_match_scene", return_value=route())
    def test_fixed_subfield_and_hard_limit_apply_after_dynamic_merge(
        self,
        *_mocks,
    ) -> None:
        config = AlgorithmConfig(
            fixed_think_stride=17,
            fixed_prob_th=0.88,
            max_thinking_tokens=1000,
        )
        plan = BudgetController._decide(
            "code", config, self.timing, hard_token_limit=80)

        self.assertEqual(plan.budget.check_length, 80)
        self.assertEqual(plan.budget.think_stride, 17)
        self.assertEqual(plan.confidence_threshold, 0.88)
        self.assertEqual(
            plan.source_by_field["check_length"]["clamped_from"], 150)

    @patch(
        "think_budget_vllm_adapter.budget_controller.get_scene_budget",
        return_value={
            "check_length": 100,
            "min_think_tokens": 50,
            "num_thinking": 3,
            "think_stride": 20,
            "prob_th": 0.90,
        },
    )
    @patch.object(BudgetController, "_match_scene", return_value=route())
    def test_nondefault_global_confidence_overrides_scene_threshold(
        self,
        _route_mock,
        _scene_budget_mock,
    ) -> None:
        config = AlgorithmConfig(
            confidence_threshold=0.92,
            enable_difficulty_estimator=False,
        )
        plan = BudgetController._decide(
            "code", config, self.timing, hard_token_limit=1000)
        self.assertEqual(plan.confidence_threshold, 0.92)
        self.assertEqual(
            plan.source_by_field["confidence_threshold"]["source"],
            "confidence_threshold_override",
        )

    @patch.object(BudgetController, "_match_scene", return_value=route())
    def test_fixed_budget_derives_rounds_to_cover_total_budget(
        self,
        _route_mock,
    ) -> None:
        config = AlgorithmConfig(
            check_length=200,
            min_think_budget=100,
            num_thinking=10,
            think_stride=100,
            fixed_budget_tokens=1000,
            max_thinking_tokens=2000,
        )
        plan = BudgetController._decide(
            "code", config, self.timing, hard_token_limit=2000)
        self.assertEqual(plan.budget.check_length, 200)
        self.assertEqual(plan.budget.min_think_budget, 1000)
        self.assertEqual(plan.budget.num_thinking, 8)
        self.assertEqual(plan.budget.think_stride, 100)

    def test_visible_request_text_uses_latest_user_message(self) -> None:
        text, source = BudgetController._visible_request_text(
            [
                {"role": "system", "content": "classify all requests"},
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": [{"type": "text", "text": "排查告警"}]},
            ],
            "fallback",
        )
        self.assertEqual(text, "排查告警")
        self.assertEqual(source, "latest_user_message")


if __name__ == "__main__":
    unittest.main()
