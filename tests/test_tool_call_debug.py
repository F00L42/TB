from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.dtb.analyze_tool_call_run import classify
from tools.dtb.replay_tool_calls import (
    assemble_observation,
    build_body,
    build_paired_summary,
    build_request_id,
    validate_conversation,
)
from tools.dtb.extract_tool_call_replay_cases import (
    read_candidates,
    select_first_clean,
)


ROOT = Path(__file__).resolve().parents[1]


class ToolCallReplayTests(unittest.TestCase):
    def test_request_id_is_deterministic_for_stream_audit_allowlist(self) -> None:
        self.assertEqual(
            build_request_id(4, 2, "dtb", "audit-01"),
            "dtbtool-l4-a2-dtb-audit-01",
        )

    def test_known_failure_is_classified_as_leaked_tool_xml(self) -> None:
        path = ROOT / "tests" / "fixtures" / "tool_call" / "chunks-4-fail.jsonl"
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

    def test_full_log_extractor_selects_wire_exact_clean_case(self) -> None:
        record = {
            "timestamp": "2026-08-27T00:00:00",
            "request_id": "source-request",
            "request_body": {
                "model": "m",
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "- branch: branch-a\nrun"},
                ],
                "tools": [],
            },
            "response_data": {
                "content": "l_call>\n<function=read>",
                "reasoning": "",
                "tool_calls": "",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "full_log.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            candidates = read_candidates(path, {"branch-a"})

        selected = select_first_clean(candidates, ["branch-a"])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].request_id, "source-request")
        self.assertEqual(selected[0].leaked_functions, ("read",))
        self.assertTrue(selected[0].context_clean)
        self.assertEqual(selected[0].record["request_body"], record["request_body"])

    def test_paired_summary_excludes_protocol_failures_from_latency(self) -> None:
        terminal = [{"id": "r", "choices": [{
            "delta": {"content": "done"}, "finish_reason": "stop",
        }]}]
        vanilla = assemble_observation(
            line_no=1,
            attempt=1,
            variant="vanilla",
            client_request_id="v",
            status_code=200,
            elapsed_ms=100.0,
            expected_tool_call=False,
            chunks=terminal,
            first_output_ms=20.0,
        )
        dtb = assemble_observation(
            line_no=1,
            attempt=1,
            variant="dtb",
            client_request_id="d",
            status_code=200,
            elapsed_ms=80.0,
            expected_tool_call=False,
            chunks=terminal,
            first_output_ms=15.0,
        )

        summary = build_paired_summary([vanilla, dtb])

        self.assertEqual(summary["all_pairs"]["pairs"], 1)
        self.assertEqual(summary["protocol_clean_pairs"]["pairs"], 1)
        self.assertEqual(
            summary["protocol_clean_pairs"]["dtb_minus_vanilla_ms"]["median"],
            -20.0,
        )
        self.assertTrue(summary["latency_ranking_valid"])

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
        self.assertEqual(analysis["terminal_contract_mode"], "terminal_flush")
        self.assertEqual(analysis["fresh_final_after_tool_boundary"], 0)
        self.assertEqual(analysis["thinking_or_probe_after_tool_boundary"], 0)

    def test_analysis_accepts_schema_four_source_terminal_passthrough(
        self,
    ) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [{
            "schema": 4,
            "event": "final_terminal_passthrough",
            "phase": "final_answer",
            "sequence": 1,
            "completion_epoch": 1,
            "reasoning_boundary": "think_end",
            "raw_text": "<tool_call></tool_call>",
            "raw_text_sha256": "text",
            "forwarded_text_sha256": "text",
            "source_token_ids_sha256": "ids",
            "forwarded_token_ids_sha256": "ids",
            "source_finish_reason": "stop",
            "forwarded_finish_reason": "stop",
            "source_stop_reason": "eos",
            "forwarded_stop_reason": "eos",
            "raw_finished": True,
            "forwarded_finished": True,
            "terminal_origin": "source",
            "final_terminal_passthrough": True,
            "synthetic_terminal": False,
            "terminal_flush": False,
        }]

        analysis = classify(summary, records)

        self.assertEqual(analysis["diagnosis"], "pass")
        self.assertEqual(analysis["terminal_flush_count"], 0)
        self.assertEqual(analysis["final_source_terminal_count"], 1)
        self.assertEqual(analysis["final_terminal_passthrough_count"], 1)
        self.assertEqual(analysis["synthetic_terminal_count"], 0)
        self.assertEqual(analysis["terminal_contract_mode"], "source_terminal")
        self.assertTrue(analysis["terminal_contract_valid"])
        self.assertEqual(
            analysis["terminal_passthrough_violation_sequences"], [])

    def test_analysis_accepts_production_schema_one_source_terminal(
        self,
    ) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [
            {
                "schema": 1,
                "source": "controller_pre_parser",
                "event": "delta",
                "phase": "final_answer",
                "sequence": 1,
                "completion_epoch": 1,
                "raw_text": "<tool_call>",
                "raw_text_sha256": "text",
                "forwarded_text_sha256": "text",
                "source_token_ids_sha256": "ids",
                "forwarded_token_ids_sha256": "ids",
                "terminal_flush": False,
                "final_terminal_passthrough": False,
                "synthetic_terminal": False,
            },
            {
                "schema": 1,
                "source": "controller_pre_parser",
                "event": "final_terminal_passthrough",
                "phase": "final_answer",
                "sequence": 2,
                "completion_epoch": 1,
                "raw_text": "</tool_call>",
                "raw_text_sha256": "terminal-text",
                "forwarded_text_sha256": "terminal-text",
                "source_token_ids_sha256": "terminal-ids",
                "forwarded_token_ids_sha256": "terminal-ids",
                "source_finish_reason": "stop",
                "forwarded_finish_reason": "stop",
                "source_stop_reason": None,
                "forwarded_stop_reason": None,
                "raw_finished": True,
                "forwarded_finished": True,
                "terminal_origin": "source",
                "final_terminal_passthrough": True,
                "synthetic_terminal": False,
                "terminal_flush": False,
            },
        ]

        analysis = classify(summary, records)

        self.assertEqual(analysis["diagnosis"], "pass")
        self.assertEqual(analysis["terminal_contract_mode"], "source_terminal")
        self.assertEqual(analysis["terminal_flush_count"], 0)
        self.assertTrue(analysis["terminal_contract_valid"])
        self.assertEqual(
            analysis["controller_passthrough_violation_sequences"], [])

    def test_analysis_checks_production_schema_one_delta_digests(self) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [
            {
                "schema": 1,
                "source": "controller_pre_parser",
                "event": "delta",
                "phase": "final_answer",
                "sequence": 1,
                "completion_epoch": 1,
                "raw_text": "<tool_call>",
                "raw_text_sha256": "source",
                "forwarded_text_sha256": "mutated",
                "source_token_ids_sha256": "ids",
                "forwarded_token_ids_sha256": "ids",
                "terminal_flush": False,
            },
            {
                "schema": 1,
                "source": "controller_pre_parser",
                "event": "final_terminal_passthrough",
                "phase": "final_answer",
                "sequence": 2,
                "completion_epoch": 1,
                "raw_text_sha256": "terminal-text",
                "forwarded_text_sha256": "terminal-text",
                "source_token_ids_sha256": "terminal-ids",
                "forwarded_token_ids_sha256": "terminal-ids",
                "source_finish_reason": "stop",
                "forwarded_finish_reason": "stop",
                "source_stop_reason": None,
                "forwarded_stop_reason": None,
                "raw_finished": True,
                "forwarded_finished": True,
                "terminal_origin": "source",
                "final_terminal_passthrough": True,
                "synthetic_terminal": False,
                "terminal_flush": False,
            },
        ]

        analysis = classify(summary, records)

        self.assertEqual(
            analysis["diagnosis"], "controller_passthrough_violation")
        self.assertEqual(
            analysis["controller_passthrough_violation_sequences"], [1])

    def test_analysis_rejects_mutated_schema_four_terminal(self) -> None:
        summary = {
            "client_request_id": "r",
            "server_request_id": "r",
            "variant": "dtb",
            "has_tool_call": True,
            "anomaly_reasons": [],
        }
        records = [{
            "schema": 4,
            "event": "final_terminal_passthrough",
            "phase": "final_answer",
            "sequence": 1,
            "completion_epoch": 1,
            "raw_text_sha256": "source",
            "forwarded_text_sha256": "mutated",
            "source_token_ids_sha256": "ids",
            "forwarded_token_ids_sha256": "ids",
            "source_finish_reason": "stop",
            "forwarded_finish_reason": "stop",
            "source_stop_reason": "eos",
            "forwarded_stop_reason": "eos",
            "raw_finished": True,
            "forwarded_finished": True,
            "terminal_origin": "source",
            "final_terminal_passthrough": True,
            "synthetic_terminal": False,
        }]

        analysis = classify(summary, records)

        self.assertEqual(
            analysis["diagnosis"], "controller_terminal_binding_failure")
        self.assertFalse(analysis["terminal_contract_valid"])
        self.assertEqual(
            analysis["terminal_passthrough_violation_sequences"], [1])

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
