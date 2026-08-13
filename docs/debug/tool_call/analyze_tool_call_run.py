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
    final_records = [row for row in records if row.get("phase") == "final_answer"]
    raw_text = "".join(str(row.get("raw_text") or "") for row in final_records)
    parser_text = "".join(
        str(row.get("parser_text") or "") for row in final_records
    )
    decoded_text = "".join(
        str(row.get("decoded_token_text") or "") for row in final_records
    )
    repairs = [
        {
            "sequence": row.get("sequence"),
            "tags": row.get("repaired_tags"),
            "raw_text": row.get("raw_text"),
            "parser_text": row.get("parser_text"),
        }
        for row in final_records
        if row.get("repaired_tags")
    ]
    raw_has_start = "<tool_call>" in raw_text
    parser_has_start = "<tool_call>" in parser_text
    decoded_has_start = "<tool_call>" in decoded_text
    sse_has_tool_call = bool(summary.get("has_tool_call"))
    anomaly_reasons = list(summary.get("anomaly_reasons") or [])

    if not records:
        diagnosis = "missing_server_trace"
    elif sse_has_tool_call and not anomaly_reasons:
        diagnosis = "pass"
    elif not raw_has_start and (decoded_has_start or repairs):
        diagnosis = "controller_text_token_gap"
    elif parser_has_start and not sse_has_tool_call:
        diagnosis = "downstream_parser_or_finish_failure"
    elif not parser_has_start and not sse_has_tool_call:
        diagnosis = "model_or_controller_missing_tool_prefix"
    else:
        diagnosis = "other_stream_contract_failure"

    return {
        "client_request_id": summary.get("client_request_id"),
        "server_request_id": summary.get("server_request_id"),
        "line_no": summary.get("line_no"),
        "attempt": summary.get("attempt"),
        "variant": summary.get("variant"),
        "anomaly_reasons": anomaly_reasons,
        "diagnosis": diagnosis,
        "server_record_count": len(records),
        "final_record_count": len(final_records),
        "raw_has_tool_call_start": raw_has_start,
        "parser_has_tool_call_start": parser_has_start,
        "decoded_has_tool_call_start": decoded_has_start,
        "repair_count": len(repairs),
        "repairs": repairs,
        "raw_final_text": raw_text,
        "parser_final_text": parser_text,
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
            f"diagnosis={analysis['diagnosis']} repairs={analysis['repair_count']}"
        )

    output_path = args.output or args.run_dir / "analysis.json"
    payload = {
        "summary": {
            "requests": len(analyses),
            "server_records": sum(len(value) for value in records.values()),
            "invalid_server_records": invalid,
            "diagnoses": {
                diagnosis: sum(
                    row["diagnosis"] == diagnosis for row in analyses
                )
                for diagnosis in sorted({row["diagnosis"] for row in analyses})
            },
        },
        "requests": analyses,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output_path}")
    return 1 if any(row["diagnosis"] != "pass" for row in analyses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
