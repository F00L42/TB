"""Strict, model-free input and provenance helpers for the G0 experiment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def fresh_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def exact_keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value)-set(allowed) or set(required)-set(value):
        raise ValueError(f"Invalid keys; allowed={sorted(allowed)}, required={sorted(required)}")


def ids(value, *, empty=True):
    if not isinstance(value, list) or (not empty and not value) or any(
        type(x) is not int or x < 0 for x in value
    ):
        raise ValueError("token IDs must be a list of nonnegative integers")
    return value


def safe_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in (".", ".."):
        raise ValueError("ID must contain only letters, digits, dot, dash, underscore")
    return value


def load_profiles(path):
    data = read_json(path)
    exact_keys(data, {"schema", "profiles"}, {"schema", "profiles"})
    if data["schema"] != 1 or not isinstance(data["profiles"], list) or not data["profiles"]:
        raise ValueError("Expected nonempty schema 1 profiles")
    seen = set()
    for p in data["profiles"]:
        exact_keys(p, {"id", "source", "evidence", "sampling_params", "runtime_state",
                       "prompt_token_ids", "identity", "request_id"},
                   {"id", "source", "sampling_params"})
        safe_name(p["id"])
        if p["id"] in seen:
            raise ValueError("duplicate profile ID")
        seen.add(p["id"])
        if p["source"] not in ("historical", "single_field_control", "log_derived", "live_captured"):
            raise ValueError("unknown profile source")
        sp = p["sampling_params"]
        if not isinstance(sp, dict) or type(sp.get("skip_special_tokens")) is not bool:
            raise ValueError("explicit boolean skip_special_tokens is required")
        if sp.get("output_kind") != "DELTA" or sp.get("n", 1) != 1:
            raise ValueError("G0 currently requires n=1, DELTA")
        if sp.get("detokenize", True) is not True or sp.get("min_tokens", 0) != 0:
            raise ValueError("G0 requires detokenize=True, min_tokens=0")
        for field, default in (("presence_penalty", 0), ("frequency_penalty", 0), ("repetition_penalty", 1)):
            if sp.get(field, default) != default:
                raise ValueError("stateful sampling profile is not supported by this restart gate")
        if any(sp.get(k) for k in ("structured_outputs", "logits_processors", "extra_args", "bad_words")):
            raise ValueError("grammar/logits processor/bad-words profiles require a separate gate")
        if "prompt_token_ids" in p:
            ids(p["prompt_token_ids"], empty=False)
        if p["source"] == "live_captured" and not all(k in p for k in ("prompt_token_ids", "identity", "runtime_state", "request_id")):
            raise ValueError("live profile needs prompt, identity, runtime state and request ID")
    return data["profiles"]


def load_fixtures(path):
    result = read_jsonl(path)
    if not result:
        raise ValueError("empty fixture file")
    seen = set()
    for r in result:
        exact_keys(r, {"id", "sample_text", "sample_token_ids", "prompt_text", "prompt_token_ids",
                       "cuts", "pending", "suffixes", "pad_tokens", "source"},
                   {"id", "cuts", "pending", "suffixes", "source"})
        safe_name(r["id"])
        if r["id"] in seen:
            raise ValueError("duplicate fixture ID")
        seen.add(r["id"])
        if ("sample_text" in r) == ("sample_token_ids" in r):
            raise ValueError("provide exactly one sample representation")
        if "sample_token_ids" in r:
            ids(r["sample_token_ids"], empty=False)
        if "prompt_text" in r and "prompt_token_ids" in r:
            raise ValueError("ambiguous prompt representation")
        if "prompt_token_ids" in r:
            ids(r["prompt_token_ids"], empty=False)
        if r["cuts"] != "all":
            if not isinstance(r["cuts"], list) or not r["cuts"] or any(type(x) is not int or x <= 0 for x in r["cuts"]):
                raise ValueError("cuts must be all or positive token offsets")
        if not isinstance(r["pending"], list) or not r["pending"] or any(type(x) is not int or x < 0 for x in r["pending"]):
            raise ValueError("pending must be nonnegative counts")
        if not isinstance(r["suffixes"], list) or not r["suffixes"] or set(r["suffixes"])-{"original", "forced_close"}:
            raise ValueError("unknown suffix type")
        if type(r.get("pad_tokens", 0)) is not int or r.get("pad_tokens", 0) < 0:
            raise ValueError("invalid pad_tokens")
        for key in ("sample_text", "prompt_text"):
            if key in r and not isinstance(r[key], str):
                raise ValueError("text fields must be strings")
    return result


def prepare_inputs(repo, selection_path, output):
    """Extract exact archived bodies, never insert IDs into messages."""
    repo = Path(repo).resolve()
    spec = read_json(selection_path)
    exact_keys(spec, {"schema", "correlation", "proxy_run", "request_ids"},
               {"schema", "correlation", "proxy_run", "request_ids"})
    if spec["schema"] != 1 or not spec["request_ids"] or len(set(spec["request_ids"])) != len(spec["request_ids"]):
        raise ValueError("invalid request selection")
    def local(path):
        full = (repo / path).resolve()
        if not full.is_relative_to(repo):
            raise ValueError("input reference escapes repository")
        return full
    ledger_rows = read_jsonl(local(spec["correlation"]))
    ledger = {r["request_id"]: r for r in ledger_rows}
    if len(ledger) != len(ledger_rows):
        raise ValueError("duplicate request ID in correlation ledger")
    records = []
    for key in spec["request_ids"]:
        r = ledger[key]
        source = local(str(Path(spec["proxy_run"]) / r["source_file"]))
        # source_line denotes a physical line; blank lines must not shift it.
        with source.open(encoding="utf-8") as stream:
            line = next((line for number, line in enumerate(stream, 1)
                         if number == r["source_line"]), None)
        if line is None or not line.strip():
            raise ValueError(f"missing source line: {key}")
        row = json.loads(line)
        if row.get("request_id") != key or not isinstance(row.get("request_body"), dict):
            raise ValueError(f"source locator mismatch: {key}")
        records.append({"case_id": f"run05-{key}", "request_id": key,
                        "request_body": row["request_body"], "request_body_sha256": digest(row["request_body"]),
                        "provenance": r,
                        "replay_expectation": {"expected_tool_call": bool(r["tool_names"]),
                            "expected_tool_names": r["tool_names"], "latency_eligible": False,
                            "category": "g0_profile_capture"}})
    out = fresh_directory(output)
    with (out / "requests.jsonl").open("w", encoding="utf-8") as stream:
        for r in records:
            stream.write(json.dumps(r, ensure_ascii=False)+"\n")
    write_json(out / "manifest.json", {"schema": 1, "selection": spec,
               "count": len(records), "selection_sha256": digest(spec),
               "warning": "Historical reasoning lengths select inputs, not guaranteed future lengths."})
    return out
