from __future__ import annotations

import csv
import re
import unittest
from pathlib import Path

from think_budget_vllm_adapter.budget_controller import BudgetController
from think_budget_vllm_adapter.config import unified_config
from think_budget_vllm_adapter.context import AlgorithmConfig, DecisionBranch
from think_budget_vllm_adapter.telemetry import TimingCollector


_EXPECTED_PROFILES = {
    "react_whitebox_single_step": (450, 400, 3, 450, 1400, 0.93),
    "react_whitebox_overall_planning": (850, 450, 2, 850, 1500, 0.93),
    "react_machine_data_analysis": (500, 250, 2, 500, 850, 0.93),
    "kpi_configuration_assurance": (500, 300, 4, 500, 1600, 0.93),
    "netqad_e2e_single_step": (300, 300, 3, 300, 1050, 0.93),
    "netqad_e2e_planning": (950, 450, 5, 950, 3050, 0.95),
    "event_self_closure": (700, 350, 3, 700, 1700, 0.93),
    "alarm_diagnosis_advice": (850, 450, 3, 850, 2150, 0.93),
    "alarm_diagnosis_conclusion": (1300, 450, 4, 1300, 2750, 0.93),
    "long_context_root_cause": (1200, 400, 4, 1200, 2600, 0.93),
    "complaint_tool_use": (750, 250, 4, 750, 1650, 0.93),
    "complaint_classification_zh": (400, 200, 3, 400, 850, 0.93),
    "complaint_classification_en": (400, 200, 4, 400, 1050, 0.93),
}

