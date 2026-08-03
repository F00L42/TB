"""Extract Think Budget phase JSON from a vLLM API-server log.

Usage:
    python tools/extract_tb_phase_metrics.py server.log docs/log/perf/<run_id>

Only Python's standard library is required. Existing files in the output
directory are replaced; use a new run_id directory for every experiment.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


PHASE_MARKER = "TB_PHASE "
NUMERIC_TIMING_FIELDS = ("wall_ms", "queue_ms", "prefill_ms", "decode_ms")


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def parse_phase_records(lines: Iterable[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, 1):
        marker_index = line.find(PHASE_MARKER)
        if marker_index < 0:
            continue
        payload = line[marker_index + len(PHASE_MARKER):].strip()
        try:
            record = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid TB_PHASE JSON at log line {line_number}") from exc
        if record.get("schema") not in {1, 2}:
            raise ValueError(
                f"unsupported TB_PHASE schema at log line {line_number}: "
                f"{record.get('schema')!r}")
        records.append(record)
    return records


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
        result.append({
            "schema": decision.get("schema", 1),
            "parent_request_id": request_id,
            "scene": decision.get("scene"),
            "scene_matched": decision.get("scene_matched"),
            "decision_branch": decision.get("decision_branch"),
            "budget_plan": decision.get("budget_plan"),
            "decision_details": decision.get("decision_details"),
            # Phases are sequential. This is controller-accounted wall time,
            # not HTTP E2E; pair it with the client-side result CSV.
            "accounted_phase_wall_ms": round(sum(
                float(record.get("wall_ms") or 0) for record in request_records), 3),
            "generate_calls": sum(
                1 for record in request_records if record.get("phase") != "decision"),
            "visible_think_tokens": sum(
                int(record.get("generated_tokens") or 0)
                for record in request_records
                if record.get("phase") in {"initial_think", "iterative_think"}
            ),
            "hidden_probe_tokens": sum(
                int(record.get("generated_tokens") or 0)
                for record in request_records
                if record.get("phase") == "confidence_probe"
            ),
            "final_answer_tokens": sum(
                int(record.get("generated_tokens") or 0)
                for record in request_records
                if record.get("phase") == "final_answer"
            ),
            "probe_rounds": sum(
                1 for record in request_records
                if record.get("phase") == "confidence_probe"),
            "accepted_probe_round": next((
                record.get("round") for record in request_records
                if record.get("phase") == "confidence_probe"
                and record.get("accepted") is True
            ), None),
            "probe_assessments": probe_assessments,
        })
    return result


def write_outputs(records: list[dict[str, object]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "controller_phases.jsonl").open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    request_summaries = summarize_requests(records)
    with (output_dir / "requests.jsonl").open("w", encoding="utf-8") as file:
        for record in request_summaries:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    phase_summaries = summarize_phases(records)
    if phase_summaries:
        with (output_dir / "phase_summary.csv").open(
            "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(phase_summaries[0]))
            writer.writeheader()
            writer.writerows(phase_summaries)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("server_log", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    with args.server_log.open("r", encoding="utf-8", errors="replace") as file:
        records = parse_phase_records(file)
    if not records:
        raise SystemExit("no TB_PHASE records found")
    write_outputs(records, args.output_dir)
    print(f"extracted {len(records)} phases for {len(summarize_requests(records))} requests")


if __name__ == "__main__":
    main()
