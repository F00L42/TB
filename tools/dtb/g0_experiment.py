#!/usr/bin/env python3
"""Executable G0 experiment: prepare archived requests, run CPU decoder gates,
or launch a dedicated API server with an allowlisted decoder-profile recorder.
No server/model is contacted by prepare, validate or run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.dtb.g0_support import (digest, fresh_directory, load_fixtures, load_profiles,
                                  prepare_inputs, read_json, write_json)


def git_revision(path):
    try:
        p = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def materialize(runtime, fixture, profile):
    # Captured prompt wins only if the fixture has not explicitly selected one.
    if "prompt_token_ids" in fixture:
        prompt = fixture["prompt_token_ids"]
        prompt_source = "fixture_ids"
    elif "prompt_text" in fixture:
        prompt = runtime.encode(fixture["prompt_text"])
        prompt_source = "synthetic_prompt_text"
    elif "prompt_token_ids" in profile:
        prompt = profile["prompt_token_ids"]
        prompt_source = "captured_engine_prompt"
    else:
        prompt = runtime.encode("<|im_start|>assistant\n<think>\n")
        prompt_source = "synthetic_legacy_prompt"
    sequence = fixture.get("sample_token_ids")
    if sequence is None:
        sequence = runtime.encode(fixture["sample_text"])
    sequence = list(sequence)
    pad = fixture.get("pad_tokens", 0)
    if pad:
        unit = runtime.encode("Plan. ")
        if not unit:
            raise ValueError("padding tokenization is empty")
        sequence = (unit*((pad+len(unit)-1)//len(unit)))[:pad]+sequence
    cuts = list(range(1, len(sequence)+1)) if fixture["cuts"] == "all" else fixture["cuts"]
    if not sequence or any(c > len(sequence) for c in cuts):
        raise ValueError(f"fixture {fixture['id']} has invalid token cut for deployed tokenizer")
    return list(prompt), sequence, cuts, prompt_source


def trial(runtime, prompt, sequence, cut, pending, suffix_kind, params):
    end = min(cut+pending, len(sequence))
    prefix = sequence[:end]
    suffix = sequence[end:] if suffix_kind == "original" else runtime.encode("</think>\nanswer")
    prefix_out = runtime.abort_prefix(prompt, prefix, cut, params)
    if prefix_out["natural_before_abort"]:
        return {"status": "not_applicable", "reason": "natural_stop_before_abort", **prefix_out}
    remainder = copy.deepcopy(params)
    if params.max_tokens is not None:
        remainder.max_tokens = params.max_tokens-len(prefix)
        if remainder.max_tokens <= 0:
            return {"status": "not_applicable", "reason": "completion_limit_before_restart"}
    reference = runtime.decode(prompt, prefix+suffix, params)
    resumed = runtime.decode(prompt+prefix, suffix, remainder)
    actual = prefix_out["text"]+resumed["text"]
    direct = runtime.direct_prefix(prompt, prefix, params)+resumed["text"]
    token_equal = prefix+resumed["token_ids"] == reference["token_ids"]
    ok = actual == reference["text"] and direct == reference["text"] and token_equal
    value = {"status": "pass" if ok else "fail", "restart": end,
             "prefix_sha256": digest(prefix), "text_equal": actual == reference["text"],
             "direct_decoder_equal": direct == reference["text"], "token_ids_equal": token_equal,
             "reference_finish": reference["finish_reason"], "resumed_finish": resumed["finish_reason"]}
    if not ok:
        first = next((i for i,(a,b) in enumerate(zip(actual,reference["text"])) if a!=b),
                     min(len(actual),len(reference["text"])))
        value.update(actual=actual, expected=reference["text"], direct=direct,
                     first_text_difference=first, prefix_ids=prefix, suffix_ids=suffix,
                     prefix_trace=prefix_out["trace"], resumed=resumed, reference=reference)
    return value


def run_matrix(args, runtime=None):
    profiles = load_profiles(args.profiles)
    fixtures = load_fixtures(args.fixtures)
    if args.profile_ids:
        chosen = set(args.profile_ids.split(","))
        if not chosen <= {p["id"] for p in profiles}:
            raise ValueError("unknown selected profile")
        profiles = [p for p in profiles if p["id"] in chosen]
    if args.require_live_profile and any(p["source"] != "live_captured" for p in profiles):
        raise ValueError("production-profile gate requires actual captured profiles")
    if args.stop_control and any(p["source"] not in ("historical","single_field_control") for p in profiles):
        raise ValueError("string-stop control may not override a production profile")
    out = fresh_directory(args.output_dir)
    counts, groups = Counter(), {}
    (out / "failures").mkdir()
    (out / "inputs").mkdir()
    manifest = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "repo_commit": git_revision(REPO), "tokenizer": str(args.tokenizer),
        "profiles": profiles, "fixtures_sha256": digest(fixtures),
        "script_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (Path(__file__), Path(__file__).with_name("g0_runtime.py"),
                                    Path(__file__).with_name("g0_support.py"))},
        "status": "running", "stream_interval": 1,
        "scope": "CPU detokenizer/collector fixed suffix; not scheduler, DTB state-machine or parser acceptance",
        "production_ready": False, "profile_identity_match": {}}
    write_json(out / "manifest.json", manifest)
    try:
        if runtime is None:
            from tools.dtb.g0_runtime import Runtime
            runtime = Runtime(args.tokenizer)
        with (out / "results.jsonl").open("w", encoding="utf-8") as stream:
            for profile in profiles:
                params = runtime.params(profile)
                decoder = runtime.decoder(runtime.encode("test"), params)
                from tools.dtb.g0_runtime import identity
                current = identity(runtime.tokenizer, decoder) if not hasattr(runtime,"test_identity") else runtime.test_identity
                manifest.setdefault("runtime_identities", {})[profile["id"]] = current
                if profile["source"] == "live_captured":
                    match = current == profile["identity"]
                    manifest["profile_identity_match"][profile["id"]] = match
                    if not match:
                        raise ValueError("captured decoder/runtime identity differs; rerun in the service environment")
                for fixture in fixtures:
                    prompt, sequence, cuts, prompt_source = materialize(runtime, fixture, profile)
                    input_key = digest({"prompt":prompt, "sequence":sequence})
                    write_json(out / "inputs" / f"{input_key}.json", {
                        "prompt_token_ids":prompt,"sample_token_ids":sequence,"prompt_source":prompt_source,
                        "fixture_source":fixture["source"],"suffix_provenance":"fixed_test_sequence_not_model_sampling"})
                    for string_stop in ([False,True] if args.stop_control else [False]):
                        active = copy.deepcopy(params)
                        if string_stop:
                            active.stop = ["NEVER_MATCH_THIS_STOP"]
                        for cut in cuts:
                            for pending in fixture["pending"]:
                                for suffix in fixture["suffixes"]:
                                    row = {"profile":profile["id"],"fixture":fixture["id"],"trigger":cut,
                                           "pending":pending,"suffix":suffix,"string_stop_control":string_stop,
                                           "input_ref":f"inputs/{input_key}.json"}
                                    value = trial(runtime,prompt,sequence,cut,pending,suffix,active)
                                    status = value["status"]
                                    counts[status] += 1
                                    group_key = f"{profile['id']}/{fixture['id']}/{string_stop}/{pending}/{suffix}"
                                    group = groups.setdefault(group_key, Counter())
                                    group[status] += 1
                                    if status == "fail":
                                        file = f"failures/{counts['fail']:06d}.json"
                                        write_json(out / file,{**row, **value})
                                        value = {k:v for k,v in value.items() if k not in (
                                            "actual","expected","direct","prefix_trace","reference","resumed","prefix_ids","suffix_ids")}
                                        value["failure_ref"] = file
                                    stream.write(json.dumps({**row,**value},ensure_ascii=False)+"\n")
                            stream.flush()
                    print(f"Completed profile={profile['id']} fixture={fixture['id']}", flush=True)
        code = 1 if counts["fail"] else (3 if not counts["pass"] else 0)
        manifest["status"] = "completed"
        summary = {"schema":1,"counts":dict(counts),"groups":{k:dict(v) for k,v in groups.items()},
                   "exit_code":code,"production_ready":False,
                   "unverified":["DTB cross-phase prompt/cue transitions", "native parser/serving equivalence",
                                 "live scheduler abort race", "Agent quality and latency"]}
        write_json(out / "summary.json",summary)
        return code
    except BaseException as exc:
        manifest["status"] = "error"
        write_json(out / "error.json",{"type":type(exc).__name__,"message":str(exc),"traceback":traceback.format_exc()})
        raise
    finally:
        write_json(out / "manifest.json", manifest)


def capture_server(args):
    # Dedicated diagnostic server; normal DTB/vLLM files stay untouched.
    import runpy
    import msgspec
    from vllm.v1.engine.detokenizer import IncrementalDetokenizer
    from tools.dtb.g0_runtime import identity
    out = fresh_directory(args.output_dir)
    from tools.dtb.g0_support import safe_name
    safe_name(args.request_prefix)
    original = IncrementalDetokenizer.from_new_request.__func__
    count = 0

    def captured(cls, tokenizer, request):
        nonlocal count
        decoder = original(cls, tokenizer, request)
        external = getattr(request,"external_req_id",None) or request.request_id
        if not external.startswith(args.request_prefix):
            return decoder
        count += 1
        try:
            p = request.sampling_params
            fields = {name: msgspec.to_builtins(getattr(p,name)) for name in p.__struct_fields__ if not name.startswith("_")}
            fields["output_kind"] = p.output_kind.name
            profile = {"id":f"captured-{count:04d}","source":"live_captured","request_id":external,
                "sampling_params":fields,"prompt_token_ids":list(request.prompt_token_ids or []),
                "runtime_state":{"_eos_token_id":p._eos_token_id,"_all_stop_token_ids":sorted(p._all_stop_token_ids)},
                "identity":identity(tokenizer,decoder)}
            write_json(out / f"profile-{count:04d}.json",{"schema":1,"profiles":[profile]})
        except Exception as exc:
            # Capture failure must not turn a previously working model request into a failure.
            try:
                write_json(out / f"capture-error-{count:04d}.json",{"request_id":external,"error":str(exc)})
            except Exception:
                print(f"G0 profile capture failed for {external}: {exc}",file=sys.stderr)
        return decoder

    IncrementalDetokenizer.from_new_request = classmethod(captured)
    write_json(out / "capture_manifest.json",{"schema":1,"request_prefix":args.request_prefix,
        "repo_commit":git_revision(REPO),"mode":"dedicated_api_server_decoder_constructor",
        "warning":"Contains prompt tokens. Only matching requests are captured; capture adds diagnostic overhead."})
    server_args = args.server_args
    if server_args[:1] == ["--"]:
        server_args = server_args[1:]
    if not server_args:
        raise ValueError("supply API server arguments after --, including --model")
    sys.argv = ["vllm.entrypoints.openai.api_server", *server_args]
    runpy.run_module("vllm.entrypoints.openai.api_server", run_name="__main__")


def run_batch(args):
    """Continue after expected H0 failures and save every child's real exit code."""
    out = fresh_directory(args.output_dir)
    inputs = REPO / "docs/log/perf/agent_minimum_intervention/input/g0"
    tasks = [
        ("H0", "legacy_profiles.json", "legacy_fixtures.jsonl", ["--profile-ids","H0-skip-false","--stop-control"]),
        ("H1", "legacy_profiles.json", "legacy_fixtures.jsonl", ["--profile-ids","H1-skip-true","--stop-control"]),
        ("log-derived", "vanilla_log_profile.json", "production_fixtures.jsonl", []),
    ]
    records = []
    for name, profiles, fixtures, extra in tasks:
        command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "run",
            "--tokenizer",args.tokenizer,"--profiles",str(inputs/profiles),
            "--fixtures",str(inputs/fixtures),"--output-dir",str(out/name),*extra]
        with (out/f"{name}.stdout.log").open("w",encoding="utf-8") as stdout, (out/f"{name}.stderr.log").open("w",encoding="utf-8") as stderr:
            proc = subprocess.run(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
        records.append({"name":name,"command":command,"exit_code":proc.returncode})
        write_json(out/"batch_summary.json",{"schema":1,"runs":records,"production_ready":False})
        print(f"{name}: exit={proc.returncode}",flush=True)
    codes = [r["exit_code"] for r in records]
    return 2 if any(c not in (0,1,3) for c in codes) else (1 if 1 in codes else (3 if 3 in codes else 0))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    prep = sub.add_parser("prepare",help="extract selected archived requests, no tokenizer/model needed")
    prep.add_argument("--selection",type=Path,required=True)
    prep.add_argument("--output-dir",type=Path,required=True)
    check = sub.add_parser("validate",help="validate input schemas without vLLM")
    check.add_argument("--profiles",type=Path,required=True)
    check.add_argument("--fixtures",type=Path,required=True)
    run = sub.add_parser("run",help="run real vLLM CPU decoder gate, no model weights")
    run.add_argument("--tokenizer",required=True)
    run.add_argument("--profiles",type=Path,required=True)
    run.add_argument("--fixtures",type=Path,required=True)
    run.add_argument("--profile-ids")
    run.add_argument("--stop-control",action="store_true")
    run.add_argument("--require-live-profile",action="store_true")
    run.add_argument("--output-dir",type=Path,required=True)
    batch = sub.add_parser("batch",help="run H0, H1 and log-derived groups, preserving all exit codes")
    batch.add_argument("--tokenizer",required=True)
    batch.add_argument("--output-dir",type=Path,required=True)
    capture = sub.add_parser("capture-server",help="launch a dedicated server with an allowlisted profile hook")
    capture.add_argument("--output-dir",type=Path,required=True)
    capture.add_argument("--request-prefix",required=True)
    capture.add_argument("server_args",nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "prepare":
        print(prepare_inputs(REPO,args.selection,args.output_dir))
        return 0
    if args.command == "validate":
        print(f"Validated {len(load_profiles(args.profiles))} profiles and {len(load_fixtures(args.fixtures))} fixtures")
        return 0
    if args.command == "capture-server":
        capture_server(args)
        return 0
    if args.command == "batch":
        return run_batch(args)
    return run_matrix(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"G0 setup/execution error: {exc}",file=sys.stderr)
        raise SystemExit(2)
