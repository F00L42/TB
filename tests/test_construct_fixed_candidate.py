from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.construct_fixed_candidate import (
    FixedCandidate,
    candidate_block,
    construct_candidate,
    round_to_quantum,
    scene_key,
    split_response,
    upsert_candidate,
)


class ConstructFixedCandidateTests(unittest.TestCase):
    def test_constructs_documented_candidate(self):
        candidate = construct_candidate(4668.706185410295)

        self.assertTrue(candidate.enable_think_budget)
        self.assertEqual(candidate.fixed_prob_th, 0.93)
        self.assertEqual(candidate.fixed_check_length, 950)
        self.assertEqual(candidate.fixed_min_think_tokens, 950)
        self.assertEqual(candidate.fixed_think_stride, 450)
        self.assertEqual(candidate.fixed_budget_tokens, 2350)
        self.assertEqual(candidate.fixed_num_thinking, 4)

    def test_round_to_quantum_uses_half_up(self):
        self.assertEqual(round_to_quantum(925, 50), 950)
        self.assertEqual(round_to_quantum(924.999, 50), 900)

    def test_split_response_accepts_optional_opening_marker(self):
        self.assertEqual(
            split_response("analysis</think>answer"),
            ("analysis", "answer"),
        )
        self.assertEqual(
            split_response("<think>analysis</think>answer"),
            ("analysis", "answer"),
        )

    def test_split_response_rejects_unseparated_completion(self):
        with self.assertRaises(ValueError):
            split_response("analysis and answer")

    def test_scene_key_reuses_original_test_set_name(self):
        path = Path(
            "vanilla_NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_"
            "规划模块_101条_2026-08-03_13-07-41.csv"
        )

        self.assertEqual(
            scene_key(path),
            "NetQAD_中文测评集v4.0_复杂任务规划_云核E2E_规划模块_101条",
        )

    def test_upsert_supports_multiple_labeled_candidates(self):
        fixed_v1 = construct_candidate(4668.706185410295)
        fixed_v2 = FixedCandidate(
            enable_think_budget=True,
            fixed_prob_th=0.90,
            fixed_check_length=800,
            fixed_think_stride=400,
            fixed_num_thinking=3,
            fixed_min_think_tokens=800,
            fixed_budget_tokens=1800,
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scene.txt"
            upsert_candidate(path, "fixed_v1", fixed_v1)
            upsert_candidate(path, "budget_0.35", fixed_v2)
            upsert_candidate(path, "fixed_v1", fixed_v2)
            content = path.read_text(encoding="utf-8")

        self.assertEqual(content.count("===== fixed_v1 ====="), 1)
        self.assertEqual(content.count("===== budget_0.35 ====="), 1)
        self.assertIn("fixed_budget_tokens = 1800", content)
        self.assertEqual(
            candidate_block("fixed_v1", fixed_v2).splitlines()[0],
            "===== fixed_v1 =====",
        )


if __name__ == "__main__":
    unittest.main()
