from __future__ import annotations

import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from dtb.config.loader import (
    clear_dtb_config_cache,
    load_dtb_service_config,
)
from dtb.config.schema import (
    DtbAlgorithmConfig,
    DtbAlgorithmPolicy,
    DtbTelemetryMode,
    POLICY_VERSION,
)
from dtb.request_controller import DtbRequestController


class DtbConfigTests(unittest.TestCase):
    def test_algorithm_config_uses_dtb_terms_only(self) -> None:
        names = {item.name for item in fields(DtbAlgorithmConfig)}
        self.assertTrue({
            "check_length", "min_think_budget", "num_thinking",
            "think_stride",
        }.issubset(names))
        self.assertTrue({
            "initial_thought_limit", "think_ratio", "token_budget",
            "trial_answers", "enable_task_type_budget",
        }.isdisjoint(names))
        self.assertNotIn("emit_phase_metrics", names)
        self.assertNotIn("verbose_stage_logging", names)
        self.assertNotIn("final_answer_max_tokens", names)

    def test_algorithm_policy_is_code_owned(self) -> None:
        self.assertEqual(POLICY_VERSION, "dtb-algorithm-v4")
        self.assertEqual(DtbAlgorithmPolicy().version, POLICY_VERSION)
        with self.assertRaises(ValueError):
            DtbAlgorithmPolicy(trial_answer_length=51)

    def test_yaml_is_the_only_runtime_source_and_defaults_telemetry_off(self) -> None:
        service = load_dtb_service_config()
        self.assertTrue(service.algorithm.budget_enabled)
        self.assertEqual(service.algorithm.check_length, 200)
        self.assertFalse(service.algorithm.enable_history_budget)
        self.assertFalse(service.algorithm.enable_difficulty_estimator)
        self.assertIs(service.observability.mode, DtbTelemetryMode.OFF)
        self.assertEqual(
            Path(service.history_store.path).parts[-4:],
            ("dtb", "data", "history", "inference_history.jsonl"),
        )
        self.assertEqual(service.history_store.max_entries, 5000)
        self.assertEqual(service.scenes["code_generation"]["check_length"], 2000)

    def test_invalid_yaml_fails_fast_without_builtin_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.yaml"
            path.write_text("RUNTIME: [invalid]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_dtb_service_config(path)
        clear_dtb_config_cache()

    def test_stream_audit_requires_request_allowlist(self) -> None:
        source = load_dtb_service_config()
        with tempfile.TemporaryDirectory() as directory:
            original = Path(__file__).parents[1] / "dtb/config/budget_config.yaml"
            text = original.read_text(encoding="utf-8").replace(
                '  mode: "off"', '  mode: "stream_audit"'
            )
            path = Path(directory) / "stream.yaml"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_dtb_service_config(path)
        self.assertIs(source.observability.mode, DtbTelemetryMode.OFF)
        clear_dtb_config_cache()

    def test_disabled_history_does_not_configure_or_construct_store(self) -> None:
        with patch(
            "dtb.request_controller.configure_history_store"
        ) as configure:
            controller = DtbRequestController(
                service_config=load_dtb_service_config()
            )
        configure.assert_not_called()
        self.assertIsNone(controller.history_recorder)


if __name__ == "__main__":
    unittest.main()
