"""Read-only offline audit of the uploaded run; no model or server required.

Run from the repository root. Output is JSON on stdout. The reasoning check
executes the unmodified Qwen streaming method extracted from the pinned source,
with only its argument/data types stubbed; it is not an end-to-end vLLM test.
Exit zero means the evidence was reproduced, including the observed defect;
it does not mean product acceptance passed. Use the 915bec9 source snapshot.
"""
from __future__ import annotations

import ast
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
from types import SimpleNamespace
import types
from dataclasses import asdict


RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[4]
sys.path.insert(0, str(ROOT))
from tools.dtb.analyze_tool_call_run import classify, extract_server_record_parts
from tools.dtb.replay_tool_calls import assemble_observation, build_body, json_hash


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patched_bytes(patch, target):
    """Apply only the selected file's unified hunks in memory; verify context."""
    source = subprocess.check_output([
        "git", "-C", str(ROOT / "third_party/vllm"), "show", f"HEAD:{target}",
    ]).decode("utf-8").splitlines(keepends=True)
    lines = patch.read_text(encoding="utf-8").splitlines(keepends=True)
    result, cursor, active, i = [], 0, False, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            active = line[4:].split()[0] == "a/" + target
        if active and line.startswith("@@ "):
            start = int(re.match(r"@@ -(\d+)", line).group(1)) - 1
            result.extend(source[cursor:start])
            cursor = start
            i += 1
            while i < len(lines) and lines[i][:1] in (" ", "+", "-") and not lines[i].startswith("--- "):
                kind, text = lines[i][0], lines[i][1:]
                if kind in (" ", "-"):
                    assert source[cursor] == text, (target, cursor + 1)
                    cursor += 1
                if kind in (" ", "+"):
                    result.append(text)
                i += 1
            continue
        i += 1
    result.extend(source[cursor:])
    return "".join(result).encode("utf-8")


