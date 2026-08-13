from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from think_budget_vllm_adapter.telemetry import TimingCollector
from tools.extract_tb_phase_metrics import (
    load_test_result_specs,
    main as extract_phase_metrics,
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
            confidence_method="value_min",
            average_confidence=0.92345678,
            value_min_raw=-0.23456789,
            pair_count=50,
            value_pair_count=37,
            accept_reason="below_threshold",
        )

        self.assertEqual(record["queue_ms"], 250.0)
        self.assertEqual(record["prefill_ms"], 500.0)
        self.assertEqual(record["decode_ms"], 500.0)
        self.assertEqual(record["cached_prompt_tokens"], 96)
        self.assertEqual(record["confidence"], 0.912346)
        self.assertEqual(record["confidence_method"], "value_min")
        self.assertEqual(record["average_confidence"], 0.923457)
        self.assertEqual(record["value_min_raw"], -0.234568)
        self.assertEqual(record["pair_count"], 50)
        self.assertEqual(record["value_pair_count"], 37)
        self.assertEqual(record["accept_reason"], "below_threshold")
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

    def test_final_phase_records_answer_reason(self) -> None:
        record = TimingCollector(
            emit_phase_metrics=False,
        ).record_phase(
            parent_request_id="p",
            child_request_id="p_final_1",
            phase="final_answer",
            round_id=1,
            wall_ms=3.0,
            controller_cpu_ms=0.2,
            prompt_tokens=20,
            generated_tokens=4,
            visible=True,
            final_answer_reason="budget_exhausted",
        )
        self.assertEqual(
            record["final_answer_reason"], "budget_exhausted")

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

    def test_decision_record_persists_effective_budget_plan(self) -> None:
        plan = {
            "scene": "planning",
            "scene_matched": True,
            "decision_branch": "fixed",
            "budget": {
                "check_length": 1024,
                "min_think_budget": 100,
                "num_thinking": 4,
                "think_stride": 512,
            },
            "confidence_threshold": 0.9,
            "source_by_field": {
                "check_length": {
                    "source": "fixed_check_length",
                    "final": 1024,
                },
            },
        }
        record = TimingCollector(
            emit_phase_metrics=False,
        ).record_phase(
            parent_request_id="p",
            child_request_id="",
            phase="decision",
            round_id=0,
            wall_ms=2.0,
            controller_cpu_ms=2.0,
            prompt_tokens=10,
            generated_tokens=0,
            visible=False,
            budget_plan=plan,
            decision_details={
                "route": {
                    "candidate_scene": "planning",
                    "top_score": 6.0,
                    "score_margin": 3.0,
                    "reject_reason": None,
                },
            },
        )

        self.assertEqual(record["schema"], 2)
        self.assertEqual(record["scene"], "planning")
        self.assertTrue(record["scene_matched"])
        self.assertEqual(record["decision_branch"], "fixed")
        self.assertEqual(record["budget_plan"], plan)
        self.assertEqual(
            record["decision_details"]["route"]["top_score"], 6.0)


