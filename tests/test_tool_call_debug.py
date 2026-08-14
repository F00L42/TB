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
        self.assertEqual(observation.terminal_count, 1)
        self.assertTrue(observation.tool_call_indexes_contiguous)
        self.assertEqual(observation.anomaly_reasons, [])

    def test_multiple_sibling_tool_calls_are_valid(self) -> None:
        chunks = [
            {"id": "r", "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call-1",
                "function": {"name": "bash", "arguments": "{}"},
            }]}, "finish_reason": None}]},
            {"id": "r", "choices": [{"delta": {"tool_calls": [{
                "index": 1,
                "id": "call-2",
                "function": {"name": "bash", "arguments": "{}"},
            }]}, "finish_reason": None}]},
            {"id": "r", "choices": [{
                "delta": {}, "finish_reason": "tool_calls",
            }]},
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

        self.assertEqual(len(observation.tool_calls), 2)
        self.assertTrue(observation.tool_call_indexes_contiguous)
        self.assertEqual(observation.anomaly_reasons, [])

    def test_content_after_terminal_is_rejected(self) -> None:
        chunks = [
            {"id": "r", "choices": [{
                "delta": {}, "finish_reason": "stop",
            }]},
            {"id": "r", "choices": [{
                "delta": {"content": "tail"}, "finish_reason": None,
            }]},
        ]
        observation = assemble_observation(
            line_no=1,
            attempt=1,
            variant="dtb",
            client_request_id="client",
            status_code=200,
            elapsed_ms=1.0,
            expected_tool_call=False,
            chunks=chunks,
        )

        self.assertTrue(observation.chunk_after_terminal)
        self.assertIn("chunk_after_terminal", observation.anomaly_reasons)

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

    def test_analysis_accepts_one_completion_with_tool_continuation(self) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [
            {
                "schema": 2,
                "event": "state_enter",
                "phase": "_ThinkingState",
                "sequence": 1,
                "completion_epoch": 1,
                "reasoning_boundary": "none",
            },
            {
                "schema": 2,
                "event": "delta",
                "phase": "initial_think",
                "sequence": 2,
                "completion_epoch": 1,
                "controller_state": "thinking",
                "next_state": "free_final_answer",
                "reasoning_boundary": "tool_call",
                "raw_text": "thought<tool_call>",
                "raw_text_sha256": "text",
                "forwarded_text_sha256": "text",
                "source_token_ids_sha256": "ids",
                "forwarded_token_ids_sha256": "ids",
            },
            {
                "schema": 2,
                "event": "state_enter",
                "phase": "_FinalAnswerState",
                "sequence": 3,
                "completion_epoch": 1,
                "reasoning_boundary": "tool_call",
            },
            {
                "schema": 2,
                "event": "terminal_flush",
                "phase": "final_answer",
                "sequence": 4,
                "completion_epoch": 1,
                "reasoning_boundary": "tool_call",
                "terminal_flush": True,
            },
        ]

        analysis = classify(summary, records)

        self.assertEqual(analysis["diagnosis"], "pass")
        self.assertEqual(analysis["completion_epoch_count"], 1)
        self.assertEqual(analysis["terminal_flush_count"], 1)
        self.assertEqual(analysis["fresh_final_after_tool_boundary"], 0)
        self.assertEqual(analysis["thinking_or_probe_after_tool_boundary"], 0)

    def test_analysis_rejects_thinking_after_tool_boundary(self) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [
            {
                "schema": 2,
                "event": "delta",
                "phase": "initial_think",
                "child_request_id": "r_initial_think_1",
                "sequence": 1,
                "completion_epoch": 1,
                "controller_state": "thinking",
                "next_state": "free_final_answer",
                "reasoning_boundary": "tool_call",
                "raw_text": "<tool_call>",
                "raw_text_sha256": "text",
                "forwarded_text_sha256": "text",
                "source_token_ids_sha256": "ids",
                "forwarded_token_ids_sha256": "ids",
            },
            {
                "schema": 2,
                "event": "state_enter",
                "phase": "_ConfidenceProbeState",
                "sequence": 2,
                "completion_epoch": 1,
                "reasoning_boundary": "tool_call",
            },
            {
                "schema": 2,
                "event": "terminal_flush",
                "phase": "final_answer",
                "sequence": 3,
                "completion_epoch": 1,
                "terminal_flush": True,
            },
        ]

        analysis = classify(summary, records)

        self.assertEqual(
            analysis["diagnosis"],
            "controller_completion_lifecycle_failure",
        )
        self.assertEqual(analysis["thinking_or_probe_after_tool_boundary"], 1)

    def test_analysis_does_not_require_dtb_trace_for_vanilla(self) -> None:
        analysis = classify({
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "vanilla",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }, [])

        self.assertEqual(analysis["diagnosis"], "vanilla_pass")

    def test_analysis_accepts_audited_stop_boundary_alignment(self) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [
            {
                "schema": 3,
                "event": "delta",
                "phase": "initial_think",
                "child_request_id": "r_initial_think_1",
                "sequence": 1,
                "completion_epoch": 1,
                "controller_state": "thinking",
                "next_state": "free_final_answer",
                "reasoning_boundary": "think_end",
                "raw_text": "</think>",
                "raw_text_sha256": "same-text",
                "forwarded_text_sha256": "same-text",
                "token_ids": [1, 2, 3],
                "forwarded_token_ids": [1],
                "alignment_dropped_token_ids": [2, 3],
                "alignment_dropped_token_count": 2,
                "source_token_ids_sha256": "raw-ids",
                "forwarded_token_ids_sha256": "forwarded-ids",
                "stop_boundary_aligned": True,
            },
            {
                "schema": 3,
                "event": "terminal_flush",
                "phase": "final_answer",
                "sequence": 2,
                "completion_epoch": 1,
                "terminal_flush": True,
            },
        ]

        analysis = classify(summary, records)

        self.assertEqual(analysis["diagnosis"], "pass")
        self.assertEqual(
            analysis["controller_passthrough_violation_sequences"], [])
        self.assertEqual(
            analysis["invalid_stop_boundary_alignment_sequences"], [])
        self.assertEqual(
            analysis["stop_boundary_alignments"][0]["dropped_token_ids"],
            [2, 3],
        )
        self.assertEqual(
            analysis["children"][0]["stream_deltas"][0]["raw_token_ids"],
            [1, 2, 3],
        )

    def test_analysis_identifies_controller_input_text_token_gap(self) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": False,
            "anomaly_reasons": ["expected_tool_call_missing"],
        }
        records = [
            {
                "schema": 2,
                "event": "delta",
                "phase": "initial_think",
                "sequence": 1,
                "completion_epoch": 1,
                "controller_state": "thinking",
                "next_state": "free_final_answer",
                "reasoning_boundary": "tool_call",
                "raw_text": "f",
                "raw_text_sha256": "text",
                "forwarded_text_sha256": "text",
                "source_token_ids_sha256": "ids",
                "forwarded_token_ids_sha256": "ids",
            },
            {
                "schema": 2,
                "event": "state_enter",
                "phase": "_FinalAnswerState",
                "sequence": 2,
                "completion_epoch": 1,
                "reasoning_boundary": "tool_call",
            },
            {
                "schema": 2,
                "event": "terminal_flush",
                "phase": "final_answer",
                "sequence": 3,
                "completion_epoch": 1,
                "terminal_flush": True,
            },
        ]

        analysis = classify(summary, records)

        self.assertEqual(
            analysis["diagnosis"], "controller_input_text_token_gap")


if __name__ == "__main__":
    unittest.main()
