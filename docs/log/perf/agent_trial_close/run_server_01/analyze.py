"""Recompute the uploaded A/B replay and phase ledger; no model/tool execution."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean
from argparse import Namespace
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[4] / "tools/dtb"))
from replay_tool_calls import build_body, json_hash


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def pct(values, q):
    values = sorted(values)
    x = (len(values) - 1) * q
    lo = int(x)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (x - lo)


def stats(rows):
    times = [r["elapsed_ms"] / 1000 for r in rows]
    return dict(n=len(rows), e2e_s=sum(times), mean_s=mean(times), p50_s=pct(times, .5),
                p95_s=pct(times, .95), completion_tokens=sum(r["usage"]["completion_tokens"] for r in rows),
                first_output_mean_ms=mean(r["first_output_ms"] for r in rows),
                anomalies=sum(bool(r["anomaly_reasons"]) for r in rows))


def phase_stats(phases):
    result = {}
    for name in sorted({p["phase"] for p in phases}):
        items = [p for p in phases if p["phase"] == name]
        result[name] = {"calls": len(items), "tokens": sum(p["generated_tokens"] for p in items)}
        for k in ("wall_ms", "queue_ms", "prefill_ms", "decode_ms", "controller_cpu_ms"):
            known = [p[k] for p in items if p.get(k) is not None]
            result[name][k] = sum(known)
            result[name][k + "_known"] = len(known)
    return result


def probe_stats(phases):
    ps = [p for p in phases if p["phase"] == "confidence_probe"]
    first = [p for p in ps if p["round"] == 1]
    return dict(calls=len(ps), accepted=sum(p["accepted"] for p in ps),
                first_calls=len(first), first_accepted=sum(p["accepted"] for p in first),
                mean_confidence=mean(p["confidence"] for p in ps) if ps else None,
                first_mean_confidence=mean(p["confidence"] for p in first) if first else None,
                stop_reasons=dict(Counter(p["stop_reason"] for p in ps)),
                methods=dict(Counter(p["confidence_method"] for p in ps)),
                accepted_length=sum(p["accepted"] and p["stop_reason"] == "length" for p in ps),
                generated_tokens=sum(p["generated_tokens"] for p in ps),
                wall_s=sum(p["wall_ms"] for p in ps)/1000)


def main():
    report = {"groups": {}, "case_comparison": [], "request_comparison": [], "hashes": {}}
    all_data = {}
    input_file = ROOT.parents[1] / "agent_run06/trial_close_experiment/requests.jsonl"
    source_hash = hashlib.sha256(input_file.read_bytes()).hexdigest()
    sources = read_rows(input_file)
    request_hashes = {}
    for deployment in ("open", "closed"):
        directory = ROOT / deployment
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
        args = Namespace(**manifest["request_overrides"], stream=manifest["stream"], model=manifest["model_override"])
        assert manifest["input_sha256"] == source_hash
        rows = read_rows(directory / "summary.jsonl")
        idx = {(r["line_no"], r["attempt"], r["variant"]): r for r in rows}
        assert len(idx) == len(rows) == 104
        assert set(idx) == {(line, attempt, variant) for line in range(1,14) for attempt in range(1,5) for variant in ("vanilla", "dtb")}
        by_id = {r["server_request_id"]: r for r in rows}
        phases, summaries = [], {}
        for log in sorted(directory.glob("server_filtered_part_*")):
            for line_no, line in enumerate(log.read_text(encoding="utf8").splitlines(), 1):
                for marker in ("DTB_PHASE ", "DTB_REQUEST_SUMMARY "):
                    if marker not in line:
                        continue
                    event = json.loads(line.split(marker, 1)[1])
                    event["source_file"] = str(log.relative_to(ROOT))
                    event["source_line"] = line_no
                    rid = event["parent_request_id"]
                    assert rid in by_id and by_id[rid]["variant"] == "dtb"
                    if marker == "DTB_PHASE ":
                        phases.append(event)
                    else:
                        assert rid not in summaries
                        summaries[rid] = event
        assert len(summaries) == 52
        expected_policy = "dtb-algorithm-v4" + ("-trial-close-exp1" if deployment == "closed" else "")
        assert all(p["policy_version"] == expected_policy for p in phases)
        for rid, summary in summaries.items():
            ps = [p for p in phases if p["parent_request_id"] == rid]
            assert sum(p["generated_tokens"] for p in ps) == summary["total_model_generated_tokens"]
            assert len([p for p in ps if p["child_request_id"]]) == summary["child_generate_calls"]
            assert summary["emitted_completion_tokens"] == by_id[rid]["usage"]["completion_tokens"]
        request_records = set()
        for row in read_rows(directory / "chunks.jsonl"):
            if row.get("record_type") == "request":
                key = (row["line_no"], row["attempt"], row["variant"])
                assert key not in request_records
                request_records.add(key)
                body = build_body(sources[row["line_no"]-1]["request_body"], variant=row["variant"], client_request_id=row["client_request_id"], args=args)
                assert json_hash(body) == row["request_body_sha256"]
                body.pop("request_id")
                normalized_hash = json_hash(body)
                if key in request_hashes:
                    assert request_hashes[key] == normalized_hash
                request_hashes[key] = normalized_hash
        assert request_records == set(idx)
        chunks = read_rows(directory / "chunks.jsonl")
        fingerprints = Counter(r.get("chunk", {}).get("system_fingerprint") for r in chunks if isinstance(r.get("chunk"), dict) and r["chunk"].get("system_fingerprint"))
        for file in directory.iterdir():
            if file.is_file():
                report["hashes"][str(file.relative_to(ROOT))] = hashlib.sha256(file.read_bytes()).hexdigest()
        group = {"client": {}, "phase": phase_stats(phases), "probe": probe_stats(phases),
                 "phase_count": len(phases), "summary_count": len(summaries),
                 "policy": dict(Counter(p["policy_version"] for p in phases)),
                 "intervention": dict(Counter(s["intervention"]["intervention_status"] for s in summaries.values())),
                 "model_work_complete": dict(Counter(s["model_work_complete"] for s in summaries.values())),
                 "budget_resolution": dict(Counter(s["budget_resolution"] for s in summaries.values())),
                 "decision_signatures": [], "role_probes": {}, "ledger": {},
                 "fingerprints": dict(fingerprints),
                 "http_status": dict(Counter(r["status_code"] for r in rows)),
                 "title_contract_failures": [dict(line=r["line_no"],attempt=r["attempt"],variant=r["variant"],content=r["content"],empty=not r["content"].strip()) for r in rows if "auxiliary" in r["case_id"] and (not r["content"].strip() or "\n" in r["content"].strip() or len(r["content"].strip())>50)],
                 "undeclared_tool_names": [dict(line=r["line_no"],attempt=r["attempt"],variant=r["variant"],name=t["name"]) for r in rows for t in r["tool_calls"] if t["name"] not in {tool["function"]["name"] for tool in sources[r["line_no"]-1]["request_body"].get("tools",[])}],
                 "content_boundary_leaks": [dict(line=r["line_no"],attempt=r["attempt"],variant=r["variant"],content=r["content"]) for r in rows if "</think>" in r["content"] or "<think>" in r["content"]],
                 "anomalies": [r for r in rows if r["anomaly_reasons"]]}
        for variant in ("vanilla", "dtb"):
            for role in ("all", "title", "business"):
                selected = [r for r in rows if r["variant"] == variant and
                            (role == "all" or ("auxiliary" in r["case_id"]) == (role == "title"))]
                group["client"][variant + "/" + role] = stats(selected)
        for role in ("title", "business"):
            group["role_probes"][role] = probe_stats([p for p in phases if
                ("auxiliary" in by_id[p["parent_request_id"]]["case_id"]) == (role == "title")])
        for k in ("thinking_tokens", "hidden_probe_tokens", "final_answer_tokens", "total_model_generated_tokens", "child_generate_calls", "emitted_completion_tokens"):
            group["ledger"][k] = sum(s[k] for s in summaries.values())
        decisions = [p for p in phases if p["phase"] == "decision"]
        for rid, summary in summaries.items():
            assert sum(p["parent_request_id"] == rid for p in decisions) == int(summary["budget_resolution"] == "resolved")
        for p in decisions:
            b = p["budget_decision"]
            assert b["decision_branch"] == "fixed"
            assert b["budget"] == {"check_length":850,"min_think_budget":1,"num_thinking":5,"think_stride":500}
            assert b["confidence_threshold"] == .93 and b["hard_think_token_limit"] == 20000
        signatures = Counter(json.dumps({k:p["budget_decision"][k] for k in ("decision_branch", "budget", "confidence_threshold", "percentile_step", "source_by_field", "hard_think_token_limit")}, sort_keys=True) for p in decisions)
        group["decision_signatures"] = [{"count":count,"signature":json.loads(sig)} for sig,count in signatures.items()]
        group["final_reasons"] = dict(Counter(p.get("final_answer_reason") for p in phases if p["phase"]=="final_answer"))
        report["groups"][deployment] = group
        all_data[deployment] = (idx, phases, summaries)
    for line in range(1,14):
        item = {"line":line,"case_id":all_data["open"][0][line,1,"dtb"]["case_id"]}
        for deployment,(idx,phases,summaries) in all_data.items():
            ps = [p for p in phases if p["parent_request_id"] in {idx[line,a,"dtb"]["server_request_id"] for a in range(1,5)}]
            item[deployment] = {v:stats([idx[line,a,v] for a in range(1,5)]) for v in ("vanilla","dtb")}
            item[deployment]["probe"] = probe_stats(ps)
            item[deployment]["thinking_tokens"] = sum(summaries[idx[line,a,"dtb"]["server_request_id"]]["thinking_tokens"] for a in range(1,5))
        report["case_comparison"].append(item)
        for attempt in range(1,5):
            row = {"line":line,"attempt":attempt}
            for deployment,(idx,phases,summaries) in all_data.items():
                r=idx[line,attempt,"dtb"]
                ps=[p for p in phases if p["parent_request_id"]==r["server_request_id"]]
                row[deployment]={"e2e_s":r["elapsed_ms"]/1000,"summary":summaries[r["server_request_id"]],"phases":ps}
            report["request_comparison"].append(row)
    report["comparisons"] = {}
    for scope in ("all", "title", "business", "without_line2", "manual_protocol_clean"):
        # Manual exclusions are published, not silently removed from headline totals.
        keys = [(line,a) for line in range(1,14) for a in range(1,5)
                if (scope not in ("title","business") or ("auxiliary" in all_data["open"][0][line,a,"dtb"]["case_id"]) == (scope=="title"))
                and (scope!="without_line2" or line!=2)
                and (scope!="manual_protocol_clean" or (line,a) not in ((12,2),(7,3)))]
        view = {}
        for d in ("open","closed"):
            idx = all_data[d][0]
            view[d] = {v:stats([idx[line,a,v] for line,a in keys]) for v in ("vanilla","dtb")}
            view[d]["gain_vs_vanilla_pct"] = 100*(1-view[d]["dtb"]["e2e_s"]/view[d]["vanilla"]["e2e_s"])
            view[d]["faster_than_vanilla"] = sum(idx[line,a,"dtb"]["elapsed_ms"]<idx[line,a,"vanilla"]["elapsed_ms"] for line,a in keys)
        view["closed_vs_open_gain_pct"] = 100*(1-view["closed"]["dtb"]["e2e_s"]/view["open"]["dtb"]["e2e_s"])
        view["closed_faster_pairs"] = sum(all_data["closed"][0][line,a,"dtb"]["elapsed_ms"]<all_data["open"][0][line,a,"dtb"]["elapsed_ms"] for line,a in keys)
        report["comparisons"][scope] = view
    report["normalized_request_hashes_verified"] = len(request_hashes)
    (ROOT / "analysis.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"comparisons":report["comparisons"], "normalized_request_hashes_verified":len(request_hashes)},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
