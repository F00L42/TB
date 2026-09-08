"""Offline, run-local audit. No model/server calls; writes analysis.json only.

Run: python -X utf8 docs/log/perf/agent_minimum_intervention/run_server_01/analyze_run.py
Semantic anomalies are retained in protocol-clean timing pairs. Positive gain
means improvement; completion reduction is NOT reasoning compression.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parent
REPO = next(p for p in ROOT.parents if (p / "tools/dtb/replay_tool_calls.py").exists())
sys.path.insert(0, str(REPO))
from tools.dtb.replay_tool_calls import build_body, json_hash  # noqa: E402


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("{")]


def stats(values):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * .95
    lo = int(k)
    return dict(n=len(values), sum=sum(values), mean=statistics.mean(values),
                median=statistics.median(values),
                p95=values[lo] + (values[min(lo + 1, len(values)-1)]-values[lo])*(k-lo),
                min=values[0], max=values[-1])


def metrics(pairs):
    if not pairs:
        return None
    result = {}
    for variant in ("vanilla", "dtb"):
        rows = [p[variant] for p in pairs]
        result[variant] = {
            "elapsed_ms": stats([r["elapsed_ms"] for r in rows]),
            "first_output_ms": stats([r["first_output_ms"] for r in rows]),
            "completion_tokens": sum(r["usage"]["completion_tokens"] for r in rows),
        }
    v, d = result["vanilla"], result["dtb"]
    result.update(
        pairs=len(pairs),
        e2e_gain_pct=100*(1-d["elapsed_ms"]["sum"]/v["elapsed_ms"]["sum"]),
        completion_reduction_pct=100*(1-d["completion_tokens"]/v["completion_tokens"]),
        paired_delta_ms=stats([p["dtb"]["elapsed_ms"]-p["vanilla"]["elapsed_ms"] for p in pairs]),
        paired_gain_pct=stats([100*(1-p["dtb"]["elapsed_ms"]/p["vanilla"]["elapsed_ms"]) for p in pairs]),
        dtb_faster=sum(p["dtb"]["elapsed_ms"] < p["vanilla"]["elapsed_ms"] for p in pairs),
    )
    return result


def main():
    result = {}
    raw = read_rows(ROOT / "detokenizer.jsonl")
    cuts = [r for r in raw if r.get("event") == "cut"]
    failures = [r for r in cuts if not r["ok"]]
    declared = next(r for r in raw if r.get("event") == "summary")
    assert (len(cuts), len(failures)) == (declared["tested"], declared["failed"])
    result["g0"] = dict(identity=raw[0], tested=len(cuts), failed=len(failures),
        samples={s: dict(tested=sum(r["sample"]==s for r in cuts),
                         failed=sum(r["sample"]==s for r in failures)) for s in range(4)},
        failure_restart_points=sorted({r["restart"] for r in failures}),
        no_string_stop_failures=sum(not r["string_stop"] for r in failures),
        no_pending_failures=sum(r["pending"] == 0 for r in failures),
        representative=failures[0])
    source = REPO / "docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl"
    # The server used LF; this Windows checkout materializes CRLF.
    source_hash = hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    cases = read_rows(source)
    phase_by_id = defaultdict(list)
    log = ROOT / "replay_phase/server_filtered_part_01"
    for line_no, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
        if "DTB_PHASE " in line:
            row = json.loads(line.split("DTB_PHASE ", 1)[1])
            assert row["schema"] == 3
            row["log_line"] = line_no
            phase_by_id[row["parent_request_id"]].append(row)
    result["request_summary_events"] = log.read_text(encoding="utf-8").count("DTB_REQUEST_SUMMARY")
    for name in ("replay", "replay_phase"):
        folder = ROOT / name
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert source_hash == manifest["input_sha256"]
        rows = read_rows(folder / "summary.jsonl")
        assert len(rows) == len(manifest["cases"]) * manifest["repetitions"] * 2
        by_id = {r["client_request_id"]: r for r in rows}
        assert len(by_id) == len(rows)
        pairs = defaultdict(dict)
        for r in rows:
            key = r["line_no"], r["attempt"]
            assert r["variant"] not in pairs[key]
            pairs[key][r["variant"]] = r
        assert all(set(p) == {"vanilla", "dtb"} for p in pairs.values())
        assert set(pairs) == {(c["line_no"], a) for c in manifest["cases"]
                             for a in range(1, manifest["repetitions"]+1)}
        args = argparse.Namespace(**manifest["request_overrides"],
                                  model=manifest["model_override"], stream=manifest["stream"])
        request_ids = []
        sse_errors = defaultdict(list)
        chunk_counts = Counter()
        for line_no, line in enumerate((folder / "chunks.jsonl").open(encoding="utf-8"), 1):
            r = json.loads(line)
            obs = by_id[r["client_request_id"]]
            if r.get("record_type") == "request":
                request_ids.append(r["client_request_id"])
                body = build_body(cases[r["line_no"]-1]["request_body"],
                                  variant=r["variant"], client_request_id=r["client_request_id"], args=args)
                assert json_hash(body) == r["request_body_sha256"], (name, line_no)
            else:
                chunk_counts[r["client_request_id"]] += bool(r.get("chunk"))
                if obs["protocol_anomaly_reasons"]:
                    for choice in (r.get("chunk") or {}).get("choices", []):
                        if choice.get("finish_reason") or choice.get("delta", {}).get("tool_calls"):
                            sse_errors[r["client_request_id"]].append(dict(file_line=line_no,
                                event_index=r["event_index"], choice=choice))
        assert len(request_ids) == len(set(request_ids)) == len(rows)
        assert set(request_ids) == set(by_id)
        assert all(chunk_counts[r["client_request_id"]] == r["chunk_count"] for r in rows)
        all_pairs = list(pairs.values())
        eligible = [p for p in all_pairs if all(r["latency_eligible"] for r in p.values())]
        clean = lambda p: all(not r["protocol_anomaly_reasons"] and not r["error"] and r["status_code"]==200 for r in p.values())
        group = dict(identity=manifest, request_count=len(rows), input_and_request_hashes_verified=True,
            status_codes=dict(Counter(str(r["status_code"]) for r in rows)),
            protocol={v: dict(Counter(e for r in rows if r["variant"]==v for e in r["protocol_anomaly_reasons"])) for v in ("vanilla", "dtb")},
            semantic={v: dict(Counter(e for r in rows if r["variant"]==v for e in r["semantic_anomaly_reasons"])) for v in ("vanilla", "dtb")},
            all_pairs=metrics(all_pairs), eligible_all=metrics(eligible),
            eligible_protocol_clean=metrics([p for p in eligible if clean(p)]),
            eligible_protocol_clean_after_first=metrics([p for p in eligible if clean(p) and p["dtb"]["attempt"] > 1]),
            all_protocol_clean=metrics([p for p in all_pairs if clean(p)]),
            all_protocol_clean_after_first=metrics([p for p in all_pairs if clean(p) and p["dtb"]["attempt"] > 1]),
            per_case={str(c["line_no"]): metrics([p for p in all_pairs if p["dtb"]["line_no"]==c["line_no"] and clean(p)]) for c in manifest["cases"]},
            prompt_usage_mismatches=[dict(line_no=k[0], attempt=k[1], vanilla=p["vanilla"]["usage"]["prompt_tokens"], dtb=p["dtb"]["usage"]["prompt_tokens"]) for k,p in pairs.items() if p["dtb"]["usage"]["prompt_tokens"]!=p["vanilla"]["usage"]["prompt_tokens"]],
            protocol_errors=[dict(summary=r, sse=sse_errors[r["client_request_id"]]) for r in rows if r["protocol_anomaly_reasons"]])
        if name == "replay_phase":
            dtb_ids = {r["server_request_id"] for r in rows if r["variant"]=="dtb"}
            assert set(phase_by_id) == dtb_ids
            assert len({(r["parent_request_id"], r["child_request_id"], r["phase"], r["round"]) for phases in phase_by_id.values() for r in phases}) == sum(map(len,phase_by_id.values()))
            native = [r for phases in phase_by_id.values() for r in phases if r["phase"]=="native_passthrough"]
            assert len(native)==len(dtb_ids)
            group["phase"] = dict(counts=dict(Counter(r["phase"] for phases in phase_by_id.values() for r in phases)),
                status=dict(Counter(r["intervention"]["intervention_status"] for r in native)),
                reasoning_tokens=stats([r["intervention"]["observed_reasoning_tokens"] for r in native]),
                bypass_forward_wall_ms=stats([r["intervention"]["observer_forward_wall_ms"] for r in native if r["intervention"]["native_same_child_completed"]]),
                wall_sum_ms=sum(r["wall_ms"] for phases in phase_by_id.values() for r in phases),
                visible_generated_tokens=sum(r["generated_tokens"] for phases in phase_by_id.values() for r in phases if r["visible"]),
                hidden_generated_tokens=sum(r["generated_tokens"] for phases in phase_by_id.values() for r in phases if not r["visible"]),
                activated=[])
            for r in native:
                if r["intervention"]["native_same_child_completed"]:
                    continue
                pair = next(p for p in all_pairs if p["dtb"]["server_request_id"]==r["parent_request_id"])
                group["phase"]["activated"].append(dict(pair=pair, metrics=metrics([pair]), phases=phase_by_id[r["parent_request_id"]]))
            group["phase"]["observations"] = native
        result[name] = group
    result["effective_source_sampling"] = [{"line_no":i, **{k:r["request_body"].get(k) for k in ("model","temperature","top_p","top_k","seed","max_tokens","max_completion_tokens")}} for i,r in enumerate(cases,1)]
    (ROOT / "analysis.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Verified G0 counts, pairing, source/request hashes, chunk coverage and phase ownership; wrote analysis.json")


if __name__ == "__main__":
    main()
