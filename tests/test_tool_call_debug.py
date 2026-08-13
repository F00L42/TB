from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from docs.debug.tool_call.analyze_tool_call_run import classify
from docs.debug.tool_call.replay_tool_calls import (
    assemble_observation,
    build_body,
    validate_conversation,
)


ROOT = Path(__file__).resolve().parents[1]


class ToolCallReplayTests(unittest.TestCase):
    def test_known_failure_is_classified_as_leaked_tool_xml(self) -> None:
        path = ROOT / "docs" / "debug" / "tool_call" / "chunks-4-fail.jsonl"
        chunks = [
            json.loads(line)["chunk"]
            for line in path.read_text(encoding="utf-8").splitlines()
        ]

        observation = assemble_observation(
            line_no=4,
            attempt=1,
            variant="dtb",
            client_request_id="client",
            status_code=200,
            elapsed_ms=1.0,
            expected_tool_call=True,
            chunks=chunks,
        )

        self.assertFalse(observation.has_tool_call)
        self.assertTrue(observation.leaked_tool_xml)
        self.assertTrue(observation.role_tail)
        self.assertIn("expected_tool_call_missing", observation.anomaly_reasons)
        self.assertIn("tool_xml_leaked_to_content", observation.anomaly_reasons)
        self.assertIn("role_tail_in_content", observation.anomaly_reasons)

    def test_tool_deltas_are_reconstructed_and_arguments_validated(self) -> None:
        chunks = [
            {"id": "r", "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call-1",
                "function": {"name": "bash", "arguments": ""},
            }]}, "finish_reason": None}]},
            {"id": "r", "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"arguments": '{"command":"pwd"}'},
            }]}, "finish_reason": "tool_calls"}]},
        ]

        observation = assemble_observation(
            line_no=1,
            attempt=1,
            variant="dtb",
            client_request_id="client",
            status_code=200,
            elapsed_ms=1.0,
            expected_tool_call=True,
            chunks=chunks,
        )

        self.assertTrue(observation.has_tool_call)
        self.assertEqual(observation.invalid_argument_indexes, [])
        self.assertEqual(observation.anomaly_reasons, [])

    def test_conversation_validator_matches_tool_results(self) -> None:
        valid = [
            {"role": "user", "content": "run"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]
        invalid = [*valid[:-1], {
            "role": "tool", "tool_call_id": "other", "content": "ok"
        }]

        self.assertEqual(validate_conversation(valid), [])
        self.assertTrue(validate_conversation(invalid))

    def test_build_body_removes_fixed_fields_for_vanilla(self) -> None:
        args = SimpleNamespace(
            model="",
            max_tokens=100,
            temperature=0.0,
            top_p=0.95,
            fixed_budget_tokens=10,
            fixed_check_length=2,
            fixed_think_stride=2,
            fixed_num_thinking=2,
            fixed_min_think_tokens=1,
            fixed_prob_th=0.9,
        )
        source = {
            "model": "m",
            "messages": [],
            "fixed_budget_tokens": 999,
        }

        vanilla = build_body(
            source,
            variant="vanilla",
            client_request_id="r",
            args=args,
        )
        dtb = build_body(
            source,
            variant="dtb",
            client_request_id="r2",
            args=args,
        )

        self.assertFalse(vanilla["enable_think_budget"])
        self.assertNotIn("fixed_budget_tokens", vanilla)
        self.assertTrue(dtb["enable_think_budget"])
        self.assertEqual(dtb["fixed_budget_tokens"], 10)

    def test_analysis_identifies_repaired_controller_gap(self) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [{
            "phase": "final_answer",
            "sequence": 1,
            "raw_text": "f",
            "parser_text": "<tool_call>\n<f",
            "decoded_token_text": "<tool_call>\n<f",
            "repaired_tags": ["<tool_call>"],
        }]

        analysis = classify(summary, records)

        self.assertEqual(analysis["diagnosis"], "pass")
        self.assertEqual(analysis["repair_count"], 1)


if __name__ == "__main__":
    unittest.main()
