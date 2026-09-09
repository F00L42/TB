"""Offline G0/G1 audit; no model, network, or tool execution.

Writes compact evidence without copying archived prompts/tool arguments.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))
from tools.dtb.g0_support import digest, read_json, read_jsonl, write_json
from tools.dtb.replay_tool_calls import assemble_observation, build_body, json_hash

ROOT = Path(__file__).parent
DATA = ROOT / "g0_production"


def main():
    evidence = {"decoder_runs": [], "replays": [], "captures": []}
    captures = {}
    for file in sorted((DATA / "captured").glob("profile-*.json")):
        p = read_json(file)["profiles"][0]
        captures[p["id"]] = p
        evidence["captures"].append({"id": p["id"], "request_id": p["request_id"],
            "prompt_tokens": len(p["prompt_token_ids"]), "prompt_sha256": digest(p["prompt_token_ids"]),
            "sampling_sha256": digest(p["sampling_params"]), "identity": p["identity"],
            "max_tokens": p["sampling_params"]["max_tokens"],
            "stop_token_ids": p["sampling_params"]["stop_token_ids"]})
    for file in sorted(DATA.rglob("summary.json")):
        folder = file.parent
        summary, manifest = read_json(file), read_json(folder / "manifest.json")
        rows = read_jsonl(folder / "results.jsonl")
        counts = Counter(r["status"] for r in rows)
        assert dict(counts) == summary["counts"]
        keys = [(r["profile"],r["fixture"],r["trigger"],r["pending"],r["suffix"],r["string_stop_control"]) for r in rows]
        assert len(set(keys)) == len(rows)
        groups = defaultdict(Counter)
        by_fixture, failure_flags = defaultdict(Counter), Counter()
        examples = {}
        for row in rows:
            key = f"{row['profile']}/{row['fixture']}/{row['string_stop_control']}/{row['pending']}/{row['suffix']}"
            groups[key][row["status"]] += 1
            by_fixture[row["fixture"]][row["status"]] += 1
            if row["status"] != "fail":
                continue
            f = read_json(folder / row["failure_ref"])
            assert f["status"] == "fail"
            for field in ("text_equal", "direct_decoder_equal", "token_ids_equal"):
                failure_flags[f"{field}={f[field]}"] += 1
            if "system\n# Tools" in f["actual"]:
                failure_flags["prompt_tools_echo"] += 1
            if any("system\n# Tools" in t["text"] for t in f["resumed"]["trace"]):
                failure_flags["resumed_delta_prompt_echo"] += 1
            example_key = f"{row['fixture']}/{row['suffix']}"
            if example_key not in examples:
                examples[example_key] = {"file": row["failure_ref"], "trigger": f["trigger"],
                    "pending": f["pending"], "restart": f["restart"],
                    "prefix_tail_ids": f["prefix_ids"][-8:],
                    "actual_chars": len(f["actual"]), "expected_chars": len(f["expected"]),
                    "actual_head": f["actual"][:80], "expected_head": f["expected"][:80],
                    "first_difference": f["first_text_difference"]}
        assert {k:dict(v) for k,v in groups.items()} == summary["groups"]
        assert len(list((folder/"failures").glob("*.json"))) == counts["fail"]
        for p in manifest["profiles"]:
            if p["source"] == "live_captured":
                assert p == captures[p["id"]]
                assert manifest["profile_identity_match"][p["id"]] is True
                assert p["identity"] == manifest["runtime_identities"][p["id"]]
        evidence["decoder_runs"].append({"run": folder.relative_to(DATA).as_posix(),
            "counts": dict(counts), "by_fixture": {k:dict(v) for k,v in by_fixture.items()},
            "failure_flags": dict(failure_flags), "examples": examples,
            "profiles": [p["id"] for p in manifest["profiles"]],
            "repo_commit": manifest["repo_commit"], "script_sha256": manifest["script_sha256"],
            "exit_code": summary["exit_code"],
            "no_string_stop": dict(Counter(r["status"] for r in rows if not r["string_stop_control"])),
            "failure_cut_signature": digest([k[1:] for k,r in zip(keys,rows) if r["status"] == "fail"]),
            "near_850_original_trigger_ge_850": dict(Counter(r["status"] for r in rows
                if r["fixture"] == "unicode-near-850" and r["suffix"] == "original" and r["trigger"] >= 850))})

    source = REPO / "docs/log/perf/agent_minimum_intervention/input/g0/prepared/requests.jsonl"
    cases = read_jsonl(source)
    input_hash = hashlib.sha256(source.read_text(encoding="utf-8").encode()).hexdigest()
    assert cases == read_jsonl(DATA / "prepared_check/requests.jsonl")
    all_ids = defaultdict(list)
    for name in ("native_capture", "smoke", "smoke_more"):
        folder = DATA/name
        manifest = read_json(folder/"manifest.json")
        assert manifest["input_sha256"] == input_hash
        rows = read_jsonl(folder/"summary.jsonl")
        chunks = read_jsonl(folder/"chunks.jsonl")
        requests, payloads = {}, defaultdict(list)
        for c in chunks:
            if c.get("record_type") == "request":
                assert c["client_request_id"] not in requests
                requests[c["client_request_id"]] = c
            else:
                assert not c.get("parse_error")
                if c.get("chunk") is not None:
                    payloads[c["client_request_id"]].append(c["chunk"])
        assert len(rows) == len(requests) == len(manifest["cases"])*len(manifest["variants"])*manifest["repetitions"]
        result_rows = []
        for r in rows:
            client_id = r["client_request_id"]
            args = SimpleNamespace(**manifest["request_overrides"],model=manifest["model_override"],stream=True)
            body = build_body(cases[r["line_no"]-1]["request_body"],variant=r["variant"],client_request_id=client_id,args=args)
            assert json_hash(body) == requests[client_id]["request_body_sha256"]
            kwargs = {k:r[k] for k in ("line_no","attempt","variant","client_request_id","status_code","elapsed_ms",
                      "expected_tool_call","case_id","category","expected_tool_names","latency_eligible")}
            rebuilt = asdict(assemble_observation(**kwargs,chunks=payloads[client_id],request_tools=body.get("tools")))
            for field in ("content","reasoning","tool_calls","finish_reasons","terminal_count","usage",
                          "protocol_anomaly_reasons","semantic_anomaly_reasons"):
                assert rebuilt[field] == r[field], (name,client_id,field)
            assert r["status_code"] == 200 and r["terminal_count"] == 1
            all_ids[r["server_request_id"]].append(name)
            result_rows.append({k:r[k] for k in ("line_no","variant","server_request_id","elapsed_ms","first_output_ms",
                               "usage","protocol_anomaly_reasons","semantic_anomaly_reasons","latency_eligible")}
                              | {"tool_names": [t["name"] for t in r["tool_calls"]],
                                 "reasoning_chars": len(r["reasoning"])})
        paired = []
        for line in manifest["selected_lines"]:
            pair = {r["variant"]:r for r in result_rows if r["line_no"] == line}
            if set(pair) == {"vanilla","dtb"}:
                v,d = pair["vanilla"],pair["dtb"]
                paired.append({"line":line,"gain_ms":v["elapsed_ms"]-d["elapsed_ms"],
                    "first_output_gap_ms":v["first_output_ms"]-d["first_output_ms"],
                    "gain_percent":100*(1-d["elapsed_ms"]/v["elapsed_ms"])})
        evidence["replays"].append({"run":name,"rows":result_rows,"pairs":paired})
    evidence["cross_run_duplicate_parent_ids"] = {k:v for k,v in all_ids.items() if len(v)>1}
    evidence["all_integrity_assertions_passed"] = True
    write_json(ROOT/"analysis_evidence.json", evidence)
    print(json.dumps({"decoder_runs":len(evidence["decoder_runs"]),
        "captured_profiles":len(captures),"client_requests":sum(len(r["rows"]) for r in evidence["replays"]),
        "integrity":"passed","output":str(ROOT/"analysis_evidence.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
