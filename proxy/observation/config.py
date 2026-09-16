"""Validated configuration. Importing the proxy never creates output files."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

DEFAULTS = {
    "listen": {"host": "127.0.0.1", "port": 9000},
    "upstream": {"base_url": "http://127.0.0.1:9001", "auth_mode": "forward",
                 "auth_env": None, "trust_env": False,
                 "path_mapping": {"incoming_prefix": "/", "upstream_prefix": "/"},
                 "timeout": {"connect_seconds": 30, "read_seconds": None,
                             "write_seconds": 30, "pool_seconds": 30}},
    "capture": {"output_root": "docs/log/perf", "label": "agent-observation",
                "labels": {}, "bucket": "hour_utc", "shard_max_bytes": 16777216,
                "queue_max_bytes": 67108864, "control_queue_max_events": 4096,
                "on_overflow": "drop_capture", "body_fragment_bytes": 65536,
                "flush_interval_ms": 250, "fsync_interval_ms": 1000,
                "metadata_only_paths": ["/metrics", "/health", "/v1/models"],
                "redact_query_keys": ["api_key", "key", "token", "access_token"]},
    "protocols": {"chat_completions": {"paths": ["/v1/chat/completions"],
                  "reasoning_fields": ["reasoning_content", "reasoning"]}},
    "tokenizers": {},
    "report": {"timezone": "Asia/Shanghai", "thresholds": [256, 512, 850, 1024, 2048, 4096],
               "live_interval_seconds": 5, "max_body_bytes": 33554432},
    "trajectories": {"enabled": True, "matching": "exact",
                     "projection_profiles": [], "show_prompt_history": True},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load_config(path=None, overrides=None):
    import yaml
    base = Path(path).resolve().parent if path else Path.cwd()
    supplied = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if path else overrides
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict):
        raise ValueError("configuration must be a mapping")
    result = copy.deepcopy(DEFAULTS)

    def merge(dst, src, prefix=""):
        for key, value in src.items():
            if key not in dst:
                raise ValueError(f"unknown configuration: {prefix}{key}")
            if isinstance(dst[key], dict) and dst[key]:
                if not isinstance(value, dict):
                    raise ValueError(f"{prefix}{key} must be a mapping")
                merge(dst[key], value, f"{prefix}{key}.")
            else:
                dst[key] = value
    merge(result, supplied)
    if not isinstance(result["listen"]["host"], str) or type(result["listen"]["port"]) is not int or not 0 < result["listen"]["port"] <= 65535:
        raise ValueError("listen requires a host string and valid port")
    upstream = result["upstream"]
    url = urlsplit(upstream["base_url"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("base_url must be a fixed HTTP(S) URL without credentials/query/fragment")
    for prefix in upstream["path_mapping"].values():
        if not isinstance(prefix, str) or not prefix.startswith("/") or "?" in prefix or "#" in prefix:
            raise ValueError("path prefixes must be absolute URL paths")
    if upstream["auth_mode"] not in {"forward", "bearer_env"}:
        raise ValueError("auth_mode must be forward or bearer_env")
    if upstream["auth_mode"] == "bearer_env" and not upstream["auth_env"]:
        raise ValueError("bearer_env requires auth_env")
    for value in upstream["timeout"].values():
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("timeouts must be positive or null")
    cap = result["capture"]
    if not isinstance(cap["labels"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in cap["labels"].items()):
        raise ValueError("capture.labels must map strings to strings")
    if cap["bucket"] != "hour_utc" or cap["on_overflow"] != "drop_capture":
        raise ValueError("only hour_utc/drop_capture are supported")
    for key in ("shard_max_bytes", "queue_max_bytes", "control_queue_max_events", "body_fragment_bytes", "flush_interval_ms", "fsync_interval_ms"):
        if not isinstance(cap[key], int) or isinstance(cap[key], bool) or cap[key] <= 0:
            raise ValueError(f"capture.{key} must be a positive integer")
    if not 1 <= cap["body_fragment_bytes"] <= 65536:
        raise ValueError("body_fragment_bytes must be <= 65536")
    cap["output_root"] = str((base / cap["output_root"]).resolve())
    if not isinstance(result["tokenizers"], dict):
        raise ValueError("tokenizers must map model names to local tokenizer settings")
    for spec in result["tokenizers"].values():
        if set(spec) - {"path", "local_files_only", "trust_remote_code"}:
            raise ValueError("unknown tokenizer setting")
        if spec.get("trust_remote_code", False) or not spec.get("local_files_only", True):
            raise ValueError("only local, non-remote-code tokenizers are supported")
        spec["path"] = str((base / spec["path"]).resolve())
    from zoneinfo import ZoneInfo
    ZoneInfo(result["report"]["timezone"])
    if result["report"]["max_body_bytes"] <= 0 or result["report"]["live_interval_seconds"] < 0:
        raise ValueError("invalid report limits")
    if any(type(n) is not int or n < 0 for n in result["report"]["thresholds"]):
        raise ValueError("thresholds must be nonnegative integers")
    traj = result["trajectories"]
    if traj["matching"] != "exact" or set(traj["projection_profiles"]) - {"omit_reasoning"}:
        raise ValueError("supported matching: exact; optional profile: omit_reasoning")
    if not traj["show_prompt_history"]:
        raise ValueError("show_prompt_history=false is not supported; disable trajectories instead")
    return result
