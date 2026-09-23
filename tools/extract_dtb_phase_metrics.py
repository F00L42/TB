"""Extract and bind DTB phase metrics from one performance run.

Usage:

    python tools/extract_dtb_phase_metrics.py artifacts/perf/run_<id>

The run directory must contain ``server.log`` at its root and exactly one
``_SUMMARY.csv`` somewhere below it. Every concrete result filename referenced
by the summary must also resolve uniquely below the run directory.

In bound mode, result CSV ``request_id`` values are the authoritative allowlist.
Old requests, judge requests, and other variants are excluded even when their
phases are interleaved in the same server log. Only Python's standard library
is required.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


PHASE_MARKERS = ("DTB_PHASE ", "TB_PHASE ")
NUMERIC_TIMING_FIELDS = ("wall_ms", "queue_ms", "prefill_ms", "decode_ms")
FINAL_QUOTA_FIELDS = (
    "completion_token_limit",
    "emitted_completion_tokens_before",
    "pending_boundary_tokens",
    "external_remaining_tokens",
    "answer_limit",
)
SUMMARY_RESULT_COLUMN = "结果文件名"
REQUEST_ID_COLUMN = "request_id"
PLAIN_REQUEST_ID_PATTERN = re.compile(
    r"\b(?:parent_)?request_id=([^\s,}\]]+)")
JSON_REQUEST_ID_PATTERN = re.compile(
    r'"(?:parent_)?request_id"\s*:\s*"([^"\\]+)"')


@dataclass(frozen=True)
class PhaseEntry:
    record: dict[str, object]
    line_number: int


@dataclass
class LogRange:
    first_line: int | None = None
    last_line: int | None = None
    matching_lines: int = 0

    def observe(self, line_number: int) -> None:
        if self.first_line is None:
            self.first_line = line_number
        self.last_line = line_number
        self.matching_lines += 1


@dataclass(frozen=True)
class ClientResultRow:
    row_number: int
    request_id: str


@dataclass(frozen=True)
class TestResultSpec:
    source_path: Path
    result_file: str
    rows: tuple[ClientResultRow, ...]
    summary_row: dict[str, str]

    @property
    def request_rows(self) -> dict[str, int]:
        return {
            row.request_id: row.row_number
            for row in self.rows
            if row.request_id
        }


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _parse_phase_line(line: str, line_number: int) -> PhaseEntry | None:
    marker = next((item for item in PHASE_MARKERS if item in line), None)
    if marker is None:
        return None
    marker_index = line.find(marker)
    payload = line[marker_index + len(marker):].strip()
    try:
        record = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid DTB phase JSON at log line {line_number}") from exc
    if record.get("schema") not in {1, 2, 3}:
        raise ValueError(
            f"unsupported DTB phase schema at log line {line_number}: "
            f"{record.get('schema')!r}")
    if not record.get("parent_request_id"):
        raise ValueError(
            f"DTB phase has no parent_request_id at log line {line_number}")
    if not record.get("phase"):
        raise ValueError(f"DTB phase has no phase at log line {line_number}")
    return PhaseEntry(record=record, line_number=line_number)


def scan_server_log(
    lines: Iterable[str],
    tracked_request_ids: set[str] | None = None,
    *, route_entries: list[PhaseEntry] | None = None,
) -> tuple[list[PhaseEntry], dict[str, LogRange]]:
    """Parse phases and locate exact tracked request IDs in one log pass.

    A range is diagnostic only: filtering always uses exact membership of
    a phase ``parent_request_id`` in ``tracked_request_ids``. Plain
    ``request_id=...`` markers and
    JSON request ID fields extend the reported first/last matching log lines.
    """
    tracked = tracked_request_ids or set()
    ranges = {request_id: LogRange() for request_id in tracked}
    entries: list[PhaseEntry] = []

    for line_number, line in enumerate(lines, 1):
        if route_entries is not None and "DTB_ROUTE " in line:
            try:
                route = json.loads(line.split("DTB_ROUTE ", 1)[1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid DTB route JSON at log line {line_number}") from exc
            if (route.get("schema") != 1 or not route.get("parent_request_id")
                    or route.get("action") not in ("native", "dtb")):
                raise ValueError(f"invalid DTB route at log line {line_number}")
            route_entries.append(PhaseEntry(route, line_number))
        entry = _parse_phase_line(line, line_number)
        matched_ids: set[str] = set()
        if entry is not None:
            entries.append(entry)
            parent_request_id = str(entry.record["parent_request_id"])
            if parent_request_id in tracked:
                matched_ids.add(parent_request_id)

        if tracked:
            for pattern in (PLAIN_REQUEST_ID_PATTERN, JSON_REQUEST_ID_PATTERN):
                for match in pattern.finditer(line):
                    request_id = match.group(1)
                    if request_id in tracked:
                        matched_ids.add(request_id)
            for request_id in matched_ids:
                ranges[request_id].observe(line_number)

    return entries, ranges


def parse_phase_records(lines: Iterable[str]) -> list[dict[str, object]]:
    """Backward-compatible parser returning records without line metadata."""
    entries, _ = scan_server_log(lines)
    return [entry.record for entry in entries]


def summarize_phases(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[str(record["phase"])].append(record)

    summaries: list[dict[str, object]] = []
    for phase, phase_records in sorted(grouped.items()):
        summary: dict[str, object] = {"phase": phase, "calls": len(phase_records)}
        for field in NUMERIC_TIMING_FIELDS:
            values = [
                float(record[field])
                for record in phase_records
                if isinstance(record.get(field), (int, float))
            ]
            summary[f"{field}_known"] = len(values)
            summary[f"{field}_total"] = round(sum(values), 3) if values else None
            summary[f"{field}_mean"] = (
                round(sum(values) / len(values), 3) if values else None)
            summary[f"{field}_p50"] = (
                round(percentile(values, 0.50), 3) if values else None)
            summary[f"{field}_p95"] = (
                round(percentile(values, 0.95), 3) if values else None)
        summary["prompt_tokens_total"] = sum(
            int(record.get("prompt_tokens") or 0) for record in phase_records)
        summary["cached_prompt_tokens_total"] = sum(
            int(record.get("cached_prompt_tokens") or 0) for record in phase_records)
        summary["generated_tokens_total"] = sum(
            int(record.get("generated_tokens") or 0) for record in phase_records)
        for field in FINAL_QUOTA_FIELDS:
            values = [
                int(record[field])
                for record in phase_records
                if isinstance(record.get(field), int)
                and not isinstance(record.get(field), bool)
            ]
            summary[f"{field}_known"] = len(values)
            summary[f"{field}_min"] = min(values) if values else None
            summary[f"{field}_mean"] = (
                round(sum(values) / len(values), 3) if values else None
            )
            summary[f"{field}_max"] = max(values) if values else None
        summaries.append(summary)
    return summaries


def summarize_requests(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[str(record["parent_request_id"])].append(record)

    result: list[dict[str, object]] = []
    for request_id, request_records in sorted(grouped.items()):
        decision = next((
            record for record in request_records
            if record.get("phase") == "decision"
        ), {})
        probe_assessments = [
            {
                "round": record.get("round"),
                "confidence": record.get("confidence"),
                "confidence_method": record.get("confidence_method"),
                "average_confidence": record.get("average_confidence"),
                "value_min_raw": record.get("value_min_raw"),
                "pair_count": record.get("pair_count"),
                "value_pair_count": record.get("value_pair_count"),
                "accepted": record.get("accepted"),
                "accept_reason": record.get("accept_reason"),
            }
            for record in request_records
            if record.get("phase") == "confidence_probe"
        ]
        final_answer = next((
            record for record in request_records
            if record.get("phase") == "final_answer"
        ), {})
        decision_details = decision.get("decision_details")
        observation = next((
            record.get("intervention", {}) for record in request_records
            if record.get("phase") == "native_passthrough"
        ), {})
        decision_completion_limit = (
            decision_details.get("completion_token_limit")
            if isinstance(decision_details, dict) else None
        )
        result.append({
            "schema": decision.get("schema", 1),
            "parent_request_id": request_id,
            "scene": decision.get("scene"),
            "scene_matched": decision.get("scene_matched"),
            "decision_branch": decision.get("decision_branch"),
            "budget_decision": (
                decision.get("budget_decision")
                or decision.get("budget_plan")
            ),
            "decision_details": decision.get("decision_details"),
            "intervention": observation or None,
            "budget_resolution": (
                "resolved" if decision else "not_required_native" if observation else "unknown"
            ),
            "unclassified_completion_tokens": observation.get("unclassified_completion_tokens", 0),
            # Phases are sequential. This is controller-accounted wall time,
            # not HTTP E2E; pair it with the client-side result CSV.
            "accounted_phase_wall_ms": round(sum(
                float(record.get("wall_ms") or 0) for record in request_records), 3),
            "generate_calls": sum(
                1 for record in request_records if record.get("phase") != "decision"),
            "visible_think_tokens": int(observation.get("observed_reasoning_tokens") or 0) + sum(
                int(record.get("generated_tokens") or 0)
                for record in request_records
                if record.get("phase") in {"initial_think", "iterative_think"}
            ),
            "hidden_probe_tokens": sum(
                int(record.get("generated_tokens") or 0)
                for record in request_records
                if record.get("phase") == "confidence_probe"
            ),
            "final_answer_tokens": int(observation.get("observed_final_tokens") or 0) + sum(
                int(record.get("generated_tokens") or 0)
                for record in request_records
                if record.get("phase") == "final_answer"
            ),
            "total_model_generated_tokens": sum(
                int(record.get("generated_tokens") or 0)
                for record in request_records
                if record.get("phase") != "decision"
            ),
            "probe_rounds": sum(
                1 for record in request_records
                if record.get("phase") == "confidence_probe"),
            "accepted_probe_round": next((
                record.get("round") for record in request_records
                if record.get("phase") == "confidence_probe"
                and record.get("accepted") is True
            ), None),
            "final_answer_reason": final_answer.get(
                "final_answer_reason"),
            "completion_token_limit": final_answer.get(
                "completion_token_limit", decision_completion_limit or observation.get("completion_token_limit")
            ),
            "emitted_completion_tokens_before": final_answer.get(
                "emitted_completion_tokens_before"
            ),
            "pending_boundary_tokens": final_answer.get(
                "pending_boundary_tokens"
            ),
            "external_remaining_tokens": final_answer.get(
                "external_remaining_tokens"
            ),
            "answer_limit": final_answer.get("answer_limit"),
            "probe_assessments": probe_assessments,
        })
    return result


def _phase_summary_fieldnames() -> list[str]:
    fields = ["phase", "calls"]
    for field in NUMERIC_TIMING_FIELDS:
        fields.extend((
            f"{field}_known",
            f"{field}_total",
            f"{field}_mean",
            f"{field}_p50",
            f"{field}_p95",
        ))
    fields.extend((
        "prompt_tokens_total",
        "cached_prompt_tokens_total",
        "generated_tokens_total",
    ))
    for field in FINAL_QUOTA_FIELDS:
        fields.extend((
            f"{field}_known",
            f"{field}_min",
            f"{field}_mean",
            f"{field}_max",
        ))
    return fields


def _write_jsonl(path: Path, records: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                + "\n")


def _write_phase_summary(path: Path, records: list[dict[str, object]]) -> None:
    summaries = summarize_phases(records)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=_phase_summary_fieldnames())
        writer.writeheader()
        writer.writerows(summaries)


def _find_column(fieldnames: Sequence[str] | None, requested: str) -> str:
    if not fieldnames:
        raise ValueError("CSV has no header")
    if requested in fieldnames:
        return requested
    normalized = {name.strip(): name for name in fieldnames}
    if requested.strip() in normalized:
        return normalized[requested.strip()]
    raise ValueError(
        f"CSV is missing required column {requested!r}; columns={list(fieldnames)!r}")


def _read_summary(
    path: Path,
    result_column: str,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        actual_column = _find_column(reader.fieldnames, result_column)
        ordered_names: list[str] = []
        rows_by_name: dict[str, dict[str, str]] = {}
        for row_number, row in enumerate(reader, 2):
            result_file = (row.get(actual_column) or "").strip()
            if not result_file:
                continue
            if result_file in rows_by_name:
                raise ValueError(
                    f"duplicate result filename {result_file!r} in {path} "
                    f"at row {row_number}")
            ordered_names.append(result_file)
            rows_by_name[result_file] = dict(row)
    if not ordered_names:
        raise ValueError(f"summary contains no result filenames: {path}")
    return ordered_names, rows_by_name


def _read_test_result(
    path: Path,
    request_id_column: str,
    summary_row: dict[str, str],
) -> TestResultSpec:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        actual_column = _find_column(reader.fieldnames, request_id_column)
        rows: list[ClientResultRow] = []
        seen: dict[str, int] = {}
        for row_number, row in enumerate(reader, 2):
            request_id = (row.get(actual_column) or "").strip()
            if request_id and request_id in seen:
                raise ValueError(
                    f"duplicate request_id {request_id!r} in {path} at rows "
                    f"{seen[request_id]} and {row_number}")
            if request_id:
                seen[request_id] = row_number
            rows.append(ClientResultRow(row_number, request_id))
    if not rows:
        raise ValueError(f"test result contains no data rows: {path}")
    return TestResultSpec(
        source_path=path,
        result_file=path.name,
        rows=tuple(rows),
        summary_row=summary_row,
    )


def _resolve_summary_result(
    result_file: str,
    roots: Sequence[Path],
) -> Path | None:
    direct = [root / result_file for root in roots]
    direct_matches = [path for path in direct if path.is_file()]
    if len({path.resolve() for path in direct_matches}) == 1:
        return direct_matches[0]
    if len({path.resolve() for path in direct_matches}) > 1:
        raise ValueError(
            f"ambiguous result filename {result_file!r}: "
            + ", ".join(str(path) for path in direct_matches))

    recursive_matches: dict[Path, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob(result_file):
            if path.is_file():
                recursive_matches[path.resolve()] = path
    if len(recursive_matches) == 1:
        return next(iter(recursive_matches.values()))
    if len(recursive_matches) > 1:
        raise ValueError(
            f"ambiguous result filename {result_file!r}: "
            + ", ".join(str(path) for path in recursive_matches.values()))
    return None


def load_test_result_specs(
    run_dir: Path,
    summary_path: Path,
) -> list[TestResultSpec]:
    """Load every summary result and validate unambiguous request ownership."""
    summary_order, summary_rows = _read_summary(
        summary_path, SUMMARY_RESULT_COLUMN)
    selected_paths: list[Path] = []
    missing: list[str] = []
    for result_file in summary_order:
        resolved = _resolve_summary_result(result_file, [run_dir])
        if resolved is None:
            missing.append(result_file)
        else:
            selected_paths.append(resolved)
    if missing:
        preview = ", ".join(missing[:5])
        suffix = "" if len(missing) <= 5 else f" (+{len(missing) - 5} more)"
        raise ValueError(
            "could not resolve result files listed by summary: "
            f"{preview}{suffix}; every listed result CSV must exist "
            "uniquely below the run directory")

    names_seen: dict[str, Path] = {}
    specs: list[TestResultSpec] = []
    for raw_path in selected_paths:
        path = raw_path.resolve()
        if not path.is_file():
            raise ValueError(f"test result does not exist: {raw_path}")
        normalized_name = path.name.casefold()
        if normalized_name in names_seen:
            raise ValueError(
                f"duplicate result filename {path.name!r}: "
                f"{names_seen[normalized_name]} and {path}")
        names_seen[normalized_name] = path
        specs.append(_read_test_result(
            path,
            REQUEST_ID_COLUMN,
            summary_rows[path.name],
        ))

    request_owners: dict[str, str] = {}
    for spec in specs:
        for request_id in spec.request_rows:
            previous = request_owners.get(request_id)
            if previous is not None:
                raise ValueError(
                    f"request_id {request_id!r} belongs to both "
                    f"{previous!r} and {spec.result_file!r}")
            request_owners[request_id] = spec.result_file
    return specs


def discover_run_inputs(run_dir: Path) -> tuple[Path, Path]:
    """Resolve the fixed server-log and unique summary inputs for one run."""
    resolved_run_dir = run_dir.resolve()
    if not resolved_run_dir.is_dir():
        raise ValueError(f"run directory does not exist: {run_dir}")

    server_log = resolved_run_dir / "server.log"
    if not server_log.is_file():
        raise ValueError(f"run directory has no server.log: {resolved_run_dir}")

    summaries = sorted(
        path for path in resolved_run_dir.rglob("_SUMMARY.csv")
        if path.is_file()
    )
    if not summaries:
        raise ValueError(
            f"run directory has no _SUMMARY.csv: {resolved_run_dir}")
    if len(summaries) > 1:
        raise ValueError(
            f"run directory contains multiple _SUMMARY.csv files: "
            + ", ".join(str(path) for path in summaries))
    return server_log, summaries[0]


def _run_relative(path: Path, run_dir: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _aggregate_range(
    request_ids: Iterable[str],
    ranges: dict[str, LogRange],
) -> tuple[int | None, int | None]:
    first_lines = [
        ranges[request_id].first_line
        for request_id in request_ids
        if ranges[request_id].first_line is not None
    ]
    last_lines = [
        ranges[request_id].last_line
        for request_id in request_ids
        if ranges[request_id].last_line is not None
    ]
    return (
        min(first_lines) if first_lines else None,
        max(last_lines) if last_lines else None,
    )


def write_bound_outputs(
    entries: list[PhaseEntry],
    ranges: dict[str, LogRange],
    specs: Sequence[TestResultSpec],
    output_dir: Path,
    server_log: Path,
    summary_path: Path,
    route_entries: list[PhaseEntry] | None = None,
) -> dict[str, object]:
    """Write one isolated extraction directory per concrete result CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir = output_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    owner_by_request_id = {
        request_id: spec.result_file
        for spec in specs
        for request_id in spec.request_rows
    }
    entries_by_result: dict[str, list[PhaseEntry]] = defaultdict(list)
    routes_by_request: dict[str, list[dict]] = defaultdict(list)
    for entry in route_entries or ():
        routes_by_request[str(entry.record["parent_request_id"])].append(entry.record)
    for entry in entries:
        request_id = str(entry.record["parent_request_id"])
        owner = owner_by_request_id.get(request_id)
        if owner is not None:
            entries_by_result[owner].append(entry)

    manifest_results: list[dict[str, object]] = []
    request_index: list[dict[str, object]] = []
    selected_phase_count = 0

    for spec in specs:
        result_entries = entries_by_result[spec.result_file]
        selected_phase_count += len(result_entries)
        records = [entry.record for entry in result_entries]
        result_output_dir = extracted_dir / spec.source_path.stem
        result_output_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(result_output_dir / "controller_phases.jsonl", records)
        result_routes = [route for request_id in spec.request_rows
                         for route in routes_by_request.get(request_id, ())]
        _write_jsonl(result_output_dir / "routes.jsonl", result_routes)

        request_summaries = summarize_requests(records)
        for request_summary in request_summaries:
            request_id = str(request_summary["parent_request_id"])
            request_summary["test_result_file"] = spec.result_file
            request_summary["test_result_row"] = spec.request_rows[request_id]
        request_summaries.sort(key=lambda row: int(row["test_result_row"]))
        _write_jsonl(result_output_dir / "requests.jsonl", request_summaries)
        _write_phase_summary(result_output_dir / "phase_summary.csv", records)

        phase_entries_by_request: dict[str, list[PhaseEntry]] = defaultdict(list)
        for entry in result_entries:
            phase_entries_by_request[
                str(entry.record["parent_request_id"])
            ].append(entry)

        request_ids = list(spec.request_rows)
        first_log_line, last_log_line = _aggregate_range(request_ids, ranges)
        phase_line_numbers = [entry.line_number for entry in result_entries]
        seen_request_count = sum(
            1 for request_id in request_ids
            if ranges[request_id].first_line is not None)
        matching_log_lines = sum(
            ranges[request_id].matching_lines for request_id in request_ids)
        matched_phase_request_count = len(phase_entries_by_request)

        for row in spec.rows:
            phase_request_entries = (
                phase_entries_by_request.get(row.request_id, [])
                if row.request_id else [])
            log_range = ranges.get(row.request_id) if row.request_id else None
            routes = routes_by_request.get(row.request_id, [])
            route = routes[0] if len(routes) == 1 else {}
            action = route.get("action", "")
            route_status = (
                "duplicate_route" if len(routes) > 1 else
                "route_missing" if not routes else
                "native_with_unexpected_phase" if action == "native" and phase_request_entries else
                "native_no_phase_expected" if action == "native" else
                "dtb_phase_present" if phase_request_entries else "dtb_phase_missing")
            request_index.append({
                "result_file": spec.result_file,
                "result_row": row.row_number,
                "request_id": row.request_id,
                "has_request_id": bool(row.request_id),
                "seen_in_server_log": bool(
                    log_range and log_range.first_line is not None),
                "matching_log_lines": (
                    log_range.matching_lines if log_range else 0),
                "first_matching_log_line": (
                    log_range.first_line if log_range else None),
                "last_matching_log_line": (
                    log_range.last_line if log_range else None),
                "phase_records": len(phase_request_entries),
                "route_records": len(routes),
                "route_action": action,
                "route_scene": route.get("scene", ""),
                "route_reason": route.get("reason", ""),
                "route_status": route_status,
                "first_phase_log_line": (
                    min(entry.line_number for entry in phase_request_entries)
                    if phase_request_entries else None),
                "last_phase_log_line": (
                    max(entry.line_number for entry in phase_request_entries)
                    if phase_request_entries else None),
            })

        interleaved_other_phases = 0
        if first_log_line is not None and last_log_line is not None:
            interleaved_other_phases = sum(
                1 for entry in entries
                if first_log_line <= entry.line_number <= last_log_line
                and owner_by_request_id.get(
                    str(entry.record["parent_request_id"])) != spec.result_file
            )

        manifest_results.append({
            "result_file": spec.result_file,
            "source_path": _run_relative(spec.source_path, output_dir),
            "extraction_dir": (
                Path("extracted") / spec.source_path.stem).as_posix(),
            "client_rows": len(spec.rows),
            "request_ids": len(request_ids),
            "blank_request_id_rows": len(spec.rows) - len(request_ids),
            "requests_seen_in_server_log": seen_request_count,
            "matching_log_lines": matching_log_lines,
            "requests_with_tb_phase": matched_phase_request_count,
            "requests_without_tb_phase": len(request_ids) - matched_phase_request_count,
            "phase_records": len(result_entries),
            "route_records": len(result_routes),
            "requests_with_route": sum(bool(routes_by_request.get(rid)) for rid in request_ids),
            "route_status_counts": {
                status: sum(item["route_status"] == status for item in request_index
                            if item["result_file"] == spec.result_file)
                for status in {item["route_status"] for item in request_index
                               if item["result_file"] == spec.result_file}
            },
            "first_matching_log_line": first_log_line,
            "last_matching_log_line": last_log_line,
            "first_phase_log_line": (
                min(phase_line_numbers) if phase_line_numbers else None),
            "last_phase_log_line": (
                max(phase_line_numbers) if phase_line_numbers else None),
            "interleaved_other_phase_records": interleaved_other_phases,
            "summary_row": spec.summary_row,
        })

    request_index_path = output_dir / "request_index.csv"
    request_index_fields = [
        "result_file",
        "result_row",
        "request_id",
        "has_request_id",
        "seen_in_server_log",
        "matching_log_lines",
        "first_matching_log_line",
        "last_matching_log_line",
        "phase_records",
        "route_records", "route_action", "route_scene", "route_reason", "route_status",
        "first_phase_log_line",
        "last_phase_log_line",
    ]
    with request_index_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=request_index_fields)
        writer.writeheader()
        writer.writerows(request_index)

    all_phase_request_ids = {
        str(entry.record["parent_request_id"]) for entry in entries
    }
    manifest: dict[str, object] = {
        "schema": 1,
        "binding": "exact_result_csv_request_id_to_tb_phase_parent_request_id",
        "run_id": output_dir.resolve().name,
        "server_log": _run_relative(server_log, output_dir),
        "summary": _run_relative(summary_path, output_dir),
        "result_files": len(specs),
        "client_rows": sum(len(spec.rows) for spec in specs),
        "request_ids": len(owner_by_request_id),
        "total_phase_records_in_log": len(entries),
        "selected_phase_records": selected_phase_count,
        "total_route_records_in_log": len(route_entries or ()),
        "selected_route_records": sum(len(routes_by_request.get(rid, ())) for rid in owner_by_request_id),
        "route_coverage_note": "Missing routes are unknown (including legacy/off/disabled); never infer native from absence. Native route is intent, not generation success.",
        "excluded_phase_records": len(entries) - selected_phase_count,
        "total_phase_parent_requests": len(all_phase_request_ids),
        "excluded_phase_parent_requests": len(
            all_phase_request_ids - set(owner_by_request_id)),
        "result_hierarchy": {
            "client_results": "discovered recursively below run_dir",
            "isolated_phase_extractions": "extracted/<result_stem>/",
            "request_binding_audit": "request_index.csv",
        },
        "results": manifest_results,
    }
    with (output_dir / "extraction_manifest.json").open(
        "w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "run_dir",
        type=Path,
        help=(
            "run_<id> directory containing server.log, one _SUMMARY.csv, "
            "and all result CSVs"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    try:
        server_log, summary_path = discover_run_inputs(args.run_dir)
        specs = load_test_result_specs(
            args.run_dir.resolve(),
            summary_path,
        )
        tracked_request_ids = {
            request_id
            for spec in specs
            for request_id in spec.request_rows
        }
        with server_log.open(
            "r", encoding="utf-8", errors="replace") as file:
            route_entries: list[PhaseEntry] = []
            entries, ranges = scan_server_log(file, tracked_request_ids, route_entries=route_entries)
        manifest = write_bound_outputs(
            entries,
            ranges,
            specs,
            args.run_dir,
            server_log,
            summary_path,
            route_entries,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"bound {manifest['selected_phase_records']} of "
        f"{manifest['total_phase_records_in_log']} phases to "
        f"{manifest['request_ids']} request IDs in "
        f"{manifest['result_files']} result files; excluded "
        f"{manifest['excluded_phase_records']} unrelated phases")


if __name__ == "__main__":
    main()