def reasoning_method():
    path = ROOT / "third_party/vllm/vllm/reasoning/qwen3_reasoning_parser.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "extract_reasoning_streaming")
    isolated = ast.Module(body=[method], type_ignores=[])
    ns = {"Sequence": list, "DeltaMessage": SimpleNamespace}
    exec(compile(isolated, str(path), "exec"), ns)
    return ns[method.name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional generated evidence JSON")
    args = parser.parse_args()
    replay = RUN / "replay"
    summaries = jsonl(replay / "summary.jsonl")
    cases = jsonl(RUN.parent / "input/cases.jsonl")
    manifest = json.loads((replay / "manifest.json").read_text(encoding="utf-8"))
    wire = defaultdict(list)
    request_hashes = {}
    for row in jsonl(replay / "chunks.jsonl"):
        if row.get("record_type") == "request":
            request_hashes[row["client_request_id"]] = row["request_body_sha256"]
        else:
            assert not row.get("parse_error"), row
            if isinstance(row.get("chunk"), dict):
                assert not row["chunk"].get("error"), row
                wire[row["client_request_id"]].append(row["chunk"])
    for row in summaries:
        kwargs = {k: row[k] for k in ["line_no", "attempt", "variant", "client_request_id", "status_code", "elapsed_ms", "expected_tool_call", "case_id", "category", "expected_tool_names", "latency_eligible", "error", "first_chunk_ms", "first_output_ms", "first_reasoning_ms", "first_content_ms", "first_tool_call_ms"]}
        source_body = cases[row["line_no"] - 1]["request_body"]
        rebuilt = assemble_observation(**kwargs, chunks=wire[row["client_request_id"]], request_tools=source_body["tools"])
        assert asdict(rebuilt) == row, (row["client_request_id"], "wire differs from summary")
        body = build_body(source_body, variant=row["variant"], client_request_id=row["client_request_id"], args=SimpleNamespace(**{**manifest["request_overrides"], "stream": manifest["stream"], "model": manifest["model_override"]}))
        assert json_hash(body) == request_hashes[row["client_request_id"]], (row["client_request_id"], "input hash differs")
    logs = sorted(replay.glob("server_filtered_part_*"))
    raw = defaultdict(list)
    for path in logs:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            row = json.loads(line.split("DTB_STREAM_AUDIT", 1)[1].strip())
            row["location"] = f"replay/{path.name}:{line_no}"
            raw[row["parent_request_id"]].append(row)
    ids = {row["server_request_id"] for row in summaries}
    records, invalid = extract_server_record_parts(logs, ids)
    actual = [classify(row, records.get(row["server_request_id"], [])) for row in summaries]
    uploaded = json.loads((replay / "analysis.json").read_text(encoding="utf-8"))
    assert actual == uploaded["requests"], "Uploaded analysis differs from recomputation"
    identity = dict(line.split("=", 1) for line in (RUN / "runtime_identity.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
    hashes = {}
    for label, patch, target in [
        ("parser", "qwen3_coder_parser.patch", "vllm/tool_parsers/qwen3coder_tool_parser.py"),
        ("serving", "dtb_serving.patch", "vllm/entrypoints/openai/chat_completion/serving.py"),
    ]:
        value = digest(patched_bytes(ROOT / "patches/vllm/v0.23.0" / patch, target))
        hashes[label] = {"expected_lf_sha256": value, "runtime_sha256": identity[label + "_sha256"], "matches": value == identity[label + "_sha256"]}
    value = digest((ROOT / "dtb/request_controller.py").read_bytes().replace(b"\r\n", b"\n"))
    hashes["controller"] = {"expected_lf_sha256": value, "runtime_sha256": identity["controller_sha256"], "matches": value == identity["controller_sha256"]}
    hashes["config_lf_sha256"] = digest((RUN / "budget_config.stream_audit.yaml").read_bytes().replace(b"\r\n", b"\n"))
    assert all(hashes[label]["matches"] for label in ("parser", "serving", "controller")), "Source differs from deployed run; use the 915bec9 source snapshot"
    assert hashes["config_lf_sha256"] == (RUN / "budget_config.stream_audit.sha256").read_text(encoding="utf-8").split()[0]

    boundary, mismatches, literal, unknown, schemas, request_counts = [], [], [], [], [], []
    method = reasoning_method()
    fake = SimpleNamespace(start_token_id=248068, end_token_id=248069, start_token="<think>", end_token="</think>", _tool_call_token_id=248058, _tool_call_tag="<tool_call>")
    grouped = defaultdict(dict)
    public_locations = {}
    for number, row in enumerate(summaries, 1):
        rid = row["server_request_id"]
        public_locations[rid] = f"replay/summary.jsonl:{number}"
        grouped[row["line_no"], row["attempt"]][row["variant"]] = row
        allowed = {tool["function"]["name"] for tool in cases[row["line_no"] - 1]["request_body"]["tools"]}
        if any(tool["name"] not in allowed for tool in row["tool_calls"]):
            unknown.append({"id": rid, "location": public_locations[rid], "tools": [t["name"] for t in row["tool_calls"]]})
        if row["schema_errors"]:
            schemas.append({"id": rid, "location": public_locations[rid], "errors": row["schema_errors"], "tool_calls": row["tool_calls"], "raw_final": "".join(r["raw_text"] for r in raw.get(rid, []) if r["phase"] == "final_answer")})
        if row["variant"] != "dtb":
            continue
        rs = raw[rid]
        assert [r["sequence"] for r in rs] == list(range(1, len(rs) + 1)), rid
        ts = [r for r in rs if r["phase"] == "initial_think"]
        bs = [r for r in ts if r["source_finish_reason"]]
        assert len(bs) == 1
        b = bs[0]
        boundary.append({"id": rid, "location": b["location"], "text": b["raw_text"], "ids": b["token_ids"], "stop": b["source_stop_reason"]})
        request_counts.append(sum(len(r["token_ids"]) for r in ts))
        text = "".join(r["raw_text"] for r in ts)
        pre_text, pre_ids, reconstructed = "", [], ""
        for r in ts:
            message = method(fake, pre_text, pre_text + r["raw_text"], r["raw_text"], pre_ids, pre_ids + r["token_ids"], r["token_ids"])
            reconstructed += getattr(message, "reasoning", "") or ""
            pre_text += r["raw_text"]
            pre_ids += r["token_ids"]
        assert reconstructed == row["reasoning"], (rid, "native reasoning replay differs")
        if text != row["reasoning"]:
            assert text == row["reasoning"] + b["raw_text"], rid
            mismatches.append({"id": rid, "location": b["location"], "public_location": public_locations[rid], "lost_text": b["raw_text"], "lost_payload_tokens": len(b["token_ids"]) - 1, "public_tail": row["reasoning"][-100:]})
        if "</think>" in text:
            literal.append({"id": rid, "boundary_location": b["location"], "literal_count": text.count("</think>"), "text": text, "matching_deltas": [{k:r[k] for k in ["location", "raw_text", "token_ids", "source_stop_reason"]} for r in ts if any(t in r["raw_text"] for t in ["think", "</", ">"])], "final_tools": [t["name"] for t in row["tool_calls"]]})

    all_raw = [r for rows in raw.values() for r in rows]
    transitions = Counter((not p["vanilla"]["anomaly_reasons"], not p["dtb"]["anomaly_reasons"]) for p in grouped.values())
    result = {
        "identity_hashes": hashes,
        "integrity": {"requests": len(summaries), "unique_request_ids": len(ids), "pairs": len(grouped), "raw_request_ids": len(raw), "whitelist_exact": set((RUN / "stream_request_ids.txt").read_text(encoding="utf-8").splitlines()) == set(raw), "invalid_records": invalid, "recomputed_analysis_identical": True, "wire_summaries_identical": True, "request_body_hashes_match": True, "sequences_contiguous": True, "source_forwarded_text_or_ids_mismatch": sum(r["raw_text"] != r["forwarded_text"] or r["token_ids"] != r["forwarded_token_ids"] for r in all_raw)},
        "tool_distributions": [{"line": line, "variant": variant, "names": dict(Counter(",".join(t["name"] for t in r["tool_calls"]) for r in summaries if r["line_no"] == line and r["variant"] == variant))} for line in [5, 10] for variant in ["vanilla", "dtb"]],
        "exact_oracle_transitions": [{"vanilla_clean": v, "dtb_clean": d, "pairs": count} for (v, d), count in transitions.items()],
        "schema_failures": schemas,
        "undeclared_tools": unknown,
        "thinking_token_counts_including_boundary": {"min": min(request_counts), "median": statistics.median(request_counts), "max": max(request_counts), "total": sum(request_counts)},
        "phase_event_counts": [{"phase": phase, "event": event, "count": count} for (phase, event), count in Counter((r["phase"], r["event"]) for r in all_raw).items()],
        "boundary_records": boundary,
        "literal_think_end_requests": literal,
        "native_reasoning_method_matches_public": 60,
        "reasoning_payload_loss": {"requests": len(mismatches), "chars": sum(len(x["lost_text"]) for x in mismatches), "payload_tokens": sum(x["lost_payload_tokens"] for x in mismatches), "nonwhitespace_requests": sum(bool(x["lost_text"].strip()) for x in mismatches), "evidence": mismatches},
        "uploaded_summary": uploaded["summary"],
    }
    # Execute the deployed tool parser source (verified above by SHA256), using
    # the repository's existing dependency-only stubs. No vLLM checkout edits.
    from tools.dtb import parser_partition_equivalence as harness
    harness._install_import_boundary()
    base = harness._load_source("vllm.tool_parsers.abstract_tool_parser", harness.VLLM_ROOT / "tool_parsers/abstract_tool_parser.py")
    sys.modules["vllm.tool_parsers"].ToolParser = base.ToolParser
    module = types.ModuleType("vllm.tool_parsers.qwen3coder_tool_parser")
    sys.modules[module.__name__] = module
    source = patched_bytes(ROOT / "patches/vllm/v0.23.0/qwen3_coder_parser.patch", "vllm/tool_parsers/qwen3coder_tool_parser.py")
    exec(compile(source, "<deployed-qwen-parser>", "exec"), module.__dict__)

    class Tokenizer(harness.FakeTokenizer):
        def get_vocab(self):
            return {**super().get_vocab(), "</tool_call>": 248059, "<think>": 248068, "</think>": 248069}

    for row in summaries:
        if row["variant"] != "dtb":
            continue
        tools = [harness.ChatCompletionToolsParam(type="function", function=harness.FunctionDefinition(**t["function"])) for t in cases[row["line_no"] - 1]["request_body"]["tools"]]
        final = [r for r in raw[row["server_request_id"]] if r["phase"] == "final_answer"]
        deltas = [harness.InputDelta(r["raw_text"], tuple(r["token_ids"]), bool(r["raw_finished"])) for r in final]
        assembled = harness._run_direct(module.Qwen3CoderToolParser, Tokenizer(), tools, harness.ChatCompletionRequest(tools=tools), deltas)["assembled"]
        assert assembled["tool_calls"] == [{"name": t["name"], "arguments": t["arguments"]} for t in row["tool_calls"]], (row["server_request_id"], "tool parser replay differs")
        assert assembled["content"] == row["content"], (row["server_request_id"], "content replay differs")
        oracle = harness._non_streaming_oracle(module.Qwen3CoderToolParser, Tokenizer(), tools, harness.ChatCompletionRequest(tools=tools), "".join(r["raw_text"] for r in final))
        assert [(t["name"], a) for t, a in zip(oracle["tool_calls"], oracle["parsed_arguments"])] == [(t["name"], json.loads(t["arguments"])) for t in row["tool_calls"]], (row["server_request_id"], "full-text tool oracle differs")
    result["deployed_tool_parser_replay_matches_public"] = 60
    result["full_text_tool_oracle_matches_public"] = 60
    marker_final = [r for r in all_raw if r["phase"] == "final_answer" and 248069 in r["token_ids"]]
    result["final_think_end_payload"] = {"deltas": len(marker_final), "requests": len({r["parent_request_id"] for r in marker_final}), "terminal_deltas": sum(bool(r["source_finish_reason"]) for r in marker_final)}
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "identity_hashes": hashes, "integrity": result["integrity"], "reasoning_loss": {k:v for k,v in result["reasoning_payload_loss"].items() if k != "evidence"}}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