class PhaseLogExtractionTests(unittest.TestCase):
    def test_parser_remains_compatible_with_schema_one(self) -> None:
        parsed = parse_phase_records([
            "TB_PHASE "
            + json.dumps({
                "schema": 1,
                "parent_request_id": "legacy",
                "phase": "decision",
            })
            + "\n",
        ])
        self.assertEqual(parsed[0]["schema"], 1)

    def test_extracts_timestamp_prefixed_phase_lines_and_summarizes_hidden_work(self) -> None:
        records = [
            {
                "schema": 2,
                "parent_request_id": "p",
                "child_request_id": "",
                "phase": "decision",
                "round": 0,
                "wall_ms": 2.0,
                "queue_ms": None,
                "prefill_ms": None,
                "decode_ms": None,
                "prompt_tokens": 90,
                "cached_prompt_tokens": None,
                "generated_tokens": 0,
                "visible": False,
                "scene": "planning",
                "scene_matched": True,
                "decision_branch": "fixed",
                "budget_plan": {
                    "scene": "planning",
                    "scene_matched": True,
                    "decision_branch": "fixed",
                    "source_by_field": {
                        "check_length": {
                            "source": "fixed_check_length",
                            "final": 1024,
                        },
                    },
                },
                "decision_details": {
                    "route": {
                        "candidate_scene": "planning",
                        "top_score": 6.0,
                        "score_margin": 3.0,
                        "reject_reason": None,
                    },
                },
            },
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
                "confidence": 0.91,
                "confidence_method": "average_fallback",
                "average_confidence": 0.91,
                "value_min_raw": None,
                "pair_count": 8,
                "value_pair_count": 6,
                "accept_reason": "average_fallback",
            },
            {
                "schema": 2,
                "parent_request_id": "p",
                "child_request_id": "p_final_3",
                "phase": "final_answer",
                "round": 1,
                "wall_ms": 5.0,
                "queue_ms": None,
                "prefill_ms": None,
                "decode_ms": None,
                "prompt_tokens": 120,
                "cached_prompt_tokens": 96,
                "generated_tokens": 3,
                "visible": True,
                "final_answer_reason": "confidence_accepted",
            },
        ]
        lines = [
            "ignored\n",
            "2026-07-21 INFO TB_PHASE " + json.dumps(records[0]) + "\n",
            "TB_PHASE " + json.dumps(records[1]) + "\n",
            "TB_PHASE " + json.dumps(records[2]) + "\n",
            "TB_PHASE " + json.dumps(records[3]) + "\n",
        ]

        parsed = parse_phase_records(lines)
        requests = summarize_requests(parsed)
        phases = {row["phase"]: row for row in summarize_phases(parsed)}

        self.assertEqual(len(parsed), 4)
        self.assertEqual(requests[0]["visible_think_tokens"], 4)
        self.assertEqual(requests[0]["hidden_probe_tokens"], 8)
        self.assertEqual(requests[0]["accepted_probe_round"], 1)
        self.assertEqual(
            requests[0]["final_answer_reason"],
            "confidence_accepted",
        )
        self.assertEqual(requests[0]["final_answer_tokens"], 3)
        self.assertEqual(requests[0]["scene"], "planning")
        self.assertTrue(requests[0]["scene_matched"])
        self.assertEqual(requests[0]["decision_branch"], "fixed")
        self.assertEqual(
            requests[0]["decision_details"]["route"]["top_score"], 6.0)
        self.assertEqual(
            requests[0]["budget_plan"]["source_by_field"]["check_length"]["source"],
            "fixed_check_length",
        )
        self.assertEqual(
            requests[0]["probe_assessments"],
            [{
                "round": 1,
                "confidence": 0.91,
                "confidence_method": "average_fallback",
                "average_confidence": 0.91,
                "value_min_raw": None,
                "pair_count": 8,
                "value_pair_count": 6,
                "accepted": True,
                "accept_reason": "average_fallback",
            }],
        )
        self.assertEqual(phases["confidence_probe"]["generated_tokens_total"], 8)
        self.assertEqual(phases["confidence_probe"]["queue_ms_known"], 0)

    def test_request_id_bound_extraction_isolates_results_and_excludes_judge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "run_04"
            test_results_dir = run_dir / "test_results"
            test_results_dir.mkdir(parents=True)
            result_a = test_results_dir / "dtb_scene_a.csv"
            result_b = test_results_dir / "dtb_scene_b.csv"
            summary = test_results_dir / "_SUMMARY.csv"
            server_log = run_dir / "server.log"

            for path, request_ids in (
                (result_a, ["request-a1", "request-a2"]),
                (result_b, ["request-b1"]),
            ):
                with path.open("w", encoding="utf-8-sig", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=["request_id", "耗时(s)"])
                    writer.writeheader()
                    for request_id in request_ids:
                        writer.writerow({"request_id": request_id, "耗时(s)": "1.0"})

            with summary.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["结果文件名", "平均时延"])
                writer.writeheader()
                writer.writerow({"结果文件名": result_a.name, "平均时延": "1.0"})
                writer.writerow({"结果文件名": result_b.name, "平均时延": "1.0"})

            def phase(request_id: str, phase_name: str = "decision") -> str:
                return "TB_PHASE " + json.dumps({
                    "schema": 2,
                    "parent_request_id": request_id,
                    "phase": phase_name,
                    "wall_ms": 1.0,
                    "generated_tokens": 0,
                })

            server_log.write_text("\n".join([
                phase("old-request"),
                "TB_ROUTE event=selected request_id=request-a1 controller=think_budget",
                phase("request-a1"),
                phase("judge-request", "final_answer"),
                phase("request-a2", "final_answer"),
                "TB_PROFILE request_id=request-a2 profile={}",
                phase("request-b1"),
            ]) + "\n", encoding="utf-8")

            stream = io.StringIO()
            with redirect_stdout(stream):
                extract_phase_metrics([str(run_dir)])

            manifest = json.loads(
                (run_dir / "extraction_manifest.json").read_text(
                    encoding="utf-8"))
            result_manifest = {
                row["result_file"]: row for row in manifest["results"]
            }
            extracted_a = run_dir / "extracted" / result_a.stem
            phases_a = [
                json.loads(line)
                for line in (extracted_a / "controller_phases.jsonl").read_text(
                    encoding="utf-8").splitlines()
            ]

            self.assertEqual(manifest["total_phase_records_in_log"], 5)
            self.assertEqual(manifest["selected_phase_records"], 3)
            self.assertEqual(manifest["excluded_phase_records"], 2)
            self.assertEqual(
                {row["parent_request_id"] for row in phases_a},
                {"request-a1", "request-a2"},
            )
            self.assertEqual(
                result_manifest[result_a.name]["first_matching_log_line"], 2)
            self.assertEqual(
                result_manifest[result_a.name]["last_matching_log_line"], 6)
            self.assertEqual(
                result_manifest[result_a.name]["interleaved_other_phase_records"],
                1,
            )
            self.assertEqual(manifest["server_log"], "server.log")
            self.assertEqual(manifest["summary"], "test_results/_SUMMARY.csv")
            self.assertEqual(
                result_manifest[result_a.name]["source_path"],
                "test_results/dtb_scene_a.csv",
            )
            self.assertFalse((run_dir / "phase_summary.csv").exists())
            self.assertIn("excluded 2 unrelated phases", stream.getvalue())

    def test_duplicate_request_id_across_result_files_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_paths = [root / "scene_a.csv", root / "scene_b.csv"]
            for path in result_paths:
                with path.open("w", encoding="utf-8", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=["request_id"])
                    writer.writeheader()
                    writer.writerow({"request_id": "same-request"})
            summary = root / "_SUMMARY.csv"
            with summary.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["结果文件名"])
                writer.writeheader()
                for path in result_paths:
                    writer.writerow({"结果文件名": path.name})

            with self.assertRaisesRegex(ValueError, "belongs to both"):
                load_test_result_specs(root, summary)

    def test_summary_alone_resolves_details_from_archived_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archived = root / "archived"
            archived.mkdir()
            result = archived / "dtb_scene.csv"
            with result.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["request_id"])
                writer.writeheader()
                writer.writerow({"request_id": "request-1"})
            summary = root / "_SUMMARY.csv"
            with summary.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["结果文件名"])
                writer.writeheader()
                writer.writerow({"结果文件名": result.name})

            specs = load_test_result_specs(root, summary)

            self.assertEqual([spec.source_path for spec in specs], [result.resolve()])
            self.assertEqual(specs[0].request_rows, {"request-1": 2})


if __name__ == "__main__":
    unittest.main()
