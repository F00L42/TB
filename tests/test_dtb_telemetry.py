from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace

from dtb.config.schema import (
    DtbObservabilityConfig,
    DtbTelemetryMode,
    POLICY_VERSION,
)
from dtb.observability.config import DtbObservabilityRuntime
from dtb.observability.event_writer import DtbEventWriter
from dtb.observability.phase_recorder import DtbPhaseRecorder
from dtb.reasoning.request_state import DtbRequestState
from tools.extract_dtb_phase_metrics import (
    parse_phase_records,
    summarize_phases,
    summarize_requests,
)


class DtbTelemetryTests(unittest.TestCase):
    def test_off_mode_creates_no_telemetry_objects(self) -> None:
        runtime = DtbObservabilityRuntime(DtbObservabilityConfig())
        self.assertIsNone(runtime.writer)
        self.assertIsNone(runtime.create_phase_recorder("policy"))
        self.assertIsNone(runtime.create_stream_audit("request"))

    def test_phase_record_uses_vllm_metrics_and_schema_three(self) -> None:
        lines: list[str] = []
        writer = DtbEventWriter(16, lines.append)
        try:
            recorder = DtbPhaseRecorder(writer, POLICY_VERSION)
            output = SimpleNamespace(
                metrics=SimpleNamespace(
                    queued_ts=10.0,
                    scheduled_ts=10.25,
                    first_token_ts=10.75,
                    last_token_ts=11.25,
                ),
                num_cached_tokens=96,
                outputs=[SimpleNamespace(
                    stop_reason="length", finish_reason="length"
                )],
            )
            record = recorder.record_phase(
                parent_request_id="parent",
                child_request_id="parent_probe_1",
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
                confidence_method="value_min",
                average_confidence=0.92345678,
                value_min_raw=-0.23456789,
                pair_count=50,
                value_pair_count=37,
                accept_reason="below_threshold",
            )
            self.assertTrue(writer.flush())
        finally:
            writer.close()
        self.assertEqual(record["schema"], 3)
        self.assertEqual(record["policy_version"], "dtb-algorithm-v3")
        self.assertEqual(record["variant"], "dtb")
        self.assertEqual(record["queue_ms"], 250.0)
        self.assertEqual(record["prefill_ms"], 500.0)
        self.assertEqual(record["decode_ms"], 500.0)
        self.assertEqual(record["confidence"], 0.912346)
        self.assertTrue(lines[0].startswith("DTB_PHASE "))
        self.assertEqual(json.loads(lines[0].split(" ", 1)[1]), record)

    def test_writer_drops_without_blocking_when_queue_is_full(self) -> None:
        sink_entered = threading.Event()
        release_sink = threading.Event()

        def blocking_sink(_: str) -> None:
            sink_entered.set()
            release_sink.wait(2.0)

        writer = DtbEventWriter(1, blocking_sink)
        try:
            self.assertTrue(writer.publish("ONE", {}))
            self.assertTrue(sink_entered.wait(1.0))
            self.assertTrue(writer.publish("TWO", {}))
            self.assertFalse(writer.publish("THREE", {}))
            self.assertEqual(writer.dropped_events, 1)
        finally:
            release_sink.set()
            writer.close()

    def test_missing_vllm_metrics_remain_null(self) -> None:
        writer = DtbEventWriter(8, lambda _line: None)
        try:
            record = DtbPhaseRecorder(writer, "policy").record_phase(
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
        finally:
            writer.close()
        self.assertIsNone(record["queue_ms"])
        self.assertIsNone(record["prefill_ms"])
        self.assertIsNone(record["decode_ms"])
        self.assertIsNone(record["cached_prompt_tokens"])

    def test_request_state_is_the_only_model_work_ledger(self) -> None:
        lines: list[str] = []
        writer = DtbEventWriter(8, lines.append)
        state = DtbRequestState(
            thinking_tokens=4,
            hidden_probe_tokens=5,
            final_answer_tokens=6,
            child_generate_calls=3,
            completion_status="success",
        )
        try:
            recorder = DtbPhaseRecorder(writer, "policy")
            record = recorder.record_request_summary(
                parent_request_id="p", state=state, error=None
            )
            self.assertTrue(writer.flush())
        finally:
            writer.close()
        self.assertEqual(record["total_model_generated_tokens"], 15)
        self.assertEqual(record["child_generate_calls"], 3)
        self.assertTrue(lines[0].startswith("DTB_REQUEST_SUMMARY "))

    def test_stream_audit_requires_allowlisted_request(self) -> None:
        runtime = DtbObservabilityRuntime(DtbObservabilityConfig(
            mode=DtbTelemetryMode.STREAM_AUDIT,
            stream_request_ids=frozenset({"allowed"}),
        ))
        try:
            self.assertIsNone(runtime.create_phase_recorder("policy"))
            self.assertIsNone(runtime.create_stream_audit("other"))
            self.assertIsNotNone(runtime.create_stream_audit("allowed"))
        finally:
            runtime.close()


class PhaseLogExtractionTests(unittest.TestCase):
    def test_parser_accepts_historical_and_new_phase_records(self) -> None:
        parsed = parse_phase_records([
            "TB_PHASE " + json.dumps({
                "schema": 1,
                "parent_request_id": "legacy-1",
                "phase": "decision",
            }) + "\n",
            "2026-08-20 INFO TB_PHASE " + json.dumps({
                "schema": 2,
                "parent_request_id": "legacy-2",
                "phase": "initial_think",
            }) + "\n",
            "DTB_PHASE " + json.dumps({
                "schema": 3,
                "parent_request_id": "new",
                "phase": "confidence_probe",
            }) + "\n",
        ])
        self.assertEqual([item["schema"] for item in parsed], [1, 2, 3])

    def test_summaries_keep_hidden_work_separate(self) -> None:
        records = [
            {
                "schema": 3,
                "parent_request_id": "p",
                "child_request_id": "think",
                "phase": "initial_think",
                "round": 0,
                "wall_ms": 10.0,
                "queue_ms": 1.0,
                "prefill_ms": 2.0,
                "decode_ms": 7.0,
                "controller_cpu_ms": 0.1,
                "prompt_tokens": 20,
                "cached_prompt_tokens": 10,
                "generated_tokens": 5,
                "visible": True,
            },
            {
                "schema": 3,
                "parent_request_id": "p",
                "child_request_id": "probe",
                "phase": "confidence_probe",
                "round": 1,
                "wall_ms": 4.0,
                "queue_ms": 0.5,
                "prefill_ms": 1.0,
                "decode_ms": 2.5,
                "controller_cpu_ms": 0.1,
                "prompt_tokens": 25,
                "cached_prompt_tokens": 20,
                "generated_tokens": 3,
                "visible": False,
            },
        ]
        phase_rows = summarize_phases(records)
        request_rows = summarize_requests(records)
        self.assertEqual(len(phase_rows), 2)
        self.assertEqual(request_rows[0]["visible_think_tokens"], 5)
        self.assertEqual(request_rows[0]["hidden_probe_tokens"], 3)
        self.assertEqual(request_rows[0]["total_model_generated_tokens"], 8)

    def test_request_summary_uses_budget_decision_name(self) -> None:
        rows = summarize_requests([{
            "schema": 3,
            "parent_request_id": "p",
            "phase": "decision",
            "budget_decision": {"scene": "general"},
        }])
        self.assertEqual(
            rows[0]["budget_decision"], {"scene": "general"}
        )
        self.assertNotIn("budget_plan", rows[0])


if __name__ == "__main__":
    unittest.main()
