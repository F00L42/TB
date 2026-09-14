"""Offline full-task audit of close+.90 vs vanilla-06; no external execution."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "agent_run06"))
import analyze_results as base
REPO = base.REPO
PROXY = REPO / "proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed"
LC = REPO / "lightcode/session_logs_dtb_06_closed"


def main():
    data, raws = {}, {}
    data["dtb"], raws["dtb"] = base.load_variant("dtb", proxy=PROXY, lc=LC,
        correlation=ROOT/"dtb_correlation", output_dir=ROOT)
    data["vanilla"], raws["vanilla"] = base.load_variant("vanilla", output_dir=ROOT)
    events, summaries = defaultdict(list), {}
    for path in sorted(PROXY.glob("server_filtered_part_*")):
        for n, line in enumerate(path.read_text(encoding="utf8").splitlines(), 1):
            m = re.search(r"(DTB_PHASE|DTB_REQUEST_SUMMARY) (\{.*\})", line)
            if not m:
                continue
            p = json.loads(m[2]); p.update(source_file=str(path.relative_to(REPO)), source_line=n)
            if m[1] == "DTB_PHASE":
                events[p["parent_request_id"]].append(p)
            else:
                assert p["parent_request_id"] not in summaries
                summaries[p["parent_request_id"]] = p
    idx = {r["parent_request_id"]:r for r in data["dtb"]["requests"]}
    assert None not in idx and len(idx) == len(data["dtb"]["requests"])
    coverage = dict(client=len(idx),phase_parents=len(events),summary_parents=len(summaries),
        missing_phase=sorted(set(idx)-set(events)), extra_phase=sorted(set(events)-set(idx)),
        missing_summary=sorted(set(idx)-set(summaries)),extra_summary=sorted(set(summaries)-set(idx)))
    coverage.update(total_phase_records=sum(map(len,events.values())),
        selected_phase_records=sum(len(ps) for parent,ps in events.items() if parent in idx),
        excluded_phase_records=sum(len(ps) for parent,ps in events.items() if parent not in idx))
    assert not coverage["missing_phase"] and not coverage["missing_summary"]
    # A shared server fragment contains other requests: only whitelist parents count.
    events = {parent:ps for parent,ps in events.items() if parent in idx}
    summaries = {parent:sm for parent,sm in summaries.items() if parent in idx}
    anomalies, markers, triggers, mismatch = [], [], [], []
    phase_totals = defaultdict(Counter)
    for v, group in data.items():
        assert all(r["task_id"] for r in group["requests"])
        for r in group["requests"]:
            original, obs = raws[v][r["request_id"]]
            declared = {t["function"]["name"] for t in original["request_body"].get("tools",[])}
            r["undeclared_tool_names"] = [t["name"] for t in obs["tool_calls"] if t["name"] not in declared]
            if r["protocol_anomalies"] or r["undeclared_tool_names"]:
                anomalies.append(dict(variant=v,request=r,observation=obs))
            if any(s in obs["content"] for s in ("</think>","<think>","<Thinking Explorer>")):
                markers.append(dict(variant=v,request=r,content=obs["content"],reasoning_tail=obs["reasoning"][-1800:]))
    for parent, r in idx.items():
        sm = summaries.get(parent); ps = events.get(parent,[])
        if not sm:
            continue
        model = [p for p in ps if p["child_request_id"]]
        for field, actual, expected in (
            ("children",len(model),sm["child_generate_calls"]),
            ("generated",sum(p["generated_tokens"] for p in model),sm["total_model_generated_tokens"]),
            ("hidden",sum(p["generated_tokens"] for p in ps if p["phase"]=="confidence_probe"),sm["hidden_probe_tokens"]),
            ("emitted",r["metrics"]["completion_tokens"],sm["emitted_completion_tokens"]),
            ("terminal",r["terminal_count"],sm["final_terminal_count"])):
            if actual!=expected: mismatch.append(dict(parent=parent,field=field,actual=actual,expected=expected))
        assert sm["policy_version"] == "dtb-algorithm-v4-trial-close-exp1"
        for p in ps:
            assert p["policy_version"] == sm["policy_version"]
            total = phase_totals[p["phase"]]; total["calls"]+=1
            for k in ("wall_ms","queue_ms","prefill_ms","decode_ms","controller_cpu_ms","generated_tokens","prompt_tokens","cached_prompt_tokens"):
                if p.get(k) is not None: total[k]+=p[k]; total[k+"_known"]+=1
            if p["phase"]=="decision":
                b=p["budget_decision"]
                assert b["decision_branch"]=="fixed" and b["confidence_threshold"]==.9
                assert b["budget"]==dict(check_length=850,min_think_budget=1,num_thinking=5,think_stride=500)
                assert b["hard_think_token_limit"]==20000
        r["summary"] = sm
        if sm["intervention"]["intervention_status"]=="triggered":
            triggers.append(dict(request=r,phases=ps,summary=sm))
    assert not mismatch
    assert len(summaries)==len(events)==len(idx)
    expected_fixed=dict(fixed_budget_tokens=20000,fixed_check_length=850,
        fixed_think_stride=500,fixed_num_thinking=5,fixed_min_think_tokens=1,fixed_prob_th=.9)
    for r in data["dtb"]["requests"]:
        assert all(r["config"][k]==v for k,v in expected_fixed.items())
    pairs=[]
    by_case={v:{t["case"]:t for t in g["tasks"].values()} for v,g in data.items()}
    assert set(by_case["dtb"])==set(by_case["vanilla"])
    for case in sorted(by_case["dtb"],key=int):
        d,v=by_case["dtb"][case],by_case["vanilla"][case]
        assert d["root_prompt_hash"]==v["root_prompt_hash"]
        row=dict(case=case,root_prompt_hash=d["root_prompt_hash"])
        for k in ("main_activity_span_s","llm_e2e_s","reasoning_tokens_local","completion_tokens","request_count"):
            row[k]=dict(vanilla=v[k],dtb=d[k],gain_pct=base.pct(v[k],d[k]))
        row["quality"]={label:dict(compilation=(t["report"] or {}).get("compilation"),dt_results=(t["report"] or {}).get("dt_results"),report_file=t["report_file"]) for label,t in (("vanilla",v),("dtb",d))}
        pairs.append(row)
    aggregates={}
    for label,cases in (("all",set(by_case["dtb"])),("both_report_all_pass",{"28","34"}),
                        ("both_raw_all_pass",{"28","34","35"})):
        subset=[p for p in pairs if p["case"] in cases]
        aggregates[label]={"cases":sorted(cases,key=int)}
        for k in ("main_activity_span_s","llm_e2e_s","reasoning_tokens_local","completion_tokens","request_count"):
            v=sum(p[k]["vanilla"] for p in subset); d=sum(p[k]["dtb"] for p in subset)
            aggregates[label][k]=dict(vanilla=v,dtb=d,gain_pct=base.pct(v,d),
                macro_gain_pct=sum(p[k]["gain_pct"] for p in subset)/len(subset))
    roles={}
    for v,g in data.items():
        roles[v]={}
        for role in ("all","auxiliary","main","subagent","business"):
            rows=[r for r in g["requests"] if role=="all" or r["role"]==role or (role=="business" and r["role"]!="auxiliary")]
            lengths=[r["metrics"]["reasoning_tokens_local"] for r in rows]
            roles[v][role]=dict(n=len(rows),llm_e2e_s=sum(r["metrics"]["e2e_ms"] for r in rows)/1000,
                reasoning_tokens=sum(lengths),completion_tokens=sum(r["metrics"].get("completion_tokens",0) for r in rows),
                reasoning_p50=base.quantile(lengths,.5),reasoning_p95=base.quantile(lengths,.95),
                longer_than_850=sum(x>850 for x in lengths))
    probes=[p for ps in events.values() for p in ps if p["phase"]=="confidence_probe"]
    bodymaps={}
    for v,g in data.items():
        bodymaps[v]=defaultdict(list)
        for r in g["requests"]: bodymaps[v][r["body_hash_without_dtb"]].append(r)
    exact=[]
    for h in bodymaps["dtb"].keys() & bodymaps["vanilla"].keys():
        d,v=bodymaps["dtb"][h],bodymaps["vanilla"][h]
        if len(d)==len(v)==1:
            exact.append(dict(hash=h,case=d[0]["case"],role=d[0]["role"],dtb=d[0]["request_id"],vanilla=v[0]["request_id"],dtb_metrics=d[0]["metrics"],vanilla_metrics=v[0]["metrics"]))
    evidence=dict(coverage=coverage,phase_mismatches=mismatch,variants=data,task_pairs=pairs,aggregates=aggregates,
        role_totals=roles,phase_totals={k:dict(v) for k,v in phase_totals.items()},triggered=triggers,
        intervention=dict(Counter(s["intervention"]["intervention_status"] for s in summaries.values())),
        model_work_complete=dict(Counter(s["model_work_complete"] for s in summaries.values())),
        empty_final_candidates=[dict(variant=v,request=r,reasoning=raws[v][r["request_id"]][1]["reasoning"]) for v,g in data.items() for r in g["requests"] if not r["content_chars"] and not r["tool_call_count"]],
        probe=dict(calls=len(probes),accepted=sum(p["accepted"] for p in probes),first_accepted=sum(p["accepted"] and p["round"]==1 for p in probes),stops=dict(Counter(p["stop_reason"] for p in probes))),
        ledger={k:sum(s[k] for s in summaries.values()) for k in ("thinking_tokens","hidden_probe_tokens","final_answer_tokens","total_model_generated_tokens","emitted_completion_tokens","child_generate_calls")},
        anomalies=anomalies,content_marker_candidates=markers,exact_body_pairs=exact)
    base.write(ROOT/"analysis.json",evidence)
    for v,g in data.items():
        (ROOT/f"{v}_request_index.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in g["requests"]),encoding="utf8")
    paths=[Path(__file__),ROOT.parent/"agent_run06/analyze_results.py",REPO/"tools/dtb/replay_tool_calls.py",REPO/"tools/dtb/correlate_lightcode_proxy.py"]
    for proxy,lc,corr in ((PROXY,LC,ROOT/"dtb_correlation"),
        (REPO/"proxy/results/qwen/local-task29/local-apiserver/vanilla/run-06",REPO/"lightcode/session_logs_vanilla_06",ROOT.parent/"agent_run06/vanilla_correlation")):
        paths.extend((proxy/"full_log").glob("*/part-*.jsonl")); paths.extend(proxy.glob("server_filtered_part_*"))
        paths.extend([proxy/"chunks.jsonl",lc/"evaluation_results.json",corr/"requests.jsonl"])
        paths.extend(lc.glob("*/logs/evaluation_report_*.json")); paths.extend(lc.glob("*/logs/sessions/*.json"))
    base.write(ROOT/"extraction_manifest.json",dict(deployment_manifest_available=False,
        coverage=coverage,phase_mismatches=mismatch,warning="HTTP 200 in parser harness is an assumption, not recovered HTTP status; aborted model work is a lower bound.",
        inputs=[dict(path=str(p.relative_to(REPO)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]))
    print(json.dumps({k:evidence[k] for k in ("phase_mismatches","intervention","probe","ledger","aggregates")},ensure_ascii=False,indent=2))


if __name__=="__main__": main()
