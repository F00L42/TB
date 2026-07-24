from __future__ import annotations

import unittest
from dataclasses import fields

from think_budget_vllm_adapter.config.unified_config import (
    _get_default_config,
    get_performance_policy_config,
    get_runtime_config,
)
from think_budget_vllm_adapter.context import (
    AlgorithmConfig,
    PerformancePolicy,
)


class AlgorithmConfigTests(unittest.TestCase):
    def test_algorithm_config_has_no_deer_projection_fields(self):
        names = {item.name for item in fields(AlgorithmConfig)}
        self.assertTrue({
            "check_length",
            "min_think_budget",
            "num_thinking",
            "think_stride",
        }.issubset(names))
        self.assertTrue({
            "initial_thought_limit",
            "think_ratio",
            "token_budget",
            "trial_answers",
            "is_code",
        }.isdisjoint(names))

    def test_performance_policy_is_not_tunable(self):
        with self.assertRaises(ValueError):
            PerformancePolicy(trial_answer_length=51)
        with self.assertRaises(ValueError):
            PerformancePolicy(retain_answer_list=True)
        with self.assertRaises(ValueError):
            PerformancePolicy(enable_soft_truncation=True)

    def test_builtin_runtime_constructs_algorithm_config(self):
        raw = _get_default_config()
        runtime = get_runtime_config(raw)
        config = AlgorithmConfig(
            budget_enabled=runtime["BUDGET_ENABLED"],
            check_length=runtime["check_length"],
            min_think_budget=runtime["min_think_budget"],
            num_thinking=runtime["num_thinking"],
            think_stride=runtime["think_stride"],
        )
        self.assertTrue(config.budget_enabled)
        self.assertEqual(config.check_length, 200)

    def test_policy_manifest_matches_code_invariants(self):
        policy = get_performance_policy_config(_get_default_config())
        self.assertEqual(policy["trial_answer_length"], 50)
        self.assertEqual(policy["confidence_missing_input"], "fail_open")
        self.assertFalse(policy["retain_answer_list"])


if __name__ == "__main__":
    unittest.main()
