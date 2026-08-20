from __future__ import annotations

import csv
import unittest
from pathlib import Path

from dtb.budgeting.budget_decision import DecisionBranch
from dtb.budgeting.budget_resolver import DtbBudgetResolver
from dtb.config.loader import DEFAULT_CONFIG_PATH, load_dtb_service_config


_EXPECTED_PROFILES = {
    "react_whitebox_single_step": (450, 200, 4, 450, 1100, 0.93),
    "react_whitebox_overall_planning": (850, 450, 2, 850, 1500, 0.93),
    "react_machine_data_analysis": (500, 250, 2, 500, 850, 0.93),
    "kpi_configuration_assurance": (500, 300, 4, 500, 1600, 0.93),
    "netqad_e2e_single_step": (300, 300, 3, 300, 1050, 0.93),
    "netqad_e2e_planning": (950, 450, 5, 950, 3050, 0.95),
    "event_self_closure": (700, 350, 3, 700, 1700, 0.93),
    "alarm_diagnosis_advice": (850, 450, 3, 850, 2150, 0.93),
    "alarm_diagnosis_conclusion": (1300, 450, 4, 1300, 2750, 0.93),
    "long_context_root_cause": (1200, 400, 4, 1200, 2600, 0.93),
    "complaint_tool_use": (1000, 250, 3, 1000, 1650, 0.93),
    "complaint_classification_zh": (400, 200, 3, 400, 1000, 0.93),
    "complaint_classification_en": (400, 200, 4, 400, 1050, 0.93),
}

_DATASET_PATTERNS = {
    "react_whitebox_single_step": "*ReAct白盒-单步规划*.csv",
    "react_whitebox_overall_planning": "*ReAct白盒-整体规划*.csv",
    "react_machine_data_analysis": "*ReAct白盒-机器数据分析*.csv",
    "kpi_configuration_assurance": "*KPI配置保障*.csv",
    "netqad_e2e_single_step": "*NetQAD*单步规划*.csv",
    "netqad_e2e_planning": "*NetQAD*规划模块*.csv",
    "event_self_closure": "*事件自闭环*.csv",
    "alarm_diagnosis_advice": "*建议总结*.csv",
    "alarm_diagnosis_conclusion": "*结论总结*.csv",
    "long_context_root_cause": "*长文本根因推理*.csv",
    "complaint_tool_use": "*投诉助理*.csv",
    "complaint_classification_zh": "*分类_164*.csv",
    "complaint_classification_en": "*分类_20*.csv",
}


class SceneConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = load_dtb_service_config()
        self.resolver = DtbBudgetResolver(self.service)

    def test_dynamic_modules_and_telemetry_default_off(self) -> None:
        self.assertFalse(self.service.algorithm.enable_history_budget)
        self.assertFalse(self.service.algorithm.enable_difficulty_estimator)
        self.assertEqual(self.service.observability.mode.value, "off")

    def test_yaml_profiles_equal_frozen_parameters(self) -> None:
        self.assertEqual(
            self.service.scene_priority[:len(_EXPECTED_PROFILES)],
            tuple(_EXPECTED_PROFILES),
        )
        for scene, expected in _EXPECTED_PROFILES.items():
            values = self.service.scenes[scene]
            actual = (
                values["check_length"],
                values["think_stride"],
                values["num_thinking"],
                values["min_think_tokens"],
                values["max_think_tokens"],
                values["prob_th"],
            )
            with self.subTest(scene=scene):
                self.assertEqual(actual, expected)
                indicators = values["indicators"]
                self.assertTrue(
                    indicators.get("required_all")
                    or indicators.get("required_any")
                )

    def test_yaml_has_no_embedded_policy_or_legacy_telemetry_switch(self) -> None:
        text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("PERFORMANCE_POLICY:", text)
        self.assertNotIn("emit_phase_metrics", text)
        self.assertNotIn("verbose_stage_logging", text)
        self.assertIn('  mode: "off"', text)

    def test_full_replay_reaches_frozen_budget_without_telemetry(self) -> None:
        result_dir = (
            Path(__file__).resolve().parents[1]
            / "docs/log/perf/run_06_v3/test_result"
        )
        total = 0
        for scene, pattern in _DATASET_PATTERNS.items():
            path = next(result_dir.glob(pattern))
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                total += 1
                decision = self.resolver.resolve(
                    row["问题"],
                    self.service.algorithm,
                    None,
                    hard_token_limit=10000,
                )
                expected = _EXPECTED_PROFILES[scene]
                with self.subTest(scene=scene, qa_id=row["QA_ID"]):
                    self.assertEqual(
                        decision.decision_branch,
                        DecisionBranch.SCENE_DIFFICULTY,
                    )
                    self.assertEqual(decision.scene, scene)
                    self.assertEqual(
                        (
                            decision.budget.check_length,
                            decision.budget.think_stride,
                            decision.budget.num_thinking,
                            decision.budget.min_think_budget,
                            decision.hard_think_token_limit,
                            decision.confidence_threshold,
                        ),
                        expected,
                    )
        self.assertEqual(total, 633)


if __name__ == "__main__":
    unittest.main()
