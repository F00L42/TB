"""Offline audit of e73d099 DTB off reruns against vanilla-06. No model calls."""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

import openpyxl

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent / "agent_run06"))
import analyze_results as base
REPO = base.REPO


def workbook(lc):
    files = list(lc.glob("*.xlsx"))
    assert len(files) == 1, files
    path = files[0]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(values_only=True))
    headers = list(rows[0])
    assert headers[:7] == ["任务ID", "分支", "任务状态", "创建时间", "开始时间", "结束时间", "耗时"]
    result = {}
    for n, values in enumerate(rows[1:], 2):
        row = dict(zip(headers, values))
        end, start, create = [datetime.fromisoformat(row[k]) for k in ("结束时间", "开始时间", "创建时间")]
        duration = (end-create).total_seconds()
        assert abs(duration-row["耗时"]) < 1e-5
        result[row["任务ID"]] = dict(file=str(path.relative_to(REPO)), sheet="Sheet1", row=n,
            values=row, create_to_end_s=duration, start_to_end_s=(end-start).total_seconds())
    wb.close()
    assert len(result) == 9
    return result


def main():
    data, anomalies, coverage, identities = {}, {}, {}, {}
    files = set()
    for variant in ("vanilla", "run07", "run08"):
        lc = REPO/"lightcode"/("session_logs_vanilla_06" if variant == "vanilla" else f"session_logs_dtb_{variant[-2:]}")
        proxy = REPO/"proxy/results/qwen/local-task29/local-apiserver"/("vanilla/run-06" if variant == "vanilla" else f"dtb/run-{variant[-2:]}")
        corr = OUT.parent/"agent_run06/vanilla_correlation" if variant == "vanilla" else OUT/f"{variant}_correlation"
        print("Loading", variant, flush=True)
        group, raw = base.load_variant(variant, proxy=proxy, lc=lc, correlation=corr, output_dir=OUT)
        sheets = workbook(lc)
        assert set(sheets) == set(group["tasks"])
        for task_id, t in group["tasks"].items():
            t["xlsx"] = sheets[task_id]
            assert t["file"].removesuffix(".json") == t["xlsx"]["values"]["分支"]
            if t["report"]:
                report=t["report"]
                assert report["evaluation_conclusion"]["grade"] == t["xlsx"]["values"]["评级"]
                dt=report["dt_results"]
                for field,column in (("total_tests","DT总测试数"),("passed_tests","DT通过数"),("failed_tests","DT失败数")):
                    assert dt.get(field,0) == t["xlsx"]["values"][column], (task_id,field)
            # Keep no session-span metrics in this report's derived data.
            for k in list(t):
                if k.startswith("main_message_") or k == "main_activity_span_s":
                    del t[k]
        index = group["requests"]
        assert all(r["task_id"] for r in index)
        assert sum(t["request_count"] for t in group["tasks"].values()) == len(index)
        anomalies[variant] = []
        fingerprints = Counter()
        configs = Counter()
        for r in index:
            original, obs = raw[r["request_id"]]
            declared = {t["function"]["name"] for t in original["request_body"].get("tools", [])}
            undeclared = [t["name"] for t in obs["tool_calls"] if t["name"] not in declared]
            if r["protocol_anomalies"] or undeclared or r["echo_candidates"]:
                anomalies[variant].append(dict(request=r, observation=obs, undeclared=undeclared))
            configs[json.dumps(r["config"], sort_keys=True)] += 1
            if variant != "vanilla":
                expected=dict(enable_think_budget=True,fixed_budget_tokens=20000,fixed_check_length=850,
                    fixed_min_think_tokens=1,fixed_num_thinking=5,fixed_prob_th=.9,fixed_think_stride=500)
                assert all(r["config"].get(k)==value for k,value in expected.items())
        for row in base.jsonl(proxy/"chunks.jsonl"):
            if isinstance(row.get("chunk"), dict) and row["chunk"].get("system_fingerprint"):
                fingerprints[row["chunk"]["system_fingerprint"]] += 1
        identities[variant] = dict(config_counts=[dict(config=json.loads(k), requests=n) for k,n in configs.items()],
                                   sse_fingerprints=dict(fingerprints))
        coverage[variant] = dict(requests=len(index), tasks=len(group["tasks"]),
            reports=sum(t["report"] is not None for t in group["tasks"].values()),
            requests_with_sse=sum(r["parent_request_id"] is not None for r in index),
            tool_calls=sum(r["tool_call_count"] for r in index),
            anomaly_requests=len(anomalies[variant]))
        data[variant] = group
        files.update(proxy.glob("full_log/*/part-*.jsonl"))
        files.add(proxy/"chunks.jsonl")
        files.add(lc/"evaluation_results.json")
        files.update(lc.glob("*.xlsx"))
        files.update(lc.glob("*/logs/evaluation_report_*.json"))
        files.update(lc.glob("*/logs/sessions/*.json"))
    by_case = {v:{t["case"]:t for t in g["tasks"].values()} for v,g in data.items()}
    assert all(set(g) == set(by_case["vanilla"]) for g in by_case.values())
    for c in by_case["vanilla"]:
        assert len({g[c]["root_prompt_hash"] for g in by_case.values()}) == 1, c
    base.write(OUT/"analysis.json", dict(variants=data, coverage=coverage, identities=identities, anomalies=anomalies))
    base.write(OUT/"extraction_manifest.json", dict(archive_commit="e73d099", baseline="vanilla/run-06",
        observability={"run07":"off, user-declared", "run08":"off, user-declared"},
        inputs=[dict(path=str(p.relative_to(REPO)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(files)]))
    print(json.dumps(coverage, ensure_ascii=False, indent=2))
    for c in sorted(by_case["vanilla"],key=int):
        for v,g in by_case.items():
            t=g[c]; report=t["report"] or {}
            print(c,v,round(t["llm_e2e_s"],3),t["request_count"],
                json.dumps({k:report.get(k) for k in ("compilation","dt_results","evaluation_conclusion")},ensure_ascii=False))


if __name__ == "__main__":
    main()
