"""Offline evidence audit for b832b74; no model calls or parser modifications.

Compare complete serving text with SSE-assembled arguments. The independent
XML reader is deliberately limited to this archive's string-valued parameters;
it is not a general XML/Qwen parser or a replacement for fixed-delta regression.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
REPO = next(p for p in ROOT.parents if (p / "tools/dtb/replay_tool_calls.py").exists())
sys.path.insert(0, str(REPO))
from tools.dtb.replay_tool_calls import build_body, json_hash

COMPLETE = re.compile(r"Generated response (\S+) \(streaming complete\): output: (.*), output_token_ids: (.*), finish_reason: (.*)$")
FUNCTION = re.compile(r"<function=([^>]+)>(.*?)</function>", re.S)
PARAM = re.compile(r"<parameter=([^>]+)>(.*?)</parameter>", re.S)


def rows(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def xml_calls(text):
    result = []
    for f in FUNCTION.finditer(text):
        params = {}
        for p in PARAM.finditer(f[2]):
            assert p[1] not in params, "duplicate parameter requires separate review"
            value = p[2]
            # Strip only XML framing newlines, NOT meaningful whitespace in code.
            if value.startswith("\n"):
                value = value[1:]
            if value.endswith("\n"):
                value = value[:-1]
            params[p[1]] = value
        result.append({"name": f[1], "arguments": params})
    return result


def main():
    source = REPO / "docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl"
    cases = rows(source)
    source_hash = hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    report = {}
    for name in ("replay_capture", "replay_capture_audit"):
        folder = ROOT / name
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["input_sha256"] == source_hash
        observations = rows(folder / "summary.jsonl")
        by_id = {r["server_request_id"]: r for r in observations}
        by_client = {r["client_request_id"]: r for r in observations}
        assert len(by_id) == len(by_client) == len(observations) == 60
        assert {(r["line_no"],r["attempt"],r["variant"]) for r in observations} == {
            (line,a,v) for line in (2,4,10) for a in range(1,11) for v in ("vanilla","dtb")}
        args = argparse.Namespace(**manifest["request_overrides"],
                                  model=manifest["model_override"], stream=manifest["stream"])
        sse = defaultdict(dict)
        request_ids = []
        event_lines = defaultdict(list)
        for line_no, r in enumerate(rows(folder / "chunks.jsonl"), 1):
            key = r["client_request_id"]
            assert key in by_client
            if r.get("record_type") == "request":
                body = build_body(cases[r["line_no"]-1]["request_body"], variant=r["variant"],
                                  client_request_id=key,args=args)
                assert json_hash(body) == r["request_body_sha256"]
                request_ids.append(key)
                continue
            for choice in (r.get("chunk") or {}).get("choices", []):
                for tc in choice.get("delta", {}).get("tool_calls") or []:
                    item = sse[key].setdefault(tc["index"], {"name":"", "arguments":""})
                    fn = tc.get("function") or {}
                    item["name"] += fn.get("name") or ""
                    item["arguments"] += fn.get("arguments") or ""
                    event_lines[key].append(line_no)
        assert len(request_ids) == len(set(request_ids)) == 60
        for key,r in by_client.items():
            assert [sse[key][i] for i in sorted(sse[key])] == [
                {"name":t["name"],"arguments":t["arguments"]} for t in r["tool_calls"]]
        logs = list(folder.glob("server*"))
        assert len(logs) == 1
        log = logs[0]
        text = log.read_text(encoding="utf-8")
        seen = set()
        evidence = []
        for line_no,line in enumerate(text.splitlines(),1):
            m = COMPLETE.search(line)
            if not m:
                continue
            parent = m[1]
            assert parent in by_id and parent not in seen
            seen.add(parent)
            raw = ast.literal_eval(m[2])
            assert m[3] == "None" and m[4] == "streaming_complete"
            r = by_id[parent]
            expected = [{"name":t["name"],"arguments":json.loads(t["arguments"])} for t in r["tool_calls"]]
            actual = xml_calls(raw)
            assert actual == expected, (name,parent,"raw/SSE mismatch")
            assert raw.count("<tool_call>") == raw.count("</tool_call>") == len(actual)
            assert raw.count("<function=") == raw.count("</function>") == len(actual)
            assert raw.count("<parameter=") == raw.count("</parameter>") == sum(len(t["arguments"]) for t in actual)
            assert r["status_code"]==200 and r["terminal_count"]==1
            evidence.append(dict(parent_request_id=parent,line_no=r["line_no"],attempt=r["attempt"],
                variant=r["variant"],server_log=log.name,server_log_line=line_no,
                sse_tool_lines=event_lines[r["client_request_id"]],expected_tools=r["expected_tool_names"],
                observed_tools=[t["name"] for t in actual],raw_and_sse_arguments_equal=True,
                raw_text_sha256=hashlib.sha256(raw.encode()).hexdigest(),raw_text_chars=len(raw),
                protocol_anomalies=r["protocol_anomaly_reasons"],semantic_anomalies=r["semantic_anomaly_reasons"],
                # Keep all anomaly arguments (small bash/grep calls), not full generated code.
                anomaly_calls=actual if r["anomaly_reasons"] else []))
        assert seen == set(by_id)
        report[name] = dict(request_count=len(observations),complete_logs=len(seen),
            input_and_request_hashes_verified=True,raw_sse_summary_arguments_equal=True,
            added_requests=text.count("Added request "),native_observation_mentions=text.count("native_observation"),
            dtb_stream_audit_events=text.count("DTB_STREAM_AUDIT"),dtb_phase_events=text.count("DTB_PHASE"),
            tool_counts=dict(Counter(t["name"] for r in observations for t in r["tool_calls"])),
            counts={v:dict(requests=sum(r["variant"]==v for r in observations),
                protocol=dict(Counter(e for r in observations if r["variant"]==v for e in r["protocol_anomaly_reasons"])),
                semantic=dict(Counter(e for r in observations if r["variant"]==v for e in r["semantic_anomaly_reasons"]))) for v in ("vanilla","dtb")},
            evidence=evidence)
    (ROOT / "output_evidence.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Verified 120 complete logs, source/request hashes, raw XML = SSE = summary for all 133 tool calls.")


if __name__ == "__main__":
    main()
