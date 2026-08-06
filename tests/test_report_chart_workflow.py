import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "docs" / "report" / "chart" / "generate_report.py"
CONFIG_PATH = REPO_ROOT / "docs" / "report" / "chart" / "configs" / "run_04_fixed_v1.json"

SPEC = importlib.util.spec_from_file_location("report_chart_generator", MODULE_PATH)
assert SPEC and SPEC.loader
REPORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REPORT
SPEC.loader.exec_module(REPORT)


class ReportChartWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.comparison = REPORT.load_comparison(CONFIG_PATH)
        cls.summaries = REPORT.build_summaries(cls.comparison)

    def test_scene_filename_normalization(self):
        filename = "vanilla_事件自闭环_25题_0514_2026-08-03_16-19-46.csv"
        self.assertEqual(
            REPORT.normalize_scene(filename, "vanilla_"),
            "事件自闭环_25题_0514",
        )

    def test_fixed_v1_regression_metrics(self):
        REPORT.validate_expected(
            self.summaries,
            self.comparison["config"]["expected"],
        )
        macro = self.summaries["macro"]
        micro = self.summaries["micro"]
        self.assertEqual(len(self.summaries["scenes"]), 13)
        self.assertEqual(micro["n"], 633)
        self.assertEqual(micro["faster_count"], 607)
        self.assertEqual((micro["baseline_correct"], micro["candidate_correct"]), (558, 553))
        self.assertEqual((micro["vanilla_only"], micro["candidate_only"]), (34, 29))
        self.assertAlmostEqual(macro["e2e_gain_pct"], 49.471186, places=5)
        self.assertAlmostEqual(micro["e2e_gain_pct"], 47.702722, places=5)
        self.assertAlmostEqual(micro["visible_token_saving_pct"], 60.060506, places=5)

    def test_scene_level_message_is_complete(self):
        scenes = self.summaries["scenes"]
        self.assertTrue(all(scene["e2e_gain_pct"] > 0 for scene in scenes))
        self.assertTrue(all(scene["visible_token_saving_pct"] > 0 for scene in scenes))
        self.assertEqual(sum(scene["accuracy_delta_pp"] < 0 for scene in scenes), 8)
        self.assertEqual(sum(scene["client_errors"] for scene in scenes), 0)


if __name__ == "__main__":
    unittest.main()
