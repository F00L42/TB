from __future__ import annotations

import csv
import unittest
from pathlib import Path

from dtb.config.loader import load_dtb_service_config
from dtb.budgeting.scene_router import (
    classify_scene,
)


_PRODUCT_SCENES = {
    "react_whitebox_single_step",
    "react_whitebox_overall_planning",
    "react_machine_data_analysis",
    "kpi_configuration_assurance",
    "netqad_e2e_single_step",
    "netqad_e2e_planning",
    "event_self_closure",
    "alarm_diagnosis_advice",
    "alarm_diagnosis_conclusion",
    "long_context_root_cause",
    "complaint_tool_use",
    "complaint_classification_zh",
    "complaint_classification_en",
}

_RUN_06_EXPECTED = {
    "*ReAct白盒-单步规划*.csv": "react_whitebox_single_step",
    "*ReAct白盒-整体规划*.csv": "react_whitebox_overall_planning",
    "*ReAct白盒-机器数据分析*.csv": "react_machine_data_analysis",
    "*KPI配置保障*.csv": "kpi_configuration_assurance",
    "*NetQAD*单步规划*.csv": "netqad_e2e_single_step",
    "*NetQAD*规划模块*.csv": "netqad_e2e_planning",
    "*事件自闭环*.csv": "event_self_closure",
    "*建议总结*.csv": "alarm_diagnosis_advice",
    "*结论总结*.csv": "alarm_diagnosis_conclusion",
    "*长文本根因推理*.csv": "long_context_root_cause",
    "*投诉助理*.csv": "complaint_tool_use",
    "*分类_164*.csv": "complaint_classification_zh",
    "*分类_20*.csv": "complaint_classification_en",
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

    def test_rejection_gate_rejects_two_complete_product_templates(
        self,
    ) -> None:
        result = classify_scene(
            "## Current Diagnosis Roadmap\n"
            "Only one step is planned at a time.\n"
            "NextOperationRecommendation\nCheckedInformation"
        )
        self.assertEqual(result["scene"], "general")
        self.assertFalse(result["scene_matched"])
        self.assertEqual(result["reject_reason"], "below_min_margin")
        self.assertEqual(result["top_score"], 100.0)
        self.assertEqual(result["second_score"], 100.0)

    def test_product_template_requires_every_anchor(self) -> None:
        result = classify_scene("## Current Diagnosis Roadmap")
        self.assertNotEqual(result["scene"], "react_whitebox_single_step")
        self.assertIn(
            "Only one step is planned at a time.",
            result["missing_required"]["react_whitebox_single_step"]["all"],
        )

    def test_each_product_template_rejects_missing_required_evidence(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        result_dir = (
            root / "tests/fixtures/scenes/run_06_v3"
        )
        definitions = load_dtb_service_config().scenes

        for pattern, scene in _RUN_06_EXPECTED.items():
            path = next(result_dir.glob(pattern))
            with path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as stream:
                question = next(csv.DictReader(stream))["问题"]
            indicators = definitions[scene]["indicators"]
            required_all = indicators.get("required_all", [])
            required_any = indicators.get("required_any", [])

            for anchor in required_all:
                mutated = question.replace(anchor, "")
                with self.subTest(scene=scene, removed=anchor):
                    self.assertNotEqual(classify_scene(mutated)["scene"], scene)
            if required_any:
                mutated = question
                for anchor in required_any:
                    mutated = mutated.replace(anchor, "")
                with self.subTest(scene=scene, removed="required_any"):
                    self.assertNotEqual(classify_scene(mutated)["scene"], scene)

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
        path = root / "tests/fixtures/scenes/legacy_regressions.csv"
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

    def test_run_06_full_replay_routes_all_thirteen_frozen_scenes(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        result_dir = (
            root / "tests/fixtures/scenes/run_06_v3"
        )
        expected_by_path = {
            path: scene
            for pattern, scene in _RUN_06_EXPECTED.items()
            for path in result_dir.glob(pattern)
        }
        self.assertEqual(len(expected_by_path), len(_RUN_06_EXPECTED))

        total = 0
        routed = 0
        for path in sorted(result_dir.glob("dtb_*.csv")):
            with path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            expected_scene = expected_by_path.get(path)
            self.assertIsNotNone(expected_scene, path.name)
            for row in rows:
                total += 1
                result = classify_scene(row["问题"])
                with self.subTest(file=path.name, qa_id=row["QA_ID"]):
                    routed += 1
                    self.assertEqual(result["scene"], expected_scene)
                    self.assertTrue(result["scene_matched"])
                    self.assertGreaterEqual(result["top_score"], 100.0)
                    self.assertEqual(result["reject_reason"], None)
                    matched_product_templates = [
                        scene for scene in _PRODUCT_SCENES
                        if result["scores"][scene] >= 100.0
                    ]
                    self.assertEqual(
                        matched_product_templates, [expected_scene])

        self.assertEqual(total, 633)
        self.assertEqual(routed, 633)


if __name__ == "__main__":
    unittest.main()
