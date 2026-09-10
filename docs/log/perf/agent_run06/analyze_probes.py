"""Read-only counterfactual bookkeeping for the recorded run-06 probe tree.

Writes derived artifacts only; never runs model requests or tools in Agent logs.
Frozen-trace estimates are not new-policy latency or quality measurements.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]


def main():
    evidence = json.loads((ROOT / "analysis_evidence.json").read_text(encoding="utf8"))
    traces = []
    for t in evidence["triggered"]:
        r = t["request"]
        path = REPO / "proxy/results/qwen/local-task29/local-apiserver/dtb/run-06" / r["source_file"]
        raw = json.loads(path.read_text(encoding="utf8").splitlines()[r["source_line"] - 1])
        text = raw["response_data"]["reasoning"]
        title = raw["response_data"]["content"].strip() if r["role"] == "auxiliary" else None
        prefix = 0
        probes = []
        for i, ph in enumerate(t["phases"]):
            if ph["phase"] in ("native_passthrough", "iterative_think"):
                prefix += ph["generated_tokens"]
            if ph["phase"] != "confidence_probe":
                continue
            probes.append({
                "phase_index": i, "prefix_tokens": prefix,
                **{k: ph[k] for k in ("round", "confidence", "value_min_raw", "pair_count", "value_pair_count", "generated_tokens", "stop_reason", "accepted", "wall_ms", "prefill_ms", "decode_ms", "source_line")},
                "value_min_probability": math.exp(ph["value_min_raw"]),
                "next_visible_segment_tokens": next((p["generated_tokens"] for p in t["phases"][i+1:] if p["phase"] == "iterative_think"), None),
            })
        traces.append({
            "case":r["case"], "role":r["role"], "request_id":r["request_id"],
            "parent_request_id":r["parent_request_id"], "source_file":r["source_file"], "source_line":r["source_line"],
            "thinking_tokens":t["summary"]["thinking_tokens"], "probes":probes,
            "reasoning_chars":len(text), "final_title":title,
            "final_title_first_char_offset":text.find(title) if title else None,
            "final_title_exact_occurrences":text.count(title) if title else None,
            "text_boundary_limit":"Character offsets are not source-token offsets; no exact text-at-probe-cut reconstruction.",
        })
    sensitivity = []
    for threshold in (.93, .92, .90, .85, .80, .75, .70, .60):
        for role in ("all", "auxiliary", "business"):
            selected = [t for t in evidence["triggered"] if role == "all" or (t["request"]["role"] == "auxiliary") == (role == "auxiliary")]
            accepted = 0; saved_calls = 0; saved_probe_wall = 0.; skipped_stride = 0.; skipped_tokens = 0
            decisions = []
            for t in selected:
                hit = next((i for i, p in enumerate(t["phases"]) if p["phase"] == "confidence_probe" and p["confidence"] >= threshold), None)
                if hit is None:
                    continue
                accepted += 1
                later = t["phases"][hit+1:]
                later_probes = [p for p in later if p["phase"] == "confidence_probe"]
                later_think = [p for p in later if p["phase"] == "iterative_think"]
                saved_calls += len(later_probes)
                saved_probe_wall += sum(p["wall_ms"] for p in later_probes)
                skipped_stride += sum(p["wall_ms"] for p in later_think)
                skipped_tokens += sum(p["generated_tokens"] for p in later_think)
                decisions.append({"request_id":t["request"]["request_id"], "round":t["phases"][hit]["round"], "confidence":t["phases"][hit]["confidence"]})
            sensitivity.append({"threshold":threshold,"role":role,"accepted_requests":accepted,
                "skipped_later_probe_calls":saved_calls, "skipped_later_probe_wall_s":saved_probe_wall/1000,
                "skipped_recorded_stride_tokens":skipped_tokens,"skipped_recorded_stride_wall_s":skipped_stride/1000,
                "first_accepts":decisions})
    truncation = []
    for limit in (10,20,50):
        for role in ("all", "auxiliary", "business"):
            phases = [p for t in evidence["triggered"] if role == "all" or (t["request"]["role"] == "auxiliary") == (role == "auxiliary") for p in t["phases"] if p["phase"] == "confidence_probe"]
            truncation.append({"limit":limit,"role":role,"calls":len(phases),
                "original_tokens":sum(p["generated_tokens"] for p in phases),
                "capped_tokens":sum(min(limit,p["generated_tokens"]) for p in phases),
                "fixed_prefill_s":sum(p["prefill_ms"] for p in phases)/1000,
                "original_decode_s":sum(p["decode_ms"] for p in phases)/1000,
                "linear_decode_saved_s":sum(p["decode_ms"]*max(0,p["generated_tokens"]-limit)/p["generated_tokens"] for p in phases)/1000,
                "wall_s":sum(p["wall_ms"] for p in phases)/1000})
    phases = [p for t in evidence["triggered"] for p in t["phases"] if p["phase"] == "confidence_probe"]
    assert len(traces)==13 and len(phases)==24
    assert all(p["pair_count"]==p["generated_tokens"] for p in phases)
    output = {"evidence_scope":"24 probes; trial text and individual sampled logprobs unavailable; no runtime counterfactual executed",
        "frozen_trace_warning":"First changed acceptance changes all later generation; skipped historical phases are bookkeeping opportunities, not measured new-policy speedups.",
        "truncation_warning":"Assumes same 24 invocations and unchanged prefixes, linear per-probe decode cost; prefix-10 confidence is unknown.",
        "stop_counts":dict(Counter(p["stop_reason"] for p in phases)),
        "value_min_above_093":sum(math.exp(p["value_min_raw"])>=.93 for p in phases),
        "traces":sorted(traces,key=lambda t:(int(t["case"]),t["role"],t["request_id"])),
        "threshold_sensitivity":sensitivity,"trial_length_cost_model":truncation}
    (ROOT / "probe_analysis.json").write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps({k:output[k] for k in ("stop_counts","value_min_above_093","trial_length_cost_model")},ensure_ascii=False,indent=2))
    for row in sensitivity:
        if row["role"] == "all": print({k:v for k,v in row.items() if k != "first_accepts"})
    for t in output["traces"]:
        if t["role"] == "auxiliary":print(t["case"],t["final_title_first_char_offset"],t["reasoning_chars"],t["final_title_exact_occurrences"])


if __name__ == "__main__":
    main()
