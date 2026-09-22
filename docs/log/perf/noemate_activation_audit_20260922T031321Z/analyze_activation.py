"""Offline audit of grep-filtered telemetry joined to replay SSE; never sends HTTP."""
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "previous_audit", OUT.parent / "noemate_direct750_off_20260921T064359Z/analyze_4c259c.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(
        previous.ROOT / "docs/tokenizer/Qwen3.6-27B", local_files_only=True,
        trust_remote_code=False)
    cases = {c.line_no: c for c in previous.replay.load_cases(previous.DATA, 1, 0)}
    source_hash = hashlib.sha256(previous.DATA.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    rows, client_audit = previous.audit_folder(OUT / "direct750", cases, tokenizer, source_hash)
    expected = {"chatcmpl-" + r["client_request_id"] for r in rows}
    groups = defaultdict(list)
    records = []
    for line_no, line in enumerate((OUT / "serverlog").read_text(encoding="utf-8").splitlines(), 1):
        markers = [tag for tag in ("DTB_PHASE", "DTB_REQUEST_SUMMARY") if tag + " " in line]
        assert len(markers) == 1, (line_no, "unrecognized/ambiguous log line")
        tag = markers[0]
        event = json.loads(line.split(tag + " ", 1)[1])
        record = {"log_line": line_no, "event_type": tag, "event": event}
        records.append(record)
        groups[event["parent_request_id"]].append(record)
    assert set(groups) == expected
    result = []
    for row in rows:
        parent = "chatcmpl-" + row["client_request_id"]
        assert row["server_request_id"] == parent
        entries = groups[parent]
        summaries = [e for e in entries if e["event_type"] == "DTB_REQUEST_SUMMARY"]
        phases = [e["event"] for e in entries if e["event_type"] == "DTB_PHASE"]
        assert len(summaries) == 1
        summary = summaries[0]["event"]
        trace = summary["intervention"]
        counts = Counter(e["phase"] for e in phases)
        assert summary["completion_status"] == "success" and summary["error"] is None
        assert summary["hidden_probe_tokens"] == 0 and not counts["confidence_probe"]
        assert trace["minimum_intervention_tokens"] == 750
        assert trace["threshold_source"] == "fixed_check_length"
        assert trace["activation_skipped_reason"] is None and not trace["handoff_safety_failure"]
        assert summary["final_terminal_count"] == row["terminal_count"] == 1
        assert summary["emitted_completion_tokens"] == row["usage"]["completion_tokens"]
        assert summary["emitted_completion_tokens"] + summary["completion_tokens_remaining"] == summary["completion_token_limit"]
        assert sum(e["generated_tokens"] for e in phases) == summary["total_model_generated_tokens"]
        model_phases = [e for e in phases if e["phase"] != "decision"]
        assert len(model_phases) == summary["child_generate_calls"]
        assert len({e["child_request_id"] for e in model_phases}) == len(model_phases)
        forced = trace["post_drain_branch"] == "budget_final"
        if forced:
            assert counts == {"decision": 1, "native_passthrough": 1, "final_answer": 1}
            decision = next(e["budget_decision"] for e in phases if e["phase"] == "decision")
            assert decision["decision_branch"] == "fixed"
            assert decision["budget"] == {"check_length": 750, "min_think_budget": 1, "num_thinking": 4, "think_stride": 256}
            assert decision["hard_think_token_limit"] == 750 and decision["confidence_threshold"] == .95
            assert decision["source_by_field"]["hard_think_token_limit"]["source"] == "fixed_budget_tokens"
            assert trace["abort_count"] == 1 and trace["abort_terminal_seen"]
            assert not trace["native_same_child_completed"]
            assert trace["drained_tail_tokens"] == 0
            final = next(e for e in phases if e["phase"] == "final_answer")
            assert final["final_answer_reason"] == "budget_exhausted"
            assert final["answer_limit"] == final["external_remaining_tokens"]
            assert final["answer_limit"] + final["emitted_completion_tokens_before"] == final["completion_token_limit"]
            assert summary["emitted_completion_tokens"] - summary["total_model_generated_tokens"] == 1
        else:
            assert counts == {"native_passthrough": 1}
            assert trace["post_drain_branch"] == "native_complete"
            assert trace["abort_count"] == 0 and trace["native_same_child_completed"]
            assert summary["budget_resolution"] == "not_required_native"
            assert summary["emitted_completion_tokens"] == summary["total_model_generated_tokens"]
        phase_wall = sum(e["wall_ms"] for e in phases)
        result.append({
            "line": row["line_no"], "attempt": row["attempt"], "parent_request_id": parent,
            "serverlog_lines": [e["log_line"] for e in entries],
            "branch": trace["post_drain_branch"], "phase_counts": counts,
            "server_thinking_tokens": summary["thinking_tokens"],
            "local_reasoning_tokens": row["reasoning_tokens"],
            "server_final_tokens": summary["final_answer_tokens"],
            "completion_tokens": row["usage"]["completion_tokens"],
            "recorded_model_generated_tokens": summary["total_model_generated_tokens"],
            "model_work_complete": summary["model_work_complete"],
            "finish_reasons": row["finish_reasons"], "has_tool_call": row["has_tool_call"],
            "empty_final": not row["content"].strip() and not row["tool_calls"],
            "protocol_anomalies": row["protocol_anomaly_reasons"],
            "semantic_anomalies": row["semantic_anomaly_reasons"],
            "e2e_ms": row["elapsed_ms"], "phase_wall_ms": phase_wall,
            "e2e_minus_phase_ms": row["elapsed_ms"] - phase_wall,
            "intervention": trace,
        })
    phase_records = [e["event"] for e in records if e["event_type"] == "DTB_PHASE"]
    phase_totals = {}
    for phase in sorted({e["phase"] for e in phase_records}):
        selected = [e for e in phase_records if e["phase"] == phase]
        phase_totals[phase] = {"calls": len(selected)}
        for field in ("wall_ms", "queue_ms", "prefill_ms", "decode_ms", "prompt_tokens", "cached_prompt_tokens", "generated_tokens"):
            known = [e[field] for e in selected if e.get(field) is not None]
            phase_totals[phase][field] = {"sum": sum(known), "known": len(known)}
    report = {
        "client_audit": client_audit, "log_source": "grep-filtered serverlog; not full server log",
        "client_commit": (OUT / "client_commit.txt").read_text().strip(),
        "input_sha256": source_hash,
        "source_sha256": {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
                          for name in ("serverlog", "start.sh", "client_worktree.patch", "direct750/manifest.json", "direct750/summary.jsonl", "direct750/chunks.jsonl")},
        "requests": len(result), "log_events": len(records), "phase_records": len(phase_records),
        "branches": Counter(r["branch"] for r in result), "phase_totals": phase_totals,
        "hidden_probe_tokens": 0, "unknown_dropped_telemetry": True,
        "model_work_complete_requests": sum(r["model_work_complete"] for r in result),
        "recorded_model_generated_tokens": sum(r["recorded_model_generated_tokens"] for r in result),
        "response_completion_tokens": sum(r["completion_tokens"] for r in result),
        "e2e_ms": sum(r["e2e_ms"] for r in result),
        "phase_wall_ms": sum(r["phase_wall_ms"] for r in result),
        "protocol_failures": sum(bool(r["protocol_anomalies"]) for r in result),
        "semantic_flags": sum(bool(r["semantic_anomalies"]) for r in result),
        "length_finishes": sum("length" in r["finish_reasons"] for r in result),
        "empty_finals": sum(r["empty_final"] for r in result),
    }
    write("audit_summary.json", report)
    write("request_audit.json", result)
    (OUT / "extracted_events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in records), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
