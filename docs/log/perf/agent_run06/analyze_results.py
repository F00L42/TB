"""Offline run-06 audit. Never sends requests or executes Agent tool calls."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]
sys.path.insert(0, str(REPO))
from tools.dtb.replay_tool_calls import assemble_observation


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def pct(v, d):
    return 100 * (1-d/v) if v else None


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for x in value:
            yield from strings(x)
    elif isinstance(value, dict):
        for x in value.values():
            yield from strings(x)


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(x.get("text", "") for x in value if isinstance(x, dict))
    return ""


def quantile(values, q):
    s = sorted(values)
    if not s:
        return None
    pos = (len(s)-1)*q
    low = int(pos)
    return s[low] + (s[min(low+1, len(s)-1)]-s[low])*(pos-low)


def load_variant(variant, *, proxy=None, lc=None, correlation=None, output_dir=None):
    # Explicit paths allow follow-up audits without rewriting historical artifacts.
    proxy = proxy or REPO / f"proxy/results/qwen/local-task29/local-apiserver/{variant}/run-06"
    lc = lc or REPO / f"lightcode/session_logs_{variant}_06"
    output_dir = output_dir or ROOT
    correlation = correlation or ROOT/f"{variant}_correlation"
    corr = {r["request_id"]: r for r in jsonl(correlation/"requests.jsonl")}
    tasks = {}
    call_owners = defaultdict(set)
    content_owners = defaultdict(set)
    root_owners = defaultdict(set)
    for ev in read(lc/"evaluation_results.json"):
        task_id = ev["task_id"]
        case = ev["file"].split("_")[0]
        session_files = sorted((lc/task_id/"logs/sessions").glob("*.json"))
        assert len(session_files) == 1, (variant, task_id, session_files)
        session = read(session_files[0])
        root_owners[digest(session["messages"][0].get("content"))].add(task_id)
        for token in set(re.findall(r"call_[a-zA-Z0-9]+", json.dumps(session))):
            call_owners[token].add(task_id)
        for msg in session["messages"]:
            if msg.get("role") == "assistant":
                for field in ("content", "reasoning"):
                    value = msg.get(field)
                    if isinstance(value, str) and len(value) >= 100:
                        content_owners[digest(value)].add(task_id)
        reports = sorted((lc/task_id/"logs").glob("evaluation_report_*.json"))
        report = read(reports[-1]) if reports else None
        times = [m["timestamp"] for m in session["messages"] if isinstance(m.get("timestamp"), (int,float))]
        part_ends = [p.get("time", {}).get("end", 0) for m in session["messages"] for p in m.get("parts", [])]
        tool_errors = []
        for m in session["messages"]:
            for p in m.get("parts", []):
                st = p.get("state", {})
                if p.get("type") == "tool" and st.get("status") == "error":
                    tool_errors.append({"message_id":m["id"], "tool":p.get("tool"),
                                        "call_id":p.get("callID"), "error":st.get("error"),
                                        "input":st.get("input"), "time":st.get("time")})
        tasks[task_id] = {"case":case, "file":ev["file"], "status":ev["status"],
            "session_id":session["sessionId"], "session_file":str(session_files[0].relative_to(REPO)),
            "report_file":str(reports[-1].relative_to(REPO)) if reports else None,
            "report":report, "platform_start":ev.get("start_time"), "platform_end":ev.get("end_time"),
            "main_message_start_ms":min(times), "main_message_end_ms":max(times),
            "main_message_span_s":(max(times)-min(times))/1000,
            "main_activity_span_s":(max([max(times), *[x for x in part_ends if x]])-min(times))/1000,
            "main_last_content":session["messages"][-1].get("content", "")[:4000],
            "tool_errors":tool_errors, "requests":[], "root_prompt_hash":digest(session["messages"][0].get("content"))}
    # Chunk timestamp is proxy request-start timestamp, not chunk arrival time.
    chunks_by_time = defaultdict(list)
    chunk_lines = defaultdict(list)
    for n, r in enumerate(jsonl(proxy/"chunks.jsonl"), 1):
        c = r.get("chunk")
        if isinstance(c, dict) and c.get("id"):
            chunks_by_time[r["timestamp"]].append(c)
            chunk_lines[r["timestamp"]].append(n)
    records = []
    supplement = []
    for path in sorted((proxy/"full_log").glob("*/part-*.jsonl")):
        for line, r in enumerate(jsonl(path), 1):
            records.append((r, str(path.relative_to(proxy)), line))
    records.sort(key=lambda t: (t[0]["timestamp"], t[0]["request_id"]))
    assert len({r[0]["request_id"] for r in records}) == len(records)
    assert len({r[0]["timestamp"] for r in records}) == len(records)
    index = []
    all_raw = {}
    for r, path, line in records:
        rid = r["request_id"]
        c = chunks_by_time.get(r["timestamp"], [])
        parent_ids = {x["id"] for x in c}
        assert len(parent_ids) <= 1, (variant, rid, parent_ids)
        obs = asdict(assemble_observation(line_no=line, attempt=1, variant=variant,
            client_request_id=rid, status_code=200, elapsed_ms=r["metrics"]["e2e_ms"],
            expected_tool_call=False, chunks=c, request_tools=r["request_body"].get("tools")))
        # status_code=200 is only a parser input; full logs do not prove HTTP status.
        for field in ("content", "reasoning"):
            assert obs[field] == (r["response_data"].get(field) or ""), (variant,rid,field)
        assert obs["usage"] == r["metrics"].get("usage", {}), (variant,rid,"usage")
        mapping = corr[rid]
        task_id = mapping.get("task_id")
        method = mapping.get("match_method")
        role = mapping.get("agent_role")
        if not task_id:
            evidence_ids = [t["id"] for t in obs["tool_calls"] if t["id"] in call_owners]
            owners = set().union(*(call_owners[x] for x in evidence_ids)) if evidence_ids else set()
            evidence_text = []
            for field in ("content", "reasoning"):
                h = digest(obs[field])
                if len(obs[field]) >= 100 and h in content_owners:
                    owners.update(content_owners[h]); evidence_text.append(h)
            if owners:
                assert len(owners) == 1, (rid, owners)
                task_id = next(iter(owners))
                role = "main"
                method = "exact_response_tool_call_id_or_full_assistant_text"
                supplement.append({"request_id":rid, "task_id":task_id, "method":method,
                    "tool_call_ids":evidence_ids, "assistant_text_sha256":evidence_text,
                    "source_file":path, "source_line":line, "session_file":tasks[task_id]["session_file"]})
        if not task_id:
            users = [m.get("content") for m in r["request_body"].get("messages", []) if m.get("role") == "user"]
            if users and isinstance(users[0], str) and users[0].startswith("Generate a title for this conversation:"):
                matches = [(digest(u), root_owners[digest(u)]) for u in users[1:] if digest(u) in root_owners]
                owners = set().union(*(o for _, o in matches)) if matches else set()
                if owners:
                    assert len(owners) == 1, (rid, owners)
                    task_id = next(iter(owners))
                    role = "auxiliary"
                    method = "title_embedded_exact_root_user_content"
                    supplement.append({"request_id":rid, "task_id":task_id, "method":method,
                        "root_content_sha256":[h for h, _ in matches], "source_file":path,
                        "source_line":line, "session_file":tasks[task_id]["session_file"]})
        deltas = [(ch, choice.get("delta", {})) for ch in c for choice in ch.get("choices", [])]
        max_delta = max((len(d.get("content") or "") + len(d.get("reasoning") or "") +
            sum(len((t.get("function") or {}).get("arguments") or "") for t in d.get("tool_calls", []))
            for _, d in deltas), default=0)
        row = {"request_id":rid, "parent_request_id":next(iter(parent_ids), None), "timestamp":r["timestamp"],
            "completed_at":r["completed_at"], "task_id":task_id, "case":tasks[task_id]["case"] if task_id else None,
            "role":role if task_id else "unmatched",
            "official_session_id":mapping.get("official_session_id") or (tasks[task_id]["session_id"] if task_id else ""),
            "match_method":method, "source_file":path, "source_line":line,
            "chunk_line_first":min(chunk_lines[r["timestamp"]],default=None), "chunk_line_last":max(chunk_lines[r["timestamp"]],default=None),
            "metrics":r["metrics"], "protocol_anomalies":obs["protocol_anomaly_reasons"],
            "schema_errors":obs.get("tool_schema_errors", obs.get("schema_errors")),
            "terminal_count":obs["terminal_count"], "finish_reasons":obs["finish_reasons"],
            "tool_call_count":len(obs["tool_calls"]), "tool_names":[t["name"] for t in obs["tool_calls"]],
            "reasoning_chars":len(obs["reasoning"]), "content_chars":len(obs["content"]),
            "max_sse_delta_chars":max_delta,
            "body_hash_without_dtb":digest({k:v for k,v in r["request_body"].items() if not k.startswith("fixed_") and k!='enable_think_budget'}),
            "config":{k:v for k,v in r["request_body"].items() if k not in ("messages", "tools")}}
        # Specific system prompt sentinels + exact prefix of request system text.
        system = [text_content(m.get("content")) for m in r["request_body"].get("messages", []) if m.get("role")=="system"]
        echo_hits = []
        for field, output in [("reasoning",obs["reasoning"]),("content",obs["content"]),
                              *[(f"tool_{t['index']}",t["arguments"]) for t in obs["tool_calls"]]]:
            for j, s in enumerate(system):
                prefix = s[:256]
                if len(prefix)>=128 and prefix in output:
                    echo_hits.append({"field":field,"system_index":j,"offset":output.index(prefix),"prefix_sha256":digest(prefix)})
            for marker in ("system\n# Tools", "<|im_start|>", "assistant\n<think>"):
                if marker in output:
                    echo_hits.append({"field":field,"marker":marker,"offset":output.index(marker)})
        row["echo_candidates"] = echo_hits
        index.append(row)
        all_raw[rid] = (r, obs)
        if task_id:
            tasks[task_id]["requests"].append(rid)
    assert len(chunks_by_time) == sum(r['parent_request_id'] is not None for r in index)
    for t in tasks.values():
        rows = [r for r in index if r["task_id"] and r["task_id"] == next(k for k,v in tasks.items() if v is t)]
        t["request_count"] = len(rows)
        t["llm_e2e_s"] = sum(r["metrics"]["e2e_ms"] for r in rows)/1000
        t["completion_tokens"] = sum(r["metrics"].get("completion_tokens",0) for r in rows)
        t["reasoning_tokens_local"] = sum(r["metrics"].get("reasoning_tokens_local",0) for r in rows)
        t["request_e2e_p50_s"] = quantile([r["metrics"]["e2e_ms"]/1000 for r in rows], .5)
        t["request_e2e_p95_s"] = quantile([r["metrics"]["e2e_ms"]/1000 for r in rows], .95)
    write(output_dir/f"{variant}_supplemental_links.json", supplement)
    (output_dir/f"{variant}_request_index.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in index),encoding="utf8")
    return {"tasks":tasks, "requests":index}, all_raw


def main():
    evidence = {"scope":"offline audit; no model execution; phase-on diagnostic, not performance release"}
    variants = {}; raws = {}
    for v in ("vanilla", "dtb"):
        variants[v], raws[v] = load_variant(v)
    phases = defaultdict(list); summaries = defaultdict(list); logcounts=Counter()
    log = REPO/"proxy/results/qwen/local-task29/local-apiserver/dtb/run-06/server_filtered_part_01"
    for line, s in enumerate(log.read_text(encoding="utf8").splitlines(),1):
        match = re.search(r"(DTB_PHASE|DTB_REQUEST_SUMMARY) (\{.*\})",s)
        if match:
            tag = match[1]; r=json.loads(match[2]); r["source_line"]=line
            (phases if tag=="DTB_PHASE" else summaries)[r["parent_request_id"]].append(r)
            logcounts[tag]+=1
        elif s.strip():
            logcounts["other"]+=1
    dtb_index={r["parent_request_id"]:r for r in variants["dtb"]["requests"] if r["parent_request_id"]}
    assert len(dtb_index)==sum(bool(r['parent_request_id']) for r in variants["dtb"]["requests"])
    whitelist=set(dtb_index)
    evidence["phase_coverage"]={"log_counts":dict(logcounts),"client_count":len(whitelist),
        "summaries_count":len(summaries),"phase_parents":len(phases),
        "client_without_summary":sorted(whitelist-set(summaries)),"client_without_phase":sorted(whitelist-set(phases)),
        "external_summary_parents":sorted(set(summaries)-whitelist),"external_phase_parents":sorted(set(phases)-whitelist)}
    triggered=[]; intervention=Counter(); phase_totals=defaultdict(Counter); mismatches=[]
    for parent in sorted(whitelist & set(summaries)):
        assert len(summaries[parent])==1, parent
        sm=summaries[parent][0]; pp=phases[parent]; rr=dtb_index[parent]
        it=sm.get("intervention",{}); intervention[it.get("intervention_status")]+=1
        modelph=[p for p in pp if p["phase"]!="decision"]
        assert len(modelph) == sm["child_generate_calls"], parent
        assert rr["terminal_count"] == sm["final_terminal_count"] == 1, parent
        assert sm["completion_status"] == "success" and sm["error"] is None, parent
        assert sum(p["generated_tokens"] for p in pp if p["phase"] == "confidence_probe") == sm["hidden_probe_tokens"], parent
        decision = [p for p in pp if p["phase"] == "decision"]
        if it.get("intervention_status") == "triggered":
            assert len(decision) == 1 and sm["budget_resolution"] == "resolved", parent
            budget = decision[0]["budget_decision"]
            assert budget["decision_branch"] == "fixed", parent
            assert budget["budget"] == {"check_length":850,"min_think_budget":1,"num_thinking":5,"think_stride":500}, parent
            assert budget["confidence_threshold"] == .93 and budget["hard_think_token_limit"] == 20000, parent
            assert it["abort_count"] == 1 and it["abort_terminal_seen"] and it["post_drain_branch"] == "confidence_probe", parent
        else:
            assert not decision and len(modelph) == 1 and sm["budget_resolution"] == "not_required_native", parent
            assert it["native_same_child_completed"] and it["abort_count"] == 0, parent
        generated=sum(p["generated_tokens"] for p in modelph)
        if generated != sm["total_model_generated_tokens"]:
            mismatches.append({"parent":parent,"field":"generated","phases":generated,"summary":sm["total_model_generated_tokens"]})
        if rr["metrics"].get("completion_tokens") != sm["emitted_completion_tokens"]:
            mismatches.append({"parent":parent,"field":"completion","proxy":rr["metrics"].get("completion_tokens"),"summary":sm["emitted_completion_tokens"]})
        for p in pp:
            total=phase_totals[p["phase"]]; total["calls"]+=1
            for k in ("wall_ms","queue_ms","prefill_ms","decode_ms","generated_tokens","prompt_tokens","cached_prompt_tokens"):
                if p.get(k) is not None: total[k]+=p[k]; total[k+"_known"]+=1
        rr["intervention"]=it; rr["summary"]=sm
        if it.get("intervention_status")=="triggered":
            original, obs=raws["dtb"][rr["request_id"]]
            triggered.append({"request":rr,"phases":pp,"summary":sm,
                "reasoning_preview":obs["reasoning"][:1600],"reasoning_tail":obs["reasoning"][-1000:],
                "content_preview":obs["content"][:800],"tool_preview":[{**x,"arguments":x["arguments"][:500]} for x in obs["tool_calls"]]})
    evidence["intervention_counts"]=dict(intervention)
    evidence["phase_totals"]={k:dict(v) for k,v in phase_totals.items()}
    evidence["phase_mismatches"]=mismatches
    evidence["triggered"]=triggered
    evidence["variants"]=variants
    assert not mismatches
    assert set(summaries) == set(phases) == whitelist
    assert all(r["task_id"] for variant in variants.values() for r in variant["requests"])
    # A task pair requires identical original root inputs; later Agent turns may diverge.
    by_case = {v:{t["case"]:t for t in data["tasks"].values()} for v,data in variants.items()}
    assert set(by_case["vanilla"]) == set(by_case["dtb"])
    pairs = []
    for case in sorted(by_case["vanilla"], key=int):
        v, d = by_case["vanilla"][case], by_case["dtb"][case]
        assert v["root_prompt_hash"] == d["root_prompt_hash"], case
        pair = {"case":case,"root_prompt_hash":v["root_prompt_hash"]}
        for field in ("main_activity_span_s","llm_e2e_s","request_count","completion_tokens","reasoning_tokens_local"):
            pair[field] = {"vanilla":v[field],"dtb":d[field],"gain_pct":pct(v[field],d[field])}
        pairs.append(pair)
    evidence["task_pairs"] = pairs
    # Exact full-body pairs are a smaller, separate set. Never pair by turn ordinal.
    body_groups = {}
    for variant, data in variants.items():
        body_groups[variant] = defaultdict(list)
        for r in data["requests"]:
            body_groups[variant][r["body_hash_without_dtb"]].append(r)
    exact_pairs = []; ambiguous = []
    for h in sorted(set(body_groups["vanilla"]) & set(body_groups["dtb"])):
        vr, dr = body_groups["vanilla"][h], body_groups["dtb"][h]
        if len(vr) != 1 or len(dr) != 1:
            ambiguous.append({"hash":h,"vanilla_count":len(vr),"dtb_count":len(dr)})
            continue
        v, d = vr[0], dr[0]
        exact_pairs.append({"hash":h,"case":v["case"],"role":v["role"],
            "vanilla_request_id":v["request_id"],"dtb_request_id":d["request_id"],
            "vanilla_metrics":v["metrics"],"dtb_metrics":d["metrics"],
            "intervention_status":d["intervention"]["intervention_status"]})
    evidence["exact_body_pairs"] = exact_pairs
    evidence["ambiguous_body_pairs"] = ambiguous
    lengths = []
    for variant, data in variants.items():
        for role in ("all","auxiliary","business"):
            scoped = [r for r in data["requests"] if role == "all" or (r["role"] == "auxiliary") == (role == "auxiliary")]
            rows = [r for r in scoped if r["terminal_count"] == 1 and not r["protocol_anomalies"]]
            values = [r["metrics"]["reasoning_tokens_local"] for r in rows]
            lengths.append({"variant":variant,"role":role,"count":len(rows),
                "excluded_request_ids":[r["request_id"] for r in scoped if r not in rows],"thinking_tokens":sum(values),
                "p50":quantile(values,.5),"p95":quantile(values,.95),"max":max(values),
                "thresholds":[{"T":t,"above":sum(x>t for x in values),"residual_tokens":sum(max(0,x-t) for x in values)}
                              for t in (500,850,1200,1500,2000)]})
    evidence["thinking_distribution"] = lengths
    write(ROOT/"comparison_summary.json", {k:evidence[k] for k in ("task_pairs","exact_body_pairs","ambiguous_body_pairs","thinking_distribution","phase_coverage","intervention_counts","phase_totals","phase_mismatches")})
    write(ROOT/"analysis_evidence.json",evidence)
    write(ROOT/"phase_requests.json", {k:{"summary":summaries[k],"phases":phases[k]} for k in sorted(whitelist)})
    # Local extraction identity, not a reconstructed server/deployment manifest.
    inputs = []
    for v in variants:
        proxy = REPO/f"proxy/results/qwen/local-task29/local-apiserver/{v}/run-06"
        inputs.extend(sorted((proxy/"full_log").glob("*/part-*.jsonl")))
        inputs.extend([proxy/"chunks.jsonl", REPO/f"lightcode/session_logs_{v}_06/evaluation_results.json",
                       ROOT/f"{v}_correlation/requests.jsonl"])
        for t in variants[v]["tasks"].values():
            inputs.append(REPO/t["session_file"])
            if t["report_file"]: inputs.append(REPO/t["report_file"])
        (ROOT/f"{v}_request_index.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in variants[v]["requests"]),encoding="utf8")
    inputs.extend([log, Path(__file__), REPO/"tools/dtb/replay_tool_calls.py", REPO/"tools/dtb/correlate_lightcode_proxy.py"])
    write(ROOT/"extraction_manifest.json", {"schema":"agent-run06-offline-audit-v1",
        "deployment_manifest_available":False,"parser_input_http_status_assumption":200,
        "warning":"HTTP status is not recovered; parsed SSE and raw logs are the evidence. Aborted model work is a lower bound.",
        "phase_coverage":evidence["phase_coverage"],
        "inputs":[{"path":str(p.relative_to(REPO)),"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs]})
    for v in variants:
        print(v, "requests",len(variants[v]["requests"]),"linked",sum(bool(x["task_id"]) for x in variants[v]["requests"]),
            "protocol",Counter(a for r in variants[v]["requests"] for a in r["protocol_anomalies"]))
        for tid,t in sorted(variants[v]["tasks"].items(),key=lambda x:int(x[1]["case"])):
            print(t["case"],t["request_count"],round(t["llm_e2e_s"],3),round(t["main_message_span_s"],3),
                  (t["report"] or {}).get("evaluation_conclusion",{}).get("grade","missing_report"))
    print("COVERAGE",evidence["phase_coverage"])
    print("INTERVENTION",dict(intervention),"TRIGGERED",len(triggered),"MISMATCHES",mismatches)
    print("PHASE",dict(phase_totals))


if __name__ == "__main__":
    main()
