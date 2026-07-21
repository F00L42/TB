from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from think_budget_vllm_adapter.telemetry import TimingCollector
from tools.extract_tb_phase_metrics import (
    parse_phase_records,
    summarize_phases,
    summarize_requests,
)


class TimingCollectorTests(unittest.TestCase):
    def test_phase_record_uses_vllm_request_metrics(self) -> None:
        output = SimpleNamespace(
            metrics=SimpleNamespace(
                queued_ts=10.0,
                scheduled_ts=10.25,
                first_token_ts=10.75,
                last_token_ts=11.25,
            ),
            num_cached_tokens=96,
            outputs=[SimpleNamespace(stop_reason="length", finish_reason="length")],
        )
        collector = TimingCollector(emit_phase_metrics=False)

        record = collector.record_phase(
            parent_request_id="chatcmpl-parent",
            child_request_id="chatcmpl-parent_trial_answer_2",
            phase="confidence_probe",
            round_id=1,
            wall_ms=1300.0,
            controller_cpu_ms=4.5,
            prompt_tokens=128,
            generated_tokens=50,
            visible=False,
            output=output,
            confidence=0.91234567,
            accepted=False,
        )

        self.assertEqual(record["queue_ms"], 250.0)
        self.assertEqual(record["prefill_ms"], 500.0)
        self.assertEqual(record["decode_ms"], 500.0)
        self.assertEqual(record["cached_prompt_tokens"], 96)
        self.assertEqual(record["confidence"], 0.912346)
        self.assertEqual(
            collector.snapshot()["ledger"],
            {
                "visible_think_tokens": 0,
                "hidden_probe_tokens": 50,
                "final_answer_tokens": 0,
                "total_model_generated_tokens": 50,
                "generate_calls": 1,
            },
        )

    def test_missing_vllm_stats_are_null_not_zero(self) -> None:
        collector = TimingCollector(emit_phase_metrics=False)
        record = collector.record_phase(
            parent_request_id="p",
            child_request_id="c",
            phase="initial_think",
            round_id=0,
            wall_ms=1.0,
            controller_cpu_ms=0.1,
            prompt_tokens=4,
            generated_tokens=2,
            visible=True,
        )
        self.assertIsNone(record["queue_ms"])
        self.assertIsNone(record["prefill_ms"])
        self.assertIsNone(record["decode_ms"])
        self.assertIsNone(record["cached_prompt_tokens"])

    def test_emitted_phase_is_single_parseable_json_line(self) -> None:
        collector = TimingCollector(emit_phase_metrics=True)
        stream = io.StringIO()
        with redirect_stdout(stream):
            collector.record_phase(
                parent_request_id="p",
                child_request_id="",
                phase="decision",
                round_id=0,
                wall_ms=2.0,
                controller_cpu_ms=2.0,
                prompt_tokens=10,
                generated_tokens=0,
                visible=False,
            )
        line = stream.getvalue().strip()
        self.assertTrue(line.startswith("TB_PHASE "))
        self.assertEqual(json.loads(line.removeprefix("TB_PHASE "))["phase"], "decision")


class PhaseLogExtractionTests(unittest.TestCase):
    def test_extracts_timestamp_prefixed_phase_lines_and_summarizes_hidden_work(self) -> None:
        records = [
            {
                "schema": 1,
                "parent_request_id": "p",
                "child_request_id": "p_rethink_1",
                "phase": "iterative_think",
                "round": 1,
                "wall_ms": 10.0,
                "queue_ms": 1.0,
                "prefill_ms": 2.0,
                "decode_ms": 7.0,
                "prompt_tokens": 100,
                "cached_prompt_tokens": 96,
                "generated_tokens": 4,
                "visible": True,
                "accepted": None,
            },
            {
                "schema": 1,
                "parent_request_id": "p",
                "child_request_id": "p_trial_2",
                "phase": "confidence_probe",
                "round": 1,
                "wall_ms": 20.0,
                "queue_ms": None,
                "prefill_ms": None,
                "decode_ms": None,
                "prompt_tokens": 110,
                "cached_prompt_tokens": 96,
                "generated_tokens": 8,
                "visible": False,
                "accepted": True,
            },
        ]
        lines = [
            "ignored\n",
            "2026-07-21 INFO TB_PHASE " + json.dumps(records[0]) + "\n",
            "TB_PHASE " + json.dumps(records[1]) + "\n",
        ]

        parsed = parse_phase_records(lines)
        requests = summarize_requests(parsed)
        phases = {row["phase"]: row for row in summarize_phases(parsed)}

        self.assertEqual(len(parsed), 2)
        self.assertEqual(requests[0]["visible_think_tokens"], 4)
        self.assertEqual(requests[0]["hidden_probe_tokens"], 8)
        self.assertEqual(requests[0]["accepted_probe_round"], 1)
        self.assertEqual(phases["confidence_probe"]["generated_tokens_total"], 8)
        self.assertEqual(phases["confidence_probe"]["queue_ms_known"], 0)


if __name__ == "__main__":
    unittest.main()