_PROFILE_FIELDS = (
    "check_length",
    "think_stride",
    "num_thinking",
    "min_think_tokens",
    "max_think_tokens",
    "prob_th",
)

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
    @staticmethod
    def _yaml_scene_block(text: str, scene: str) -> str:
        match = re.search(
            rf"(?ms)^  {re.escape(scene)}:\n"
            rf"(?P<body>.*?)(?=^  [a-z][a-z0-9_]*:\n|^SCENE_PRIORITY:)",
            text,
        )
        if match is None:
            raise AssertionError(f"missing YAML scene: {scene}")
        return match.group("body")

    def test_phase_one_runtime_defaults_disable_dynamic_modules(self) -> None:
        runtime = unified_config._get_default_config()["RUNTIME"]
        self.assertFalse(runtime["enable_history_budget"])
        self.assertFalse(runtime["enable_difficulty_estimator"])
        self.assertFalse(AlgorithmConfig().enable_history_budget)
        self.assertFalse(AlgorithmConfig().enable_difficulty_estimator)

    def test_builtin_profiles_equal_selected_frozen_parameters(self) -> None:
        config = unified_config._get_default_config()
        scenes = config["SCENES"]
        self.assertEqual(
            config["SCENE_PRIORITY"][:len(_EXPECTED_PROFILES)],
            list(_EXPECTED_PROFILES),
        )
        for scene, expected in _EXPECTED_PROFILES.items():
            with self.subTest(scene=scene):
                actual = tuple(scenes[scene][field] for field in _PROFILE_FIELDS)
                self.assertEqual(actual, expected)
                indicators = scenes[scene]["indicators"]
                self.assertTrue(
                    indicators.get("required_all")
                    or indicators.get("required_any")
                )

    def test_yaml_declares_phase_one_switches(self) -> None:
        path = Path(unified_config._BUDGET_CONFIG_PATH)
        text = path.read_text(encoding="utf-8")
        self.assertIn("  enable_history_budget: false", text)
        self.assertIn("  enable_difficulty_estimator: false", text)

    def test_yaml_product_routes_equal_builtin_scalars_and_anchors(
        self,
    ) -> None:
        path = Path(unified_config._BUDGET_CONFIG_PATH)
        text = path.read_text(encoding="utf-8")
        builtin = unified_config._get_default_config()
        priority_block = text.split("SCENE_PRIORITY:\n", 1)[1].split(
            "\nDIFFICULTY_TIERS:", 1
        )[0]
        yaml_priority = [
            line.removeprefix("  - ")
            for line in priority_block.splitlines()
            if line.startswith("  - ")
        ]
        self.assertEqual(
            yaml_priority[:len(_EXPECTED_PROFILES)],
            list(_EXPECTED_PROFILES),
        )

        for scene, expected in _EXPECTED_PROFILES.items():
            with self.subTest(scene=scene):
                block = self._yaml_scene_block(text, scene)
                actual = []
                for field in _PROFILE_FIELDS:
                    match = re.search(
                        rf"(?m)^    {re.escape(field)}: ([0-9.]+)$",
                        block,
                    )
                    self.assertIsNotNone(match, field)
                    raw = match.group(1)
                    actual.append(float(raw) if "." in raw else int(raw))
                self.assertEqual(tuple(actual), expected)

                indicators = builtin["SCENES"][scene]["indicators"]
                anchors = (
                    indicators.get("required_all", [])
                    + indicators.get("required_any", [])
                )
                for anchor in anchors:
                    self.assertIn(f'        - "{anchor}"', block)

    def test_switch_only_full_replay_reaches_effective_frozen_budget(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        result_dir = (
            root / "docs" / "log" / "perf" / "run_06_v3"
            / "test_result"
        )
        timing = TimingCollector(emit_phase_metrics=False)
        config = AlgorithmConfig(
            enable_difficulty_estimator=False,
            enable_history_budget=False,
        )

        total = 0
        for scene, pattern in _DATASET_PATTERNS.items():
            path = next(result_dir.glob(pattern))
            with path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                total += 1
                plan = BudgetController._decide(
                    row["问题"], config, timing, hard_token_limit=10000)
                expected = _EXPECTED_PROFILES[scene]
                with self.subTest(scene=scene, qa_id=row["QA_ID"]):
                    self.assertEqual(
                        plan.decision_branch,
                        DecisionBranch.SCENE_DIFFICULTY,
                    )
                    self.assertEqual(plan.scene, scene)
                    self.assertTrue(plan.scene_matched)
                    self.assertEqual(
                        (
                            plan.budget.check_length,
                            plan.budget.think_stride,
                            plan.budget.num_thinking,
                            plan.budget.min_think_budget,
                            plan.hard_think_token_limit,
                            plan.confidence_threshold,
                        ),
                        expected,
                    )
                    self.assertEqual(
                        plan.source_by_field[
                            "hard_think_token_limit"
                        ]["source"],
                        "scene.max_think_tokens",
                    )
                    for field in (
                        "check_length",
                        "min_think_budget",
                        "num_thinking",
                        "think_stride",
                    ):
                        self.assertEqual(
                            plan.source_by_field[field]["source"],
                            "scene",
                        )
                    self.assertEqual(
                        plan.source_by_field[
                            "confidence_threshold"
                        ]["source"],
                        "scene",
                    )
        self.assertEqual(total, 633)

    @unittest.skipUnless(
        unified_config._YAML_AVAILABLE,
        "PyYAML is not installed in the local test runtime",
    )
    def test_yaml_and_builtin_product_routes_are_equivalent(self) -> None:
        path = Path(unified_config._BUDGET_CONFIG_PATH)
        with path.open("r", encoding="utf-8") as stream:
            yaml_config = unified_config.yaml.safe_load(stream)
        builtin = unified_config._get_default_config()

        self.assertEqual(
            yaml_config["SCENE_PRIORITY"][:len(_EXPECTED_PROFILES)],
            list(_EXPECTED_PROFILES),
        )
        for scene in _EXPECTED_PROFILES:
            with self.subTest(scene=scene):
                self.assertEqual(
                    yaml_config["SCENES"][scene],
                    builtin["SCENES"][scene],
                )


if __name__ == "__main__":
    unittest.main()
