"""Reproduce replay2 evidence from recorded SSE and pre-parser audit deltas.

No generation or deployment is performed. Semantic tool-choice/schema anomalies
are reported separately and are not framework acceptance failures. Pair with
tools/dtb/reasoning_tail_fidelity.py for native-parser positive/negative controls.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
from types import SimpleNamespace

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[5]
sys.path.insert(0, str(ROOT))
from tools.dtb.analyze_tool_call_run import classify, extract_server_record_parts
from tools.dtb.replay_tool_calls import assemble_observation, build_body, json_hash


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def audit():
    rows = jsonl(RUN / "summary.jsonl")
    manifest = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    cases_path = RUN.parent.parent / "input/cases.jsonl"
    cases = jsonl(cases_path)
    # Git's Windows checkout may convert physical JSONL line endings to CRLF.
    input_bytes = cases_path.read_bytes()
    input_lf_sha256 = hashlib.sha256(input_bytes.replace(b"\r\n", b"\n")).hexdigest()
    assert input_lf_sha256 == manifest["input_sha256"]
    wire, request_hashes = defaultdict(list), {}
    wire_locations = defaultdict(list)
    for number, record in enumerate(jsonl(RUN / "chunks.jsonl"), 1):
        rid = record["client_request_id"]
        if record.get("record_type") == "request":
            assert rid not in request_hashes
            request_hashes[rid] = record["request_body_sha256"]
        else:
            assert not record.get("parse_error")
            if isinstance(record.get("chunk"), dict):
                assert not record["chunk"].get("error")
                wire[rid].append(record["chunk"])
                wire_locations[rid].append(number)
    ids = {row["server_request_id"] for row in rows}
    assert len(ids) == len(rows) == len(request_hashes) == 120
    pairs = Counter((row["line_no"], row["attempt"]) for row in rows)
    assert len(pairs) == 60 and set(pairs.values()) == {2}
    kwargs_keys = [
        "line_no", "attempt", "variant", "client_request_id", "status_code",
        "elapsed_ms", "expected_tool_call", "case_id", "category",
        "expected_tool_names", "latency_eligible", "error", "first_chunk_ms",
        "first_output_ms", "first_reasoning_ms", "first_content_ms", "first_tool_call_ms",
    ]
    for row in rows:
        source = cases[row["line_no"] - 1]["request_body"]
        observation = assemble_observation(
            **{key: row[key] for key in kwargs_keys},
            chunks=wire[row["client_request_id"]], request_tools=source["tools"])
        assert asdict(observation) == row, (row["client_request_id"], "SSE/summary mismatch")
        body = build_body(source, variant=row["variant"],
                          client_request_id=row["client_request_id"],
                          args=SimpleNamespace(**{
                              **manifest["request_overrides"], "stream": manifest["stream"],
                              "model": manifest["model_override"],
                          }))
        assert json_hash(body) == request_hashes[row["client_request_id"]]
        assert row["status_code"] == 200 and not row["protocol_anomaly_reasons"]

    logs = sorted(RUN.glob("server_filtered_part_*"))
    raw = defaultdict(list)
    for path in logs:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "DTB_STREAM_AUDIT" not in line:
                continue
            record = json.loads(line.split("DTB_STREAM_AUDIT", 1)[1])
            record["location"] = f"{path.name}:{number}"
            raw[record["parent_request_id"]].append(record)
    assert set(raw) == {row["server_request_id"] for row in rows if row["variant"] == "dtb"}
    records, invalid = extract_server_record_parts(logs, ids)
    assert not invalid
    uploaded = json.loads((RUN / "analysis.json").read_text(encoding="utf-8"))
    assert [classify(row, records.get(row["server_request_id"], [])) for row in rows] == uploaded["requests"]

    boundaries, thinking_lengths, semantics = [], [], []
    for number, row in enumerate(rows, 1):
        rid = row["server_request_id"]
        source = cases[row["line_no"] - 1]["request_body"]
        allowed = {tool["function"]["name"] for tool in source["tools"]}
        if row["semantic_anomaly_reasons"]:
            item = {
                "request_id": rid, "variant": row["variant"],
                "location": f"summary.jsonl:{number}",
                "reasons": row["semantic_anomaly_reasons"],
                "tools": [tool["name"] for tool in row["tool_calls"]],
                "undeclared_tools": [tool["name"] for tool in row["tool_calls"]
                                     if tool["name"] not in allowed],
                "schema_errors": row["schema_errors"],
            }
            if row["schema_errors"] and row["variant"] == "dtb":
                final = [r for r in raw[rid] if r["phase"] == "final_answer"]
                item["raw_final"] = "".join(r["raw_text"] for r in final)
                item["public_tool_calls"] = row["tool_calls"]
            semantics.append(item)
        if row["variant"] != "dtb":
            continue
        rs = raw[rid]
        assert [r["sequence"] for r in rs] == list(range(1, len(rs) + 1))
        assert all(r["raw_text"] == r["forwarded_text"] and
                   r["token_ids"] == r["forwarded_token_ids"] for r in rs)
        ts = [r for r in rs if r["phase"] == "initial_think"]
        terminal = [r for r in ts if r["source_finish_reason"]]
        assert len(terminal) == 1
        b = terminal[0]
        assert b is ts[-1] and b["source_finish_reason"] == "stop"
        assert type(b["source_stop_reason"]) is int
        assert b["source_stop_reason"] in (248069, 248058)
        assert b["token_ids"][-1] == b["source_stop_reason"]
        marker = "</think>" if b["source_stop_reason"] == 248069 else "<tool_call>"
        assert marker not in b["raw_text"]
        assert all(r["include_stop_str_in_output"] is False and
                   r["configured_stop_token_ids"] == [248069, 248058] and
                   r["configured_stop_strings"] == [] for r in ts)
        expected = "".join(r["raw_text"] for r in ts)
        assert expected == row["reasoning"], (rid, "public reasoning fidelity failure")
        thinking_lengths.append(sum(len(r["token_ids"]) for r in ts))
        matching_wire = [line for line, chunk in zip(wire_locations[row["client_request_id"]],
                                                   wire[row["client_request_id"]])
                         if any(choice.get("delta", {}).get("reasoning") == b["raw_text"]
                                and b["raw_text"] for choice in chunk.get("choices", []))]
        boundaries.append({
            "request_id": rid, "line_no": row["line_no"], "attempt": row["attempt"],
            "source_location": b["location"], "public_location": f"summary.jsonl:{number}",
            "matching_reasoning_sse_lines": matching_wire,
            "stop_reason": b["source_stop_reason"], "boundary_text": b["raw_text"],
            "boundary_token_ids": b["token_ids"],
            "payload_tokens": len(b["token_ids"]) - 1,
            "source_chars": len(expected), "public_chars": len(row["reasoning"]),
            "public_tail": row["reasoning"][-150:],
            "literal_think_end_occurrences": expected.count("</think>"),
            "exact_public_fidelity": True,
        })

    all_raw = [r for rs in raw.values() for r in rs]
    phase_counts = Counter((r["phase"], r["event"]) for r in all_raw)
    assert set(phase_counts) == {
        ("_ThinkingState", "state_enter"), ("_FinalAnswerState", "state_enter"),
        ("initial_think", "delta"), ("final_answer", "delta"),
        ("final_answer", "final_terminal_passthrough"), ("controller_end", "delta"),
    }, "This focused audit does not handle probe/iterative/forced-close trajectories"
    mixed = [b for b in boundaries if b["stop_reason"] == 248069 and b["boundary_text"]]
    result = {
        "scope": "Uploaded replay2 HTTP SSE and controller_pre_parser audit; no new generation",
        "identity": {"created_at": manifest["created_at"], "input_sha256": manifest["input_sha256"],
                     "input_sha256_verified_after_lf_normalization": True,
                     "request_id_run": manifest["request_id_run"],
                     "deployed_reasoner_hash_recorded_in_replay2": False},
        "integrity": {
            "requests": len(rows), "pairs": len(pairs), "unique_request_ids": len(ids),
            "audit_requests": len(raw), "audit_records": len(all_raw), "invalid_audit_records": invalid,
            "all_request_hashes_match": True, "all_sse_summaries_match": True,
            "uploaded_analysis_reproduced": True, "all_audit_sequences_contiguous": True,
            "source_forwarded_text_ids_mismatches": 0,
        },
        "reasoning": {
            "exact_public_fidelity_requests": len(boundaries), "loss_or_duplication_requests": 0,
            "public_chars": sum(b["public_chars"] for b in boundaries),
            "mixed_think_end_chunks": len(mixed),
            "mixed_tail_chars_preserved": sum(len(b["boundary_text"]) for b in mixed),
            "mixed_tail_nonboundary_token_ids": sum(b["payload_tokens"] for b in mixed),
            "mixed_nonwhitespace_tails": sum(bool(b["boundary_text"].strip()) for b in mixed),
            "pure_think_end_chunks": sum(b["stop_reason"] == 248069 and not b["boundary_text"]
                                         for b in boundaries),
            "pure_tool_call_chunks": sum(b["stop_reason"] == 248058 and not b["boundary_text"]
                                         for b in boundaries),
            "literal_think_end_requests": [b["request_id"] for b in boundaries
                                          if b["literal_think_end_occurrences"]],
        },
        "thinking_tokens_including_boundary": {
            "min": min(thinking_lengths), "median": statistics.median(thinking_lengths),
            "max": max(thinking_lengths), "total": sum(thinking_lengths),
        },
        "phase_event_counts": [{"phase": p, "event": e, "count": n}
                               for (p, e), n in phase_counts.items()],
        "protocol": {v: {"requests": sum(r["variant"] == v for r in rows),
                          "failures": sum(bool(r["protocol_anomaly_reasons"])
                                          for r in rows if r["variant"] == v)}
                     for v in ["vanilla", "dtb"]},
        "semantic_counts_excluded_from_framework_acceptance": {
            v: {"requests": sum(r["variant"] == v for r in semantics),
                "tool_choice_mismatch": sum("expected_tool_name_missing" in r["reasons"]
                                            for r in semantics if r["variant"] == v),
                "schema_failure": sum(bool(r["schema_errors"]) for r in semantics if r["variant"] == v),
                "undeclared_tool": sum(bool(r["undeclared_tools"]) for r in semantics if r["variant"] == v)}
            for v in ["vanilla", "dtb"]},
        "boundaries": boundaries, "semantic_evidence": semantics,
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit()
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("boundaries", "semantic_evidence")}, ensure_ascii=False, indent=2))
