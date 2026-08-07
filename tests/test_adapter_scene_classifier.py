from __future__ import annotations

import csv
import unittest
from pathlib import Path

from think_budget_vllm_adapter.optimizations.scene_classifier import (
    classify_scene,
)


_PRODUCT_SCENES = {
    "react_whitebox_overall_planning",
    "react_machine_data_analysis",
    "kpi_configuration_assurance",
    "netqad_e2e_single_step",
    "netqad_e2e_planning",
    "long_context_root_cause",
}

_RUN_05_EXPECTED = {
    "*ReAct白盒-整体规划*.csv": "react_whitebox_overall_planning",
    "*ReAct白盒-机器数据分析*.csv": "react_machine_data_analysis",
    "*KPI配置保障*.csv": "kpi_configuration_assurance",
    "*NetQAD*单步规划*.csv": "netqad_e2e_single_step",
    "*NetQAD*规划模块*.csv": "netqad_e2e_planning",
    "*长文本根因推理*.csv": "long_context_root_cause",
}


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
            (root / "docs" / "log").rglob("dtb_*07-27*.csv"))
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
        self.assertTrue(all(
            result["scene"] == "react_whitebox_overall_planning"
            for result in sampled.values()
        ))
        self.assertTrue(all(
            result["scene_matched"]
            for result in sampled.values()
        ))
        self.assertTrue(all(
            result["scene"] != "classification"
            for result in sampled.values()
        ))

    def test_run_05_full_replay_routes_only_six_frozen_scenes(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        result_dir = (
            root / "docs" / "log" / "perf" / "run_05_fixed_v2"
            / "test_result"
        )
        expected_by_path = {
            path: scene
            for pattern, scene in _RUN_05_EXPECTED.items()
            for path in result_dir.glob(pattern)
        }
        self.assertEqual(len(expected_by_path), len(_RUN_05_EXPECTED))

        total = 0
        routed = 0
        rejected_from_product_routes = 0
        for path in sorted(result_dir.glob("dtb_*.csv")):
            with path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            expected_scene = expected_by_path.get(path)
            for row in rows:
                total += 1
                result = classify_scene(row["问题"])
                with self.subTest(file=path.name, qa_id=row["QA_ID"]):
                    if expected_scene is not None:
                        routed += 1
                        self.assertEqual(result["scene"], expected_scene)
                        self.assertTrue(result["scene_matched"])
                        self.assertGreaterEqual(result["top_score"], 100.0)
                        self.assertEqual(result["reject_reason"], None)
                    else:
                        rejected_from_product_routes += 1
                        self.assertNotIn(result["scene"], _PRODUCT_SCENES)

        self.assertEqual(total, 633)
        self.assertEqual(routed, 282)
        self.assertEqual(rejected_from_product_routes, 351)


if __name__ == "__main__":
    unittest.main()
