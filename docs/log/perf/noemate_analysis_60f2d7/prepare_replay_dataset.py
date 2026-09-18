"""Build audited Noemate replay inputs for protocol and latency experiments.

No network, inference, or historical tool execution. Run from any directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
SOURCE = ROOT / "proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3"
sys.path.insert(0, str(ROOT / "tools/dtb"))
import replay_tool_calls as replay


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_lines(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_audited_detail(path, expected):
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    # Historical audit hashed Windows checkout bytes; Git stores LF blobs.
    candidates = (raw, lf, lf.replace(b"\n", b"\r\n"))
    require(any(hashlib.sha256(value).hexdigest() == expected for value in candidates),
            f"detail hash changed (beyond line endings): {path}")
    return json.loads(raw), hashlib.sha256(lf).hexdigest()


def build(destination):
    features = read_lines(OUT / "request_features.jsonl")
    hashes = {r["request_id"]: r["sha256"] for r in json.loads((OUT / "detail_hashes.json").read_text(encoding="utf-8"))}
    pilot_index = json.loads((OUT / "replay_index.json").read_text(encoding="utf-8"))
    pilot_ids = [r["request_id"] for r in pilot_index if r["latency_eligible_after_quality_gate"]]
    diagnostic_ids = [r["request_id"] for r in pilot_index if not r["latency_eligible_after_quality_gate"]]
    annotations = {r["request_id"]: r for r in pilot_index}
    tail = sorted((r for r in features if not r["exclusions"] and r["reasoning_tokens_local"] > 750),
                  key=lambda r: r["arrival_seq"])
    require(len(tail) == 415, "source population changed; review selection before building")
    groups = {}
    for row in tail:
        groups.setdefault(row["request_sha256"], []).append(row)
    selected = []
    duplicates = []
    for members in groups.values():
        # Keep already inspected examples as representatives when available.
        representative = next((r for r in members if r["request_id"] in annotations), members[0])
        selected.append(representative)
        duplicates.extend({"request_id": r["request_id"], "representative": representative["request_id"],
                           "request_sha256": r["request_sha256"]}
                          for r in members if r is not representative)
    selected.sort(key=lambda r: r["arrival_seq"])
    needed = {r["request_id"]: r for r in selected}
    needed.update({r["request_id"]: r for r in features if r["request_id"] in diagnostic_ids})
    cases, index, blocked = {}, [], []
    for rid, row in needed.items():
        path = SOURCE / row["detail_json"]
        detail, detail_lf_sha256 = read_audited_detail(path, hashes[rid])
        body = detail["request"]
        require(replay.json_hash(body) == row["request_sha256"], f"request hash changed: {rid}")
        require(body.get("stream") is True, f"unexpected nonstream case: {rid}")
        errors = replay.validate_conversation(body["messages"])
        if errors:
            blocked.append({"request_id": rid, "errors": errors})
            continue
        message = detail["response"]["choices"][0]["message"]
        note = annotations.get(rid, {})
        cases[rid] = {"case_id": rid, "request_body": body, "response_data": message,
                      "replay_expectation": {
                          "category": note.get("category", "longtail_unreviewed"),
                          "expected_tool_call": bool(message.get("tool_calls")),
                          "expected_tool_names": [t["function"]["name"] for t in message.get("tool_calls") or []],
                          "latency_eligible": rid not in diagnostic_ids}}
        index.append({"case_id": rid, "request_sha256": row["request_sha256"],
                      "source_detail": path.relative_to(ROOT).as_posix(),
                      "source_detail_lf_sha256": detail_lf_sha256, "source_detail_audit_sha256": hashes[rid],
                      "reasoning_tokens_local": row["reasoning_tokens_local"],
                      "max_tokens": row["max_tokens_request"], "tool_names": row["tool_names"],
                      "question_preview": row["last_non_skill_user_preview"],
                      "question_hint_sha256": row["last_non_skill_user_sha256"],
                      "role": "diagnostic" if rid in diagnostic_ids else "pilot" if rid in pilot_ids else "pool",
                      "quality_status": "deferred_by_user", "quality_hint": note.get("quality_check")})
    require(not blocked, f"invalid conversation inputs: {blocked}")
    pool = [cases[r["request_id"]] for r in selected if r["request_id"] not in diagnostic_ids]
    destination.mkdir(parents=True, exist_ok=True)
    files = {"pilot.jsonl": [cases[rid] for rid in pilot_ids],
             "diagnostic.jsonl": [cases[rid] for rid in diagnostic_ids], "pool.jsonl": pool}
    for name, records in files.items():
        write_lines(destination / name, records)
        require(len(replay.load_cases(destination / name, 1, 0)) == len(records), f"load mismatch: {name}")
    write_lines(destination / "index.jsonl", index)
    manifest = {"source_commit": "60f2d7c01faa3268874893dc42f850ad86086c7c",
                "source_hash_policy": "historical SHA256, allowing only LF/CRLF conversion; portable detail hash uses LF",
                "selection": "exclusions=[] and reasoning_tokens_local>750; exact request-body deduplication",
                "raw_tail_count": len(tail), "unique_tail_count": len(selected), "duplicates": duplicates,
                "known_diagnostic_tail_ids": [r["request_id"] for r in selected if r["request_id"] in diagnostic_ids],
                "counts": {name: len(rows) for name, rows in files.items()},
                "pool_max_tokens": dict(Counter(r["request_body"].get("max_tokens") for r in pool)),
                "reference_status": "deferred_by_user_no_accuracy_claim", "inference_executed": False,
                "files": {name: {"sha256": sha(destination / name), "bytes": (destination / name).stat().st_size}
                          for name in [*files, "index.jsonl"]}}
    write_json(destination / "manifest.json", manifest)
    print(json.dumps({"output": str(destination), "counts": manifest["counts"],
                      "raw_tail": len(tail), "unique_tail": len(selected)}, ensure_ascii=False))



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT / "replay_dataset")
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
