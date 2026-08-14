#!/usr/bin/env python3
"""Correlate replay summaries with TB_TOOL_STREAM records from server.log."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PREFIX = "TB_TOOL_STREAM "


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as source:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            rows.append(value)
    return rows


def extract_server_records(
    server_log: Path,
    request_ids: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid = 0
    with server_log.open("r", encoding="utf-8", errors="replace") as source:
        for line_no, line in enumerate(source, start=1):
            marker = line.find(PREFIX)
            if marker < 0:
                continue
            payload = line[marker + len(PREFIX):].strip()
            try:
                record = json.loads(payload)
            except json.JSONDecodeError:
                invalid += 1
                continue
            request_id = str(record.get("parent_request_id") or "")
            if request_id in request_ids:
                record["server_log_line"] = line_no
                grouped[request_id].append(record)
    for records in grouped.values():
        records.sort(key=lambda row: int(row.get("sequence") or 0))
    return grouped, invalid


def classify(
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    visible_records = [
        row for row in records
        if row.get("event") in {
            "delta", "terminal_body", "synthetic_boundary",
            "terminal_flush", "error_terminal",
        }
        or (row.get("schema") == 1 and row.get("phase") != "controller_end")
    ]
    raw_text = "".join(
        str(row.get("raw_text") or "") for row in visible_records
        if not row.get("terminal_flush")
    )
    raw_has_start = "<tool_call>" in raw_text
    sse_has_tool_call = bool(summary.get("has_tool_call"))
    anomaly_reasons = list(summary.get("anomaly_reasons") or [])

    boundary_records = [
        row for row in records
        if row.get("reasoning_boundary") in {"think_end", "tool_call"}
        and (
            row.get("event") == "synthetic_boundary"
            or row.get("controller_state") == "thinking"
        )
        and row.get("next_state") == "free_final_answer"
    ]
    first_boundary = boundary_records[0] if boundary_records else None
    first_boundary_sequence = int(
        (first_boundary or {}).get("sequence") or 0)
    tool_boundary = next(
        (
            row for row in boundary_records
            if row.get("reasoning_boundary") == "tool_call"
        ),
        None,
    )
    tool_boundary_sequence = int((tool_boundary or {}).get("sequence") or 0)

    state_entries = [row for row in records if row.get("event") == "state_enter"]
    thinking_or_probe_after_tool_boundary = sum(
        tool_boundary_sequence > 0
        and int(row.get("sequence") or 0) > tool_boundary_sequence
        and row.get("phase") in {
            "_ThinkingState", "_ConfidenceProbeState",
        }
        for row in state_entries
    )
    fresh_final_after_tool_boundary = sum(
        tool_boundary_sequence > 0
        and int(row.get("sequence") or 0) > tool_boundary_sequence
        and row.get("phase") == "_FinalAnswerState"
        and row.get("reasoning_boundary") != "tool_call"
        for row in state_entries
    )
    terminal_flush_count = sum(bool(row.get("terminal_flush")) for row in records)
    completion_epochs = {
        int(row["completion_epoch"])
        for row in records
        if isinstance(row.get("completion_epoch"), int)
    }
    completion_epoch_count = len(completion_epochs)
    duplicate_reasoning_boundary = max(0, len(boundary_records) - 1)
    passthrough_violations = [
        int(row.get("sequence") or 0)
        for row in records
        if row.get("schema") == 2
        and row.get("event") in {"delta", "terminal_body"}
        and not row.get("terminal_flush")
        and (
            row.get("raw_text_sha256") != row.get("forwarded_text_sha256")
            or row.get("source_token_ids_sha256")
            != row.get("forwarded_token_ids_sha256")
        )
    ]
    lifecycle_failures = (
        terminal_flush_count != 1
        or completion_epoch_count != 1
        or duplicate_reasoning_boundary > 0
        or fresh_final_after_tool_boundary > 0
        or thinking_or_probe_after_tool_boundary > 0
    )

    if summary.get("variant") == "vanilla":
        diagnosis = (
            "vanilla_pass" if not anomaly_reasons else "vanilla_sse_failure"
        )
    elif not records:
        diagnosis = "missing_server_trace"
    elif passthrough_violations:
        diagnosis = "controller_passthrough_violation"
    elif lifecycle_failures:
        diagnosis = "controller_completion_lifecycle_failure"
    elif sse_has_tool_call and not anomaly_reasons:
        diagnosis = "pass"
    elif tool_boundary is not None and not raw_has_start:
        diagnosis = "controller_input_text_token_gap"
    elif raw_has_start and (not sse_has_tool_call or anomaly_reasons):
        diagnosis = "downstream_parser_or_finish_failure"
    elif not raw_has_start and not sse_has_tool_call:
        diagnosis = "model_or_controller_missing_tool_prefix"
    else:
        diagnosis = "other_stream_contract_failure"

    children: dict[str, dict[str, Any]] = {}
    for row in visible_records:
        child_request_id = str(row.get("child_request_id") or "")
        if not child_request_id:
            continue
        child = children.setdefault(child_request_id, {
            "child_request_id": child_request_id,
            "phases": [],
            "sequences": [],
            "raw_text": "",
            "source_finish_reasons": [],
            "source_stop_reasons": [],
        })
        phase = str(row.get("phase") or "")
        if phase and phase not in child["phases"]:
            child["phases"].append(phase)
        child["sequences"].append(row.get("sequence"))
        if not row.get("terminal_flush"):
            child["raw_text"] += str(row.get("raw_text") or "")
        if row.get("source_finish_reason") is not None:
            child["source_finish_reasons"].append(
                row.get("source_finish_reason"))
        if row.get("source_stop_reason") is not None:
            child["source_stop_reasons"].append(row.get("source_stop_reason"))

    return {
        "client_request_id": summary.get("client_request_id"),
        "server_request_id": summary.get("server_request_id"),
        "line_no": summary.get("line_no"),
        "attempt": summary.get("attempt"),
        "variant": summary.get("variant"),
        "anomaly_reasons": anomaly_reasons,
        "diagnosis": diagnosis,
        "server_record_count": len(records),
        "visible_record_count": len(visible_records),
        "raw_has_tool_call_start": raw_has_start,
        "raw_visible_text": raw_text,
        "first_boundary_sequence": first_boundary_sequence,
        "reasoning_boundaries": [
            {
                "sequence": row.get("sequence"),
                "boundary": row.get("reasoning_boundary"),
                "phase": row.get("phase"),
            }
            for row in boundary_records
        ],
        "completion_epoch_count": completion_epoch_count,
        "terminal_flush_count": terminal_flush_count,
        "duplicate_reasoning_boundary": duplicate_reasoning_boundary,
        "fresh_final_after_tool_boundary": fresh_final_after_tool_boundary,
        "thinking_or_probe_after_tool_boundary": (
            thinking_or_probe_after_tool_boundary),
        "controller_passthrough_violation_sequences": passthrough_violations,
        "children": list(children.values()),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("server_log", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary_path = args.run_dir / "summary.jsonl"
    summaries = read_jsonl(summary_path)
    request_ids = {
        str(row.get("server_request_id") or row.get("client_request_id") or "")
        for row in summaries
    }
    records, invalid = extract_server_records(args.server_log, request_ids)
    analyses = []
    for summary in summaries:
        request_id = str(
            summary.get("server_request_id")
            or summary.get("client_request_id")
            or ""
        )
        analysis = classify(summary, records.get(request_id, []))
        analyses.append(analysis)
        print(
            f"request_id={request_id} variant={analysis['variant']} "
            f"diagnosis={analysis['diagnosis']} "
            f"epochs={analysis['completion_epoch_count']} "
            f"terminal_flushes={analysis['terminal_flush_count']}"
        )

    output_path = args.output or args.run_dir / "analysis.json"
    dtb_analyses = [row for row in analyses if row["variant"] == "dtb"]
    payload = {
        "summary": {
            "requests": len(analyses),
            "dtb_requests": len(dtb_analyses),
            "server_records": sum(len(value) for value in records.values()),
            "invalid_server_records": invalid,
            "diagnoses": {
                diagnosis: sum(
                    row["diagnosis"] == diagnosis for row in analyses
                )
                for diagnosis in sorted({row["diagnosis"] for row in analyses})
            },
            "dtb_acceptance": {
                "pass": sum(
                    row["diagnosis"] == "pass" for row in dtb_analyses),
                "completion_epoch_one": sum(
                    row["completion_epoch_count"] == 1
                    for row in dtb_analyses
                ),
                "terminal_flush_one": sum(
                    row["terminal_flush_count"] == 1
                    for row in dtb_analyses
                ),
                "no_fresh_final_after_tool_boundary": sum(
                    row["fresh_final_after_tool_boundary"] == 0
                    for row in dtb_analyses
                ),
                "no_thinking_or_probe_after_tool_boundary": sum(
                    row["thinking_or_probe_after_tool_boundary"] == 0
                    for row in dtb_analyses
                ),
                "no_passthrough_violation": sum(
                    not row["controller_passthrough_violation_sequences"]
                    for row in dtb_analyses
                ),
            },
        },
        "requests": analyses,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output_path}")
    accepted = {"pass", "vanilla_pass"}
    return 1 if any(
        row["diagnosis"] not in accepted for row in analyses
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
