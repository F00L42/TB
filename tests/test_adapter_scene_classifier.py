from __future__ import annotations

import csv
import unittest
from pathlib import Path

from think_budget_vllm_adapter.optimizations.scene_classifier import (
    classify_scene,
)


class SceneClassifierTests(unittest.TestCase):
    def test_ascii_keyword_uses_word_boundaries(self) -> None:
        result = classify_scene(
            "Explain how a searchable index is organized.")
        self.assertNotIn(
            "search",
            result["strong_matches"].get("retrieval", []),
        )

    def test_rejection_gate_rejects_ambiguous_evidence(self) -> None:
        result = classify_scene("请规划并检索信息")
        self.assertEqual(result["scene"], "general")
        self.assertFalse(result["scene_matched"])
        self.assertEqual(result["reject_reason"], "below_min_margin")
        self.assertGreater(result["top_score"], 0)

    def test_explicit_scene_has_no_separate_semantic_type(self) -> None:
        result = classify_scene(
            "arbitrary text", scene_override="planning")
        self.assertEqual(result["scene"], "planning")
        self.assertTrue(result["scene_matched"])
        self.assertNotIn("task_type", result)
        self.assertNotIn("confidence", result)

    def test_sampled_product_questions_do_not_repeat_old_classification_error(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        path = next(
            (root / "docs" / "log" / "test_results").glob("*07-27*.csv"))
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = {row["QA_ID"]: row for row in csv.DictReader(stream)}

        sampled = {
            qa_id: classify_scene(rows[qa_id]["问题"])
            for qa_id in (
                "TC0000000201",
                "TC0000000204",
                "TC0000000212",
                "TC0000000216",
            )
        }
        self.assertEqual(
            sampled["TC0000000201"]["scene"], "fault_diagnosis")
        self.assertEqual(
            sampled["TC0000000212"]["scene"], "planning")
        self.assertEqual(
            sampled["TC0000000216"]["scene"], "fault_diagnosis")
        self.assertEqual(
            sampled["TC0000000204"]["reject_reason"], "below_min_margin")
        self.assertTrue(all(
            result["scene"] != "classification"
            for result in sampled.values()
        ))


if __name__ == "__main__":
    unittest.main()
