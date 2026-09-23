#!/usr/bin/env python3
"""Audit delivery JSONL replay against a separate OBSERVABILITY=phase server log.

Auto expectations describe budget_config_noemate_750.yaml. They are not inferred
from output text or HTTP success. Other policies require a dataset with matching
auto_route_noemate_750 declarations. Missing events fail closed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path


def audit(run_dir, logs, *, require_intervention=False):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["server_identity"]["observability"] != "phase":
        raise ValueError("phase audit requires a separately recorded phase run")
    dataset = Path(manifest["dataset"])
    import hashlib
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != manifest["input_sha256"]:
        raise ValueError("dataset has changed since replay")
    expected = {r["case_id"]: r["auto_route_noemate_750"] for r in
                map(json.loads, dataset.read_text(encoding="utf-8").splitlines()) if r}
    rows = [json.loads(line) for file in sorted(run_dir.glob("*/summary.jsonl"))
            for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("no client observations")
    streams = [True, False] if manifest["protocol"] == "both" else [manifest["protocol"] == "stream"]
    expected_keys = {(profile, stream, case, attempt, variant)
                     for profile in manifest["profiles"] for stream in streams for case in manifest["cases"]
                     for attempt in range(1-manifest["warmup_batches_per_cell_variant"], manifest["repetitions"]+1)
                     for variant in ("vanilla", "dtb")}
    actual_keys = [(r["profile"], r["stream"], r["case_id"], r["attempt"], r["variant"]) for r in rows]
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != expected_keys:
        raise ValueError("client matrix is missing, duplicate or inconsistent with manifest")
    parents = [r["server_request_id"] for r in rows]
    if any(not p for p in parents) or len(set(parents)) != len(parents):
        raise ValueError("missing or duplicate server request IDs")
    selected = set(parents)
    events = {name: defaultdict(list) for name in ("DTB_ROUTE", "DTB_PHASE", "DTB_REQUEST_SUMMARY")}
    for path in logs:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                for name, records in events.items():
                    if name + " " in line:
                        row = json.loads(line.split(name + " ", 1)[1])
                        if row.get("parent_request_id") in selected:
                            records[row["parent_request_id"]].append(row)
                        break
    failures, details = [], []
    direct, probes = 0, 0
    validation = json.loads((run_dir / "validation.json").read_text(encoding="utf-8"))
    if not validation["functional_pass"]:
        failures.append("client functional gate failed")
    for row in rows:
        parent = row["server_request_id"]
        route, phases, summaries = (events[n][parent] for n in events)
        errors = list(row["validation_errors"])
        if row["variant"] == "vanilla":
            if route or phases or summaries:
                errors.append("vanilla_has_dtb_events")
        else:
            exp = expected[row["case_id"]] if row["profile"] == "auto" else {"scene":"general", "action":"dtb"}
            if len(route) != 1 or any(route[0].get(k) != v for k, v in exp.items()):
                errors.append("route_missing_duplicate_or_mismatch")
            if exp["action"] == "native":
                if phases or summaries:
                    errors.append("native_has_dtb_ledger")
            elif len(summaries) != 1 or not phases:
                errors.append("dtb_ledger_missing_or_duplicate")
            else:
                summary = summaries[0]
                if (summary.get("completion_status") != "success" or summary.get("error")
                        or (row["stream"] and summary.get("final_terminal_count") != 1)):
                    errors.append("dtb_summary_unsuccessful")
                hidden = sum(p.get("generated_tokens", 0) for p in phases if p.get("phase") == "confidence_probe")
                if hidden != summary.get("hidden_probe_tokens"):
                    errors.append("hidden_probe_ledger_mismatch")
                if len({json.dumps(p, sort_keys=True) for p in phases}) != len(phases):
                    errors.append("duplicate_phase")
                trace = summary.get("intervention", {})
                if row["stream"] and trace.get("intervention_status") == "triggered":
                    if (trace.get("abort_count") != 1 or trace.get("abort_terminal_seen") is not True
                            or trace.get("abort_lost_to_native_terminal") is not False):
                        errors.append("invalid_abort_handoff")
                    if (sum(p.get("phase") == "native_passthrough" for p in phases) != 1
                            or sum(p.get("phase") == "final_answer" for p in phases) != 1):
                        errors.append("intervention_phases_missing_or_duplicate")
                    if trace.get("post_drain_branch") == "confidence_probe" and hidden > 0:
                        probes += not errors
                    if trace.get("post_drain_branch") == "budget_final" and hidden == 0:
                        direct += not errors
        if errors:
            failures.append({"parent_request_id": parent, "errors": errors})
        details.append({"parent_request_id":parent,"profile":row["profile"],"variant":row["variant"],
                        "route_count":len(route),"phase_count":len(phases),"errors":errors})
    coverage = direct > 0 and probes > 0
    return {"requests":len(rows), "failures":failures, "direct_interventions":direct,
            "probe_interventions":probes, "intervention_coverage":coverage, "details":details,
            "exit_code":1 if failures else (3 if require_intervention and not coverage else 0)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path)
    p.add_argument("server_logs", type=Path, nargs="+")
    p.add_argument("--require-intervention", action="store_true")
    args = p.parse_args()
    result = audit(args.run_dir, args.server_logs, require_intervention=args.require_intervention)
    (args.run_dir / "phase_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "details"}, ensure_ascii=False, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"FAIL: {exc}")
