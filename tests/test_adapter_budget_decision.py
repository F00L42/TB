from __future__ import annotations

import unittest
from unittest.mock import patch

from think_budget_vllm_adapter.budget_controller import BudgetController
from think_budget_vllm_adapter.context import (
    AlgorithmConfig,
    DecisionBranch,
)
from think_budget_vllm_adapter.telemetry import TimingCollector


def scene() -> dict:
    return {
        "scene_type": "general",
        "typical_tokens": 1000,
        "scene_confidence": 0.5,
    }


def task(task_type: str = "coding", confidence: float = 1.0) -> dict:
    return {
        "task_type": task_type,
        "task_confidence": confidence,
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
        "think_budget_vllm_adapter.budget_controller.get_task_type_budget")
    @patch(
        "think_budget_vllm_adapter.budget_controller.is_known_task_type",
        return_value=True,
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_task_type",
        return_value=task(),
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_scene",
        return_value=scene(),
    )
    def test_fixed_budget_still_classifies_but_skips_dynamic_budget(
        self,
        classify_scene_mock,
        classify_task_mock,
        known_mock,
        task_budget_mock,
        difficulty_mock,
    ) -> None:
        config = AlgorithmConfig(
            fixed_budget_tokens=120,
            fixed_num_thinking=7,
            fixed_check_length=30,
            max_thinking_tokens=1000,
        )
        plan = BudgetController._decide(
            "prompt", config, self.timing, hard_token_limit=500)

        self.assertEqual(plan.decision_branch, DecisionBranch.FIXED)
        self.assertEqual(plan.budget.check_length, 30)
        self.assertEqual(plan.budget.min_think_budget, 120)
        self.assertEqual(plan.budget.num_thinking, 7)
        self.assertEqual(plan.hard_think_token_limit, 120)
        classify_scene_mock.assert_called_once()
        classify_task_mock.assert_called_once()
        task_budget_mock.assert_not_called()
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
        "think_budget_vllm_adapter.budget_controller.get_task_type_budget",
        return_value={
            "check_length": 100,
            "min_think_tokens": 50,
            "num_thinking": 3,
            "think_stride": 20,
            "prob_th": 0.90,
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.is_known_task_type",
        return_value=True,
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_task_type",
        return_value=task("statistics_aggregation"),
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_scene",
        return_value=scene(),
    )
    def test_known_statistics_task_averages_each_vector_field_half_up(
        self,
        _scene_mock,
        _task_mock,
        _known_mock,
        _task_budget_mock,
        difficulty_mock,
        _difficulty_budget_mock,
    ) -> None:
        plan = BudgetController._decide(
            "statistics", self.config, self.timing,
            hard_token_limit=1000)

        self.assertEqual(
            plan.decision_branch, DecisionBranch.TASK_DIFFICULTY)
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
            plan.source_by_field["num_thinking"]["rounding"],
            "half_up",
        )
        self.assertEqual(plan.confidence_threshold, 0.90)
        self.assertEqual(
            plan.source_by_field["confidence_threshold"]["source"],
            "task",
        )
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
            "history_snapshot_id": "general:general:2:90",
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.estimate_difficulty")
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_task_type",
        return_value=task("general", 0.0),
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_scene",
        return_value=scene(),
    )
    def test_unmatched_task_uses_only_history(
        self,
        _scene_mock,
        _task_mock,
        difficulty_mock,
        history_mock,
    ) -> None:
        config = AlgorithmConfig(
            check_length=40,
            min_think_budget=20,
            num_thinking=3,
            think_stride=10,
            history_stride_percentile=0.60,
            max_thinking_tokens=1000,
        )
        plan = BudgetController._decide(
            "unknown", config, self.timing,
            hard_token_limit=1000)

        self.assertEqual(plan.decision_branch, DecisionBranch.HISTORY)
        self.assertEqual(plan.budget.think_stride, 15)
        self.assertEqual(plan.confidence_threshold, 0.93)
        self.assertEqual(plan.percentile_step, 0.60)
        self.assertEqual(
            plan.history_snapshot_id, "general:general:2:90")
        difficulty_mock.assert_not_called()
        history_mock.assert_called_once()
        self.assertEqual(
            history_mock.call_args.kwargs["config"][
                "history_stride_percentile"
            ],
            0.60,
        )

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
        "think_budget_vllm_adapter.budget_controller.get_task_type_budget",
        return_value={
            "check_length": 100,
            "min_think_tokens": 50,
            "num_thinking": 3,
            "think_stride": 20,
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.is_known_task_type",
        return_value=True,
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_task_type",
        return_value=task(),
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_scene",
        return_value=scene(),
    )
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
        self.assertEqual(
            plan.source_by_field["think_stride"]["source"],
            "fixed_think_stride",
        )

    @patch(
        "think_budget_vllm_adapter.budget_controller.compute_budget_from_difficulty",
        return_value={
            "check_length": 222,
            "min_think_tokens": 88,
            "num_thinking": 6,
            "think_stride": 44,
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.estimate_difficulty",
        return_value={"difficulty": 0.5},
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.get_task_type_budget",
        return_value={
            "min_think_tokens": 50,
            "num_thinking": 4,
            "think_stride": 20,
        },
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.is_known_task_type",
        return_value=True,
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_task_type",
        return_value=task(),
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.classify_scene",
        return_value=scene(),
    )
    def test_missing_task_field_uses_difficulty_without_averaging_default(
        self,
        *_mocks,
    ) -> None:
        plan = BudgetController._decide(
            "code", self.config, self.timing, hard_token_limit=1000)
        self.assertEqual(plan.budget.check_length, 222)
        self.assertEqual(
            plan.source_by_field["check_length"]["source"], "difficulty")

    @patch.object(
        BudgetController,
        "_classify_task",
        return_value=(scene(), task(), "coding", True),
    )
    @patch(
        "think_budget_vllm_adapter.budget_controller.get_task_type_budget",
        return_value={
            "check_length": 100,
            "min_think_tokens": 50,
            "num_thinking": 3,
            "think_stride": 20,
            "prob_th": 0.90,
        },
    )
    def test_nondefault_global_confidence_overrides_task_threshold(
        self,
        _task_budget_mock,
        _classify_mock,
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

    @patch.object(
        BudgetController,
        "_classify_task",
        return_value=(scene(), task(), "coding", True),
    )
    def test_fixed_budget_derives_rounds_to_cover_total_budget(
        self,
        _classify_mock,
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


if __name__ == "__main__":
    unittest.main()
