#!/usr/bin/env python3
"""Correlate replay summaries with DTB stream-audit records."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PREFIXES = ("DTB_STREAM_AUDIT ", "TB_TOOL_STREAM ")
PRODUCTION_STREAM_AUDIT_SOURCE = "controller_pre_parser"


def has_bound_reasoning_protocol(record: dict[str, Any]) -> bool:
    bindings = record.get("reasoning_protocol_bindings")
    required = {
        "tokenizer_class",
        "reasoning_parser_class",
        "response_parser_class",
        "tool_parser_class",
    }
    return (
        isinstance(bindings, dict)
        and required.issubset(bindings)
        and all(
            isinstance(bindings[name], str)
            and bindings[name] not in {"", "none", "unknown"}
            for name in required
        )
    )


def uses_source_terminal_contract(records: list[dict[str, Any]]) -> bool:
    """Detect audit formats that bind completion to the source terminal.

    Historical ``TB_TOOL_STREAM`` introduced this contract in schema 4.  The
    production ``DTB_STREAM_AUDIT`` restarted its schema at 1 and identifies
    itself with ``source=controller_pre_parser``.
    """
    return any(
        row.get("source") == PRODUCTION_STREAM_AUDIT_SOURCE
        or (
            isinstance(row.get("schema"), int)
            and row["schema"] >= 4
        )
        for row in records
    )


def supports_passthrough_digests(record: dict[str, Any]) -> bool:
    """Return whether a record carries the raw/forwarded digest contract."""
    return (
        record.get("source") == PRODUCTION_STREAM_AUDIT_SOURCE
        or (
            isinstance(record.get("schema"), int)
            and record["schema"] >= 2
        )
    )


def is_valid_stop_boundary_alignment(record: dict[str, Any]) -> bool:
    if not record.get("stop_boundary_aligned"):
        return False
    raw_token_ids = record.get("token_ids")
    forwarded_token_ids = record.get("forwarded_token_ids")
    dropped_token_ids = record.get("alignment_dropped_token_ids")
    if not all(isinstance(value, list) for value in (
        raw_token_ids, forwarded_token_ids, dropped_token_ids,
    )):
        return False
    return (
        record.get("reasoning_boundary") in {"think_end", "tool_call"}
        and record.get("raw_text_sha256")
        == record.get("forwarded_text_sha256")
        and len(forwarded_token_ids) < len(raw_token_ids)
        and raw_token_ids[:len(forwarded_token_ids)] == forwarded_token_ids
        and raw_token_ids[len(forwarded_token_ids):] == dropped_token_ids
        and len(dropped_token_ids) > 0
        and int(record.get("alignment_dropped_token_count") or 0)
        == len(dropped_token_ids)
    )


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
            prefix = next((item for item in PREFIXES if item in line), None)
            if prefix is None:
                continue
            marker = line.find(prefix)
            payload = line[marker + len(prefix):].strip()
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


def extract_server_record_parts(
    server_logs: list[Path],
    request_ids: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Merge ordered split logs and restore per-request sequence order."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid = 0
    for server_log in server_logs:
        part_records, part_invalid = extract_server_records(
            server_log, request_ids
        )
        invalid += part_invalid
        for request_id, rows in part_records.items():
            grouped[request_id].extend(rows)
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("sequence") or 0))
    return grouped, invalid


def classify(
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    source_terminal_contract = uses_source_terminal_contract(records)
    terminal_contract_mode = (
        "source_terminal" if source_terminal_contract else "terminal_flush"
    )
    visible_records = [
        row for row in records
        if row.get("event") in {
            "delta", "terminal_body", "synthetic_boundary",
            "terminal_flush", "final_terminal_passthrough", "error_terminal",
        }
        or (row.get("schema") == 1 and row.get("phase") != "controller_end")
    ]
    raw_text = "".join(
        str(row.get("raw_text") or "") for row in visible_records
        if not row.get("terminal_flush")
    )
    protocol_boundary_ids: dict[str, int] = {}
    for row in records:
        configured = row.get("reasoning_protocol_boundary_token_ids")
        if not isinstance(configured, dict):
            continue
        for role, token_id in configured.items():
            if isinstance(role, str) and isinstance(token_id, int):
                protocol_boundary_ids[role] = token_id
    think_end_token_id = protocol_boundary_ids.get("think_end")
    tool_call_token_id = protocol_boundary_ids.get("tool_call")
    raw_has_start_text = "<tool_call>" in raw_text
    raw_has_start_token = bool(
        tool_call_token_id is not None
        and any(
            tool_call_token_id in list(row.get("token_ids") or [])
            for row in visible_records
        )
    )
    raw_has_start = raw_has_start_text or raw_has_start_token
    raw_has_complete_call = (
        "</function>" in raw_text and "</tool_call>" in raw_text
    )
    sse_has_tool_call = bool(summary.get("has_tool_call"))
    anomaly_reasons = list(summary.get("anomaly_reasons") or [])
    anomaly_reason_set = set(anomaly_reasons)
    semantic_name_only = (
        bool(anomaly_reason_set)
        and anomaly_reason_set <= {"expected_tool_name_missing"}
        and sse_has_tool_call
    )
    semantic_schema_failure = (
        "invalid_tool_arguments_schema" in anomaly_reason_set
        and "invalid_tool_arguments_json" not in anomaly_reason_set
    )
    final_answer_stopped_on_think_end = any(
        row.get("phase") == "final_answer"
        and row.get("source_stop_reason") in {
            "</think>", think_end_token_id,
        }
        for row in visible_records
    )

    boundary_records = [
        row for row in records
        if row.get("reasoning_boundary") in {"think_end", "tool_call"}
        and (
            row.get("event") == "synthetic_boundary"
            or row.get("controller_state") == "thinking"
        )
        and row.get("next_state") == "free_final_answer"
    ]
    stop_contract_records = [
        row for row in records
        if row.get("configured_stop_token_ids")
    ]
    stop_contract_violations = [
        int(row.get("sequence") or 0)
        for row in stop_contract_records
        if (
            row.get("boundary_stop_mode") != "token"
            or row.get("reasoning_protocol_adapter") != "qwen3"
            or not has_bound_reasoning_protocol(row)
            or not {
                think_end_token_id,
                tool_call_token_id,
            }.issubset(set(row.get("configured_stop_token_ids") or []))
            or "</think>" in (row.get("configured_stop_strings") or [])
            or "<tool_call>" in (row.get("configured_stop_strings") or [])
            or row.get("include_stop_str_in_output") is not False
        )
    ]
    token_protocol_active = bool(protocol_boundary_ids) or any(
        row.get("boundary_stop_mode") == "token" for row in records
    )
    boundary_token_contract_violations: list[int] = []
    if token_protocol_active:
        for row in boundary_records:
            role = row.get("reasoning_boundary")
            expected_id = protocol_boundary_ids.get(str(role))
            forwarded_ids = list(row.get("forwarded_token_ids") or [])
            natural_boundary = row.get("event") != "synthetic_boundary"
            if (
                expected_id is None
                or not forwarded_ids
                or forwarded_ids[-1] != expected_id
                or (
                    natural_boundary
                    and row.get("source_stop_reason") != expected_id
                )
            ):
                boundary_token_contract_violations.append(
                    int(row.get("sequence") or 0)
                )
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
    final_source_terminal_count = sum(
        row.get("event") == "final_terminal_passthrough"
        and row.get("terminal_origin") == "source"
        for row in records
    )
    final_terminal_passthrough_count = sum(
        bool(row.get("final_terminal_passthrough")) for row in records)
    synthetic_terminal_count = sum(
        bool(row.get("synthetic_terminal")) for row in records)
    completion_epochs = {
        int(row["completion_epoch"])
        for row in records
        if isinstance(row.get("completion_epoch"), int)
    }
    completion_epoch_count = len(completion_epochs)
    duplicate_reasoning_boundary = max(0, len(boundary_records) - 1)
    boundary_alignment_records = [
        row for row in records if row.get("stop_boundary_aligned")
    ]
    invalid_boundary_alignments = [
        int(row.get("sequence") or 0)
        for row in boundary_alignment_records
        if not is_valid_stop_boundary_alignment(row)
    ]
    passthrough_violations = [
        int(row.get("sequence") or 0)
        for row in records
        if supports_passthrough_digests(row)
        and row.get("event") in {
            "delta", "terminal_body", "final_terminal_passthrough",
        }
        and not row.get("terminal_flush")
        and (
            row.get("raw_text_sha256") != row.get("forwarded_text_sha256")
            or row.get("source_token_ids_sha256")
            != row.get("forwarded_token_ids_sha256")
        )
        and not is_valid_stop_boundary_alignment(row)
    ]
    terminal_passthrough_violations = [
        int(row.get("sequence") or 0)
        for row in records
        if row.get("event") == "final_terminal_passthrough"
        and (
            row.get("terminal_origin") != "source"
            or not row.get("final_terminal_passthrough")
            or row.get("synthetic_terminal")
            or row.get("raw_text_sha256") != row.get("forwarded_text_sha256")
            or row.get("source_token_ids_sha256")
            != row.get("forwarded_token_ids_sha256")
            or row.get("source_finish_reason")
            != row.get("forwarded_finish_reason")
            or row.get("source_stop_reason")
            != row.get("forwarded_stop_reason")
            or row.get("raw_finished") != row.get("forwarded_finished")
        )
    ]
    terminal_contract_failure = (
        final_source_terminal_count != 1
        or final_terminal_passthrough_count != 1
        or synthetic_terminal_count != 0
        or bool(terminal_passthrough_violations)
    ) if source_terminal_contract else terminal_flush_count != 1
    lifecycle_failures = (
        terminal_contract_failure
        or completion_epoch_count != 1
        or duplicate_reasoning_boundary > 0
        or fresh_final_after_tool_boundary > 0
        or thinking_or_probe_after_tool_boundary > 0
        or bool(stop_contract_violations)
        or bool(boundary_token_contract_violations)
    )

    if summary.get("variant") == "vanilla":
        if not anomaly_reasons:
            diagnosis = "vanilla_pass"
        elif semantic_name_only:
            diagnosis = "vanilla_semantic_tool_choice_mismatch"
        elif semantic_schema_failure:
            diagnosis = "vanilla_tool_call_semantic_schema_failure"
        elif anomaly_reason_set <= {"expected_tool_call_missing"}:
            diagnosis = "vanilla_missing_expected_tool"
        else:
            diagnosis = "vanilla_sse_failure"
    elif not records:
        diagnosis = "missing_server_trace"
    elif invalid_boundary_alignments:
        diagnosis = "controller_stop_boundary_alignment_failure"
    elif terminal_passthrough_violations:
        diagnosis = "controller_terminal_binding_failure"
    elif passthrough_violations:
        diagnosis = "controller_passthrough_violation"
    elif stop_contract_violations or boundary_token_contract_violations:
        diagnosis = "controller_token_boundary_contract_failure"
    elif lifecycle_failures:
        diagnosis = "controller_completion_lifecycle_failure"
    elif sse_has_tool_call and not anomaly_reasons:
        diagnosis = "pass"
    elif semantic_name_only:
        diagnosis = "semantic_tool_choice_mismatch"
    elif semantic_schema_failure:
        diagnosis = "tool_call_semantic_schema_failure"
    elif tool_boundary is not None and not raw_has_start:
        diagnosis = "controller_input_text_token_gap"
    elif (
        "invalid_tool_arguments_json" in anomaly_reason_set
        and raw_has_complete_call
    ):
        diagnosis = "downstream_parser_failure"
    elif (
        "invalid_tool_arguments_json" in anomaly_reason_set
        and raw_has_start
        and final_answer_stopped_on_think_end
    ):
        diagnosis = "controller_final_answer_stop_collision"
    elif (
        "invalid_tool_arguments_json" in anomaly_reason_set
        and raw_has_start
    ):
        diagnosis = "model_or_controller_truncated_tool_xml"
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
            "stream_deltas": [],
            "source_finish_reasons": [],
            "source_stop_reasons": [],
        })
        phase = str(row.get("phase") or "")
        if phase and phase not in child["phases"]:
            child["phases"].append(phase)
        child["sequences"].append(row.get("sequence"))
        if not row.get("terminal_flush"):
            child["raw_text"] += str(row.get("raw_text") or "")
            child["stream_deltas"].append({
                "sequence": row.get("sequence"),
                "phase": row.get("phase"),
                "raw_text": row.get("raw_text"),
                "raw_token_ids": row.get("token_ids"),
                "forwarded_token_ids": row.get("forwarded_token_ids"),
                "stop_boundary_aligned": row.get(
                    "stop_boundary_aligned", False),
            })
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
        "raw_has_tool_call_start_text": raw_has_start_text,
        "raw_has_tool_call_start_token": raw_has_start_token,
        "raw_has_complete_tool_call": raw_has_complete_call,
        "final_answer_stopped_on_think_end": (
            final_answer_stopped_on_think_end
        ),
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
        "reasoning_protocol_boundary_token_ids": protocol_boundary_ids,
        "token_stop_contract_record_count": len(stop_contract_records),
        "token_stop_contract_violation_sequences": stop_contract_violations,
        "boundary_token_contract_violation_sequences": (
            boundary_token_contract_violations
        ),
        "completion_epoch_count": completion_epoch_count,
        "terminal_flush_count": terminal_flush_count,
        "final_source_terminal_count": final_source_terminal_count,
        "final_terminal_passthrough_count": final_terminal_passthrough_count,
        "synthetic_terminal_count": synthetic_terminal_count,
        "terminal_contract_mode": terminal_contract_mode,
        "terminal_contract_valid": not terminal_contract_failure,
        "terminal_passthrough_violation_sequences": (
            terminal_passthrough_violations),
        "duplicate_reasoning_boundary": duplicate_reasoning_boundary,
        "fresh_final_after_tool_boundary": fresh_final_after_tool_boundary,
        "thinking_or_probe_after_tool_boundary": (
            thinking_or_probe_after_tool_boundary),
        "controller_passthrough_violation_sequences": passthrough_violations,
        "invalid_stop_boundary_alignment_sequences": (
            invalid_boundary_alignments),
        "stop_boundary_alignments": [
            {
                "sequence": row.get("sequence"),
                "boundary": row.get("reasoning_boundary"),
                "phase": row.get("phase"),
                "raw_text": row.get("raw_text"),
                "raw_token_ids": row.get("token_ids"),
                "forwarded_token_ids": row.get("forwarded_token_ids"),
                "dropped_token_ids": row.get("alignment_dropped_token_ids"),
            }
            for row in boundary_alignment_records
        ],
        "children": list(children.values()),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "server_logs",
        type=Path,
        nargs="+",
        help="one server log or ordered server_filtered_part_* files",
    )
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
    records, invalid = extract_server_record_parts(
        args.server_logs, request_ids
    )
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
            f"terminal_flushes={analysis['terminal_flush_count']} "
            f"source_terminals={analysis['final_source_terminal_count']} "
            "terminal_passthroughs="
            f"{analysis['final_terminal_passthrough_count']} "
            f"synthetic_terminals={analysis['synthetic_terminal_count']} "
            f"terminal_contract={analysis['terminal_contract_mode']}"
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
                "final_source_terminal_one": sum(
                    row["final_source_terminal_count"] == 1
                    for row in dtb_analyses
                ),
                "final_terminal_passthrough_one": sum(
                    row["final_terminal_passthrough_count"] == 1
                    for row in dtb_analyses
                ),
                "no_synthetic_terminal": sum(
                    row["synthetic_terminal_count"] == 0
                    for row in dtb_analyses
                ),
                "no_terminal_passthrough_violation": sum(
                    not row["terminal_passthrough_violation_sequences"]
                    for row in dtb_analyses
                ),
                "terminal_contract_valid": sum(
                    row["terminal_contract_valid"] for row in dtb_analyses
                ),
                "source_terminal_contract": sum(
                    row["terminal_contract_mode"] == "source_terminal"
                    for row in dtb_analyses
                ),
                "legacy_terminal_flush_contract": sum(
                    row["terminal_contract_mode"] == "terminal_flush"
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
                "no_invalid_stop_boundary_alignment": sum(
                    not row["invalid_stop_boundary_alignment_sequences"]
                    for row in dtb_analyses
                ),
                "token_stop_contract_covered": sum(
                    row["token_stop_contract_record_count"] > 0
                    for row in dtb_analyses
                ),
                "token_stop_contract_valid": sum(
                    row["token_stop_contract_record_count"] > 0
                    and not row["token_stop_contract_violation_sequences"]
                    and not row[
                        "boundary_token_contract_violation_sequences"
                    ]
                    for row in dtb_analyses
                ),
                "stop_boundary_alignment_covered": sum(
                    bool(row["stop_boundary_alignments"])
                    for row in dtb_analyses
                ),
                "think_end_boundary_covered": sum(
                    any(
                        boundary["boundary"] == "think_end"
                        for boundary in row["reasoning_boundaries"]
                    )
                    for row in dtb_analyses
                ),
                "tool_call_boundary_covered": sum(
                    any(
                        boundary["boundary"] == "tool_call"
                        for boundary in row["reasoning_boundaries"]
                    )
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
