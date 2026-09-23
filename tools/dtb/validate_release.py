#!/usr/bin/env python3
"""Replay a small, answer-checked matrix against a user-supplied vLLM server.

Closed-loop batches use identical requests/seeds and alternate variant order.
This checks the selected samples, not general model quality or production gains.
No tools returned by the model are executed. All output goes to a new directory.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.dtb import replay_tool_calls as replay

PROFILES = {
    "auto": {},
    "cap750": dict(zip(replay.FIXED_FIELDS, (750, 750, 256, 4, 1, 0.95))),
    "probe128": dict(zip(replay.FIXED_FIELDS, (2048, 128, 256, 4, 1, 0.95))),
}
IDENTITY_FIELDS = (
    "tb_commit", "vllm_commit", "vllm_ascend_commit", "torch_version",
    "torch_npu_version", "cann_version", "model_revision", "config_sha256",
    "patches_sha256", "observability", "prefix_cache", "launch_command",
)


def read_dataset(path):
    cases = replay.load_cases(path, 1, 0)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or len({c.case_id for c in cases}) != len(cases):
        raise ValueError("dataset needs nonempty unique case IDs")
    expected = {}
    for case, row in zip(cases, rows):
        check = row.get("answer_check", {})
        if check.get("kind") not in {"text", "json", "tool_calls"} or "value" not in check:
            raise ValueError(f"{case.case_id}: missing supported answer_check")
        expected[case.case_id] = check
    return cases, expected


def request_body(case, profile, variant, request_id, args, stream):
    # Remove every captured override before constructing a cell. In particular,
    # auto must contain no fixed fields; replay_tool_calls' CLI defaults differ.
    source = {key: value for key, value in case.request_body.items() if key not in replay.FIXED_FIELDS}
    options = SimpleNamespace(model=args.model, stream=stream, max_tokens=args.max_tokens,
                              temperature=0.0, top_p=1.0, seed=args.seed,
                              **{key: PROFILES[profile].get(key) for key in replay.FIXED_FIELDS})
    return replay.build_body(source, variant=variant, client_request_id=request_id, args=options)


def answer_errors(row, check):
    errors = list(row.get("anomaly_reasons", []))
    if row.get("status_code") != 200 or row.get("error"):
        errors.append("http_or_transport_error")
    expected_finish = "tool_calls" if check["kind"] == "tool_calls" else "stop"
    if row.get("finish_reasons") != [expected_finish]:
        errors.append("unexpected_finish_reason")
    if row.get("terminal_count") != 1:
        errors.append("terminal_count")
    try:
        if check["kind"] == "text":
            actual = row["content"].strip()
        elif check["kind"] == "json":
            actual = json.loads(row["content"])
        else:
            actual = [{"name": call["name"], "arguments": json.loads(call["arguments"])}
                      for call in row["tool_calls"]]
        # JSON canonical equality also distinguishes true from 1 and 1 from "1".
        if json.dumps(actual, sort_keys=True, ensure_ascii=False) != json.dumps(check["value"], sort_keys=True, ensure_ascii=False):
            errors.append("answer_mismatch")
        if check["kind"] != "tool_calls" and row.get("tool_calls"):
            errors.append("unexpected_tool_call")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid_answer")
    elapsed = row.get("elapsed_ms", 0)
    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed <= 0:
        errors.append("invalid_elapsed_ms")
    return sorted(set(errors))


def summarize(rows, case_ids, repetitions, minimum_gain):
    """Fail closed on missing/duplicate pairs; never rank only surviving rows."""
    expected = {(case, attempt, variant) for case in case_ids
                for attempt in range(1, repetitions + 1) for variant in ("vanilla", "dtb")}
    indexed = {(r["case_id"], r["attempt"], r["variant"]): r for r in rows}
    complete = len(indexed) == len(rows) and set(indexed) == expected
    quality_ok = complete and all(not r["validation_errors"] for r in rows)
    result = {"complete": complete, "quality_ok": quality_ok, "requests": len(rows),
              "expected_requests": len(expected), "gain_gate_passed": False}
    if not quality_ok:
        return result
    pairs = [(indexed[c, a, "vanilla"]["elapsed_ms"], indexed[c, a, "dtb"]["elapsed_ms"])
             for c in case_ids for a in range(1, repetitions + 1)]
    deltas = [d-v for v, d in pairs]
    speedups = [100 * (v-d)/v for v, d in pairs]
    total_gain = 100 * (sum(v for v, _ in pairs)-sum(d for _, d in pairs))/sum(v for v, _ in pairs)
    result.update(pairs=len(pairs), paired_mean_delta_ms=statistics.mean(deltas),
                  paired_median_delta_ms=statistics.median(deltas),
                  paired_median_gain_percent=statistics.median(speedups),
                  total_e2e_gain_percent=total_gain, faster_pairs=sum(d < v for v, d in pairs),
                  gain_gate_passed=(repetitions >= 5 and statistics.median(speedups) >= minimum_gain
                                    and total_gain >= minimum_gain))
    return result


def load_identity(path, require_gain):
    if path is None:
        raise ValueError("--server-manifest is required for a live run; copy validation/replay/server-manifest.example.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in IDENTITY_FIELDS:
        if not isinstance(data.get(key), str) or not data[key].strip() or "REPLACE" in data[key]:
            raise ValueError(f"server manifest: fill {key}")
    if data["observability"] not in {"off", "phase", "stream_audit"}:
        raise ValueError("server manifest: invalid observability")
    if require_gain and data["observability"] != "off":
        raise ValueError("gain gate requires a separate observability=off run")
    return data


async def run_matrix(args):
    cases, checks = read_dataset(args.file)
    cells = [(p, stream) for p in args.profiles for stream in
             ([True, False] if args.protocol == "both" else [args.protocol == "stream"])]
    if args.dry_run:
        plan = [{"profile": p, "stream": s, "case_id": c.case_id,
                 "request_body": request_body(c, p, "dtb", "dry-run", args, s)}
                for p, s in cells for c in cases]
        print(json.dumps({"status": "offline_plan_only", "requests_per_variant": len(cases)*args.repetitions,
                          "cells": len(cells), "plan": plan}, ensure_ascii=False, indent=2))
        return 0
    identity = load_identity(args.server_manifest, args.require_gain)
    if args.require_gain and args.repetitions < 5:
        raise ValueError("gain gate requires --repetitions >= 5")
    args.output.mkdir(parents=True, exist_ok=False)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    manifest = {"schema": 1, "run_id": run_id, "evidence": "client_replay",
                "server_identity": identity, "server_identity_source": "operator_declaration",
                "input_sha256": hashlib.sha256(args.file.read_bytes()).hexdigest(),
                "dataset": str(args.file.resolve()), "url": args.url, "model": args.model,
                "profiles": {p: PROFILES[p] for p in args.profiles}, "protocol": args.protocol,
                "concurrency": args.concurrency, "arrival": "closed_loop_per_variant_batch",
                "order": "alternating_by_repetition", "repetitions": args.repetitions,
                "warmup_batches_per_cell_variant": args.warmup, "seed": args.seed,
                "max_tokens": args.max_tokens, "minimum_gain_percent": args.minimum_gain,
                "cases": [c.case_id for c in cases]}
    write_json(args.output / "manifest.json", manifest)
    headers = {"Content-Type": "application/json"}
    if os.getenv("OPENAI_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]
    results = []
    limits = replay.httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with replay.httpx.AsyncClient(timeout=args.timeout, limits=limits, trust_env=False) as client:
        for profile, stream in cells:
            name = f"{profile}-{'stream' if stream else 'json'}"
            directory = args.output / name
            directory.mkdir()
            observations = []
            semaphore = asyncio.Semaphore(args.concurrency)
            with (directory / "chunks.jsonl").open("w", encoding="utf-8") as chunks, \
                 (directory / "summary.jsonl").open("w", encoding="utf-8") as summary:
                async def one(case, attempt, variant):
                    async with semaphore:
                        request_id = f"release-{run_id}-{name}-{case.line_no}-{attempt}-{variant}"
                        body = request_body(case, profile, variant, request_id, args, stream)
                        observation = await replay.run_one(
                            client, url=args.url, headers=headers, body=body, line_no=case.line_no,
                            case_id=case.case_id, category=case.category, attempt=attempt, variant=variant,
                            expected_tool_call=case.expected_tool_call, expected_tool_names=case.expected_tool_names,
                            latency_eligible=True, chunks_file=chunks)
                        row = asdict(observation)
                        row.update(profile=profile, stream=stream, warmup=attempt <= 0,
                                   request_body_sha256=replay.json_hash(body))
                        row["validation_errors"] = answer_errors(row, checks[case.case_id])
                        summary.write(json.dumps(row, ensure_ascii=False) + "\n")
                        summary.flush()
                        if attempt > 0:
                            observations.append(row)
                        return not row["validation_errors"]

                for attempt in range(1-args.warmup, args.repetitions+1):
                    variants = ("vanilla", "dtb") if attempt % 2 else ("dtb", "vanilla")
                    for variant in variants:
                        ok = await asyncio.gather(*(one(c, attempt, variant) for c in cases))
                        print(f"{name} attempt={attempt} variant={variant}: {sum(ok)}/{len(ok)} clean", flush=True)
                        if not all(ok):
                            # Preserve partial results, return nonzero, and stop spending server time.
                            result = summarize(observations, [c.case_id for c in cases], args.repetitions, args.minimum_gain)
                            result["quality_ok"] = False  # Warmup failures also block the run.
                            results.append({"cell": name, **result})
                            return finish(args, results, identity)
            results.append({"cell": name, **summarize(observations, [c.case_id for c in cases],
                                                       args.repetitions, args.minimum_gain)})
    return finish(args, results, identity)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finish(args, results, identity):
    quality = all(r["quality_ok"] for r in results)
    gain = identity["observability"] == "off" and any(r["gain_gate_passed"] for r in results)
    code = 1 if not quality else (3 if args.require_gain and not gain else 0)
    report = {"functional_pass": quality, "sample_gain_observed": gain,
              "production_performance_accepted": False, "exit_code": code, "cells": results}
    write_json(args.output / "validation.json", report)
    lines = ["# Server replay result", "", f"- Functional gate: {quality}",
             f"- Sample gain gate (off only): {gain}", f"- Exit code: {code}", "",
             "This is a synthetic sample gate. It does not establish production quality, throughput or causal speedup.",
             "Route/probe coverage requires a separate phase log audit; response usage excludes hidden probes.", "",
             "| Cell | Complete | Quality | Paired median delta ms | Total E2E gain % |",
             "| --- | --- | --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r['cell']} | {r['complete']} | {r['quality_ok']} | {r.get('paired_median_delta_ms', 'n/a')} | {r.get('total_e2e_gain_percent', 'n/a')} |")
    (args.output / "RESULT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(f"Result: {args.output / 'RESULT.md'}; exit={code}")
    return code


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:9001/v1/chat/completions")
    p.add_argument("--model", required=True)
    p.add_argument("--file", type=Path, default=ROOT / "validation/replay/cases.jsonl")
    p.add_argument("--server-manifest", type=Path)
    p.add_argument("--output", type=Path, default=ROOT / "artifacts/replay" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    p.add_argument("--profiles", nargs="+", choices=tuple(PROFILES), default=list(PROFILES))
    p.add_argument("--protocol", choices=("stream", "json", "both"), default="both")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--repetitions", type=int, default=1)
    p.add_argument("--warmup", type=int, default=0)
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--seed", type=int, default=20260923)
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--minimum-gain", type=float, default=5.0)
    p.add_argument("--require-gain", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if min(args.concurrency, args.repetitions, args.max_tokens) < 1 or args.warmup < 0 or args.timeout <= 0:
        p.error("concurrency, repetitions, max-tokens, timeout must be positive; warmup must be >= 0")
    if not 0 < args.minimum_gain < 100 or len(set(args.profiles)) != len(args.profiles):
        p.error("minimum-gain must be between 0 and 100; profiles must be unique")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run_matrix(parse_args())))
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
