"""Extract the 13 original HTTP inputs; no model calls or Agent tool execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
sys.path.insert(0, str(REPO))
from tools.dtb.replay_tool_calls import load_cases


def sha(value):
    return hashlib.sha256(value).hexdigest()


def main():
    source = ROOT.parent / "probe_analysis.json"
    data = json.loads(source.read_text(encoding="utf8"))
    rows = []; manifest = []
    for t in data["traces"]:
        relative = t["source_file"].replace("\\", "/")
        path = REPO / "proxy/results/qwen/local-task29/local-apiserver/dtb/run-06" / relative
        raw = json.loads(path.read_text(encoding="utf8").splitlines()[t["source_line"]-1])
        assert raw["request_id"] == t["request_id"]
        row = {
            "case_id":f"run06-{t['case']}-{t['role']}-{t['request_id']}",
            "request_body":raw["request_body"],
            "replay_expectation":{
                "category":"trial_close_single_variable",
                "expected_tool_call":False, "expected_tool_names":[],
                "latency_eligible":True,
            },
        }
        # No predetermined business action: validate all returned tool calls,
        # but do not require the same tool name as one historical stochastic run.
        rows.append(row)
        manifest.append({"line":len(rows),"case_id":row["case_id"],
            "role":t["role"],"source_request_id":raw["request_id"],
            "source_file":str(path.relative_to(REPO)).replace("\\", "/"),
            "source_line":t["source_line"],
            "request_body_sha256":sha(json.dumps(raw["request_body"],ensure_ascii=False,sort_keys=True,separators=(",",":")).encode())})
    assert len(rows)==13
    output = ROOT / "requests.jsonl"
    encoded = "".join(json.dumps(r,ensure_ascii=False)+"\n" for r in rows).encode()
    if output.exists() and output.read_bytes()!=encoded:
        raise RuntimeError("existing requests.jsonl differs; choose a new experiment directory")
    output.write_bytes(encoded)
    loaded = load_cases(output,1,0)
    assert len(loaded)==13
    report = {"scope":"historical HTTP contexts, not captured P/H checkpoints; tools are never executed",
        "latency_eligible_meaning":"same-input replay only; code/title quality gate is still required",
        "index_sha256":sha(source.read_bytes()),"requests_sha256":sha(encoded),
        "patch_sha256":sha((ROOT/"closed_trial.patch").read_bytes()),"cases":manifest}
    (ROOT/"input_manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"cases":len(rows),"bytes":len(encoded),"sha256":sha(encoded)},indent=2))


if __name__ == "__main__":
    main()
