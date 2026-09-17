"""Replay capture snapshots through a disk-backed event index."""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
import csv
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import zlib

from .chat_completions import decode_body, first_semantic_offset, parse_response
from .config import canonical, digest
from .report import distribution, render_views
from .storage import write_json
from .tokens import TokenCounter
from .trajectories import build_trajectories


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def snapshot_events(root, db, problems):
    db.execute("CREATE TABLE events (rid TEXT, seq INTEGER, event TEXT, ref TEXT, PRIMARY KEY(rid,seq))")
    files = []
    for path in sorted((root / "archive").glob("*/*/part-*.jsonl")):
        size = path.stat().st_size
        hasher = hashlib.sha256()
        consumed = 0
        with path.open("rb") as source:
            while consumed < size:
                rid = None
                offset = consumed
                line = source.readline(size - consumed)
                consumed += len(line)
                hasher.update(line)
                ref = {"path": path.relative_to(root).as_posix(), "byte_offset": offset, "byte_length": len(line)}
                try:
                    if not line.endswith(b"\n"):
                        raise ValueError("incomplete_tail")
                    event = json.loads(line)
                    if event.get("schema") != "agent_observation.event.v1":
                        raise ValueError("unsupported_schema")
                    rid, seq = event["request_id"], event["event_seq"]
                    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", rid) or type(seq) is not int or seq < 0:
                        raise ValueError("invalid_event_identity")
                    if not isinstance(event.get("payload"), dict) or not isinstance(event.get("kind"), str) or type(event.get("elapsed_ns")) is not int:
                        raise ValueError("invalid_event_fields")
                    db.execute("INSERT INTO events VALUES (?,?,?,?)", (rid, seq, line.decode("utf-8"), canonical(ref)))
                except (ValueError, KeyError, TypeError, AttributeError, sqlite3.IntegrityError) as exc:
                    problems.append({"type": type(exc).__name__, "request_id": rid, "source": ref})
        files.append({"path": path.relative_to(root).as_posix(), "size": size, "sha256": hasher.hexdigest()})
    db.commit()
    return files


def reconstruct(db, rid, config, counter):
    events = db.execute("SELECT event,ref FROM events WHERE rid=? ORDER BY seq", (rid,))
    buffers = {"request": bytearray(), "response": bytearray()}
    offsets = {"request": 0, "response": 0}
    warnings, refs, timings = [], [], {}
    start = terminal = headers = None
    transform = None
    last_response = None
    response_marks = []
    semantic_ms = None
    body_end_at = None
    expected_seq = 0
    limit = config["report"]["max_body_bytes"]
    for encoded, encoded_ref in events:
        event = json.loads(encoded)
        refs.append(json.loads(encoded_ref))
        if event["event_seq"] != expected_seq:
            warnings.append("event_sequence_gap")
        expected_seq = event["event_seq"] + 1
        kind, payload = event["kind"], event["payload"]
        timings[kind] = event["elapsed_ns"] / 1e6
        if kind == "request_started":
            if start is not None:
                warnings.append("duplicate_start")
            start = payload
        elif kind == "request_terminal":
            if terminal is not None:
                warnings.append("duplicate_terminal")
            terminal = payload
        elif kind == "upstream_headers":
            headers = payload
        elif kind == "request_body_end":
            body_end_at = event["at_utc"]
        elif kind == "request_body_transform":
            transform = payload
        elif kind.endswith("_body_fragment"):
            direction = kind.removesuffix("_body_fragment")
            try:
                if direction not in {"request", "response", "forwarded_request"}:
                    raise ValueError("unknown_body_direction")
                offsets.setdefault(direction, 0)
                buffers.setdefault(direction, bytearray())
                data = base64.b64decode(payload["data_b64"], validate=True)
                if payload["offset"] != offsets[direction]:
                    warnings.append("body_offset_gap")
                offsets[direction] = payload["offset"] + len(data)
                if len(buffers[direction]) + len(data) <= limit:
                    buffers[direction].extend(data)
                else:
                    warnings.append("analysis_body_limit")
                if direction == "response":
                    last_response = event["at_utc"]
                    response_marks.append((offsets[direction], event["elapsed_ns"] / 1e6))
            except (ValueError, KeyError):
                warnings.append("invalid_body_fragment")
    if start is None:
        # A lost control record must not make the entire archive unreadable.
        start = {"started_at_utc": "1970-01-01T00:00:00+00:00", "process_id": "unknown",
                 "arrival_seq": 0, "bucket": "1970-01-01/00", "labels": {}, "path": "", "headers": []}
        warnings.append("missing_start")
    if terminal is None:
        terminal = {"transport_status": "interrupted_unknown", "capture_status": "incomplete"}
        warnings.append("missing_terminal")
    if terminal.get("body_bytes", offsets) != offsets:
        warnings.append("body_size_mismatch")
    capture_status = "complete" if terminal.get("capture_status") == "complete" and not any(w != "analysis_body_limit" for w in warnings) else "incomplete"
    request = {}
    original_request = None
    response = {"parse_status": "unsupported", "choices": [], "usage": None, "warnings": []}
    request_parse_error = None
    if not start.get("metadata_only") and capture_status == "complete" and "analysis_body_limit" not in warnings:
        try:
            decoded = decode_body(bytes(buffers["request"]), start["headers"], limit)
            request = json.loads(decoded) if decoded else {}
            if not isinstance(request, dict):
                raise ValueError("request_not_object")
            if transform:
                original_request = request
                decoded = decode_body(bytes(buffers.get("forwarded_request", b"")), transform["headers"], limit)
                request = json.loads(decoded)
                if not isinstance(request, dict):
                    raise ValueError("forwarded_request_not_object")
        except (ValueError, UnicodeError, zlib.error) as exc:
            request_parse_error = type(exc).__name__
            request = {}
        if start["path"] in config["protocols"]["chat_completions"]["paths"]:
            if not isinstance(request.get("messages"), list) or not all(isinstance(m, dict) for m in request.get("messages", [])):
                request_parse_error = request_parse_error or "invalid_messages"
            if request_parse_error:
                response["parse_status"] = "parse_error"
                warnings.append("request_parse_error:" + request_parse_error)
            elif headers:
                try:
                    decoded = decode_body(bytes(buffers["response"]), headers["headers"], limit)
                    response = parse_response(decoded, headers["headers"], config["protocols"]["chat_completions"]["reasoning_fields"])
                    if not any(k.lower() == "content-encoding" and v.lower() not in {"identity", ""} for k, v in headers["headers"]):
                        semantic_offset = first_semantic_offset(decoded, response["streaming"], config["protocols"]["chat_completions"]["reasoning_fields"])
                        semantic_ms = next((ms for end, ms in response_marks if semantic_offset is not None and end >= semantic_offset), None)
                except (ValueError, zlib.error) as exc:
                    response["parse_status"] = "parse_error"
                    warnings.append("response_decode_error:" + type(exc).__name__)
    tokens = counter.measure(response, request.get("model"))
    tokens["output_scope"] = "complete_output" if response["parse_status"] == "complete" and terminal["transport_status"] == "completed" else "observed_partial_or_unsupported"
    one = tokens["choices"][0] if len(tokens["choices"]) == 1 else {}
    def local(name):
        return one.get(name, {}).get("value")
    tool_values = [local("tool_name"), local("tool_arguments")]
    exclusions = []
    if "analysis_body_limit" in warnings:
        exclusions.append("analysis_body_limit")
    if capture_status != "complete":
        exclusions.append("capture_incomplete")
    if terminal["transport_status"] != "completed":
        exclusions.append(terminal["transport_status"])
    if not headers or not 200 <= headers["status_code"] < 300:
        exclusions.append("http_error_or_missing")
    if response["parse_status"] != "complete":
        exclusions.append(response["parse_status"])
    if len(response["choices"]) != 1:
        exclusions.append("not_single_choice")
    if any(c["finish_reason"] not in {"stop", "tool_calls", "function_call"} for c in response["choices"]):
        exclusions.append("censored_or_other_finish")
    if local("reasoning") is None:
        exclusions.append("reasoning_unknown")
    row = {"request_id": rid, "started_at_utc": start["started_at_utc"],
           "completed_at_utc": terminal.get("completed_at_utc"), "process_id": start["process_id"],
           "arrival_seq": start["arrival_seq"], "bucket": start["bucket"], "labels": start["labels"],
           "model": tokens["model"], "tokenizer_ref": tokens["tokenizer_ref"],
           "body_modified": transform is not None,
           "path": start["path"], "transport_status": terminal["transport_status"],
           "capture_status": capture_status, "parse_status": response["parse_status"],
           "status_code": headers["status_code"] if headers else None,
           "e2e_ms": timings.get("downstream_send_end", timings.get("request_terminal")),
           "ttfb_ms": timings.get("first_body_byte"), "timings_ms": timings,
           "first_semantic_delta_ms": semantic_ms,
           "last_response_at_utc": last_response, "body_received_at_utc": body_end_at or start["started_at_utc"],
           "prompt_tokens_api": tokens["api"]["prompt"]["value"],
           "completion_tokens_api": tokens["api"]["completion"]["value"],
           "reasoning_tokens_local": local("reasoning"), "content_tokens_local": local("content"),
           "tool_tokens_local": sum(tool_values) if all(v is not None for v in tool_values) else None,
           "visible_reasoning_share": one.get("visible_reasoning_share"),
           "tool_names": sorted({t.get("function", {}).get("name", "") for c in response["choices"] for t in c["message"].get("tool_calls", [])}),
           "finish_reasons": [c["finish_reason"] for c in response["choices"]],
           "exclusions": sorted(set(exclusions)),
           "detail_json": f"details/{rid}.json", "detail_markdown": f"requests/{start['bucket']}/{rid}.md"}
    return row, {"request": request, "response": response, "tokens": tokens,
                 "original_request": original_request, "request_transform": transform,
                 "warnings": sorted(set(warnings + response["warnings"])), "raw_refs": refs,
                 "request_metadata": start}


def write_jsonl(path, records):
    with Path(path).open("w", encoding="utf-8", newline="\n") as out:
        for record in records:
            out.write(canonical(record) + "\n")


def parse_time(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("time filters must include a timezone")
    return parsed


def analyze_capture(root, *, live=False, counter=None, from_time=None, to_time=None, thresholds=None, annotations=None):
    root = Path(root).resolve()
    manifest = read_json(root / "manifest.json")
    if manifest.get("schema") != "agent_observation.capture.v1":
        raise ValueError("not an observation capture")
    config = manifest["config"]
    lower, upper = parse_time(from_time), parse_time(to_time)
    if lower and upper and lower >= upper:
        raise ValueError("from must precede to")
    thresholds = config["report"]["thresholds"] if thresholds is None else thresholds
    if any(type(h) is not int or h < 0 for h in thresholds):
        raise ValueError("thresholds must be nonnegative integers")
    counter = counter or TokenCounter.from_config(config)
    counter.prepare()
    derived = root / "derived"
    derived.mkdir(exist_ok=True)
    problems = []
    with tempfile.TemporaryDirectory(prefix=".analysis-", dir=derived) as temporary:
        work = Path(temporary)
        database = work / "events.sqlite"
        with closing(sqlite3.connect(database)) as db:
            files = snapshot_events(root, db, problems)
            rows = []
            ids = [item[0] for item in db.execute("SELECT DISTINCT rid FROM events")]
            for rid in ids:
                row, detail = reconstruct(db, rid, config, counter)
                if any(p.get("request_id") == rid for p in problems):
                    row["capture_status"] = "incomplete"
                    row["exclusions"] = sorted(set([*row["exclusions"], "invalid_or_duplicate_event"]))
                when = parse_time(row["started_at_utc"])
                if (lower and when < lower) or (upper and when >= upper):
                    continue
                write_json(work / row["detail_json"], detail)
                rows.append(row)
        database.unlink()
        rows.sort(key=lambda r: (r["started_at_utc"], r["process_id"], r["arrival_seq"], r["request_id"]))
        annotation_hash = None
        if annotations:
            data = Path(annotations).read_bytes()
            annotation_hash = hashlib.sha256(data).hexdigest()
            by_id = {r["request_id"]: r for r in rows}
            seen = set()
            for raw in data.decode("utf-8").splitlines():
                item = json.loads(raw)
                rid = item.get("request_id")
                if item.get("capture_id") != manifest["capture_id"] or rid not in by_id or rid in seen:
                    problems.append({"type": "annotation_conflict_or_unknown", "request_id": rid})
                    continue
                seen.add(rid)
                by_id[rid]["annotation"] = item
        processes = [read_json(path) for path in sorted((root / "processes").glob("*.json"))]
        integrity = {"status": "complete", "invalid_events": problems,
                     "incomplete_requests": sum(r["capture_status"] != "complete" for r in rows),
                     "processes": processes, "snapshot_live": live,
                     "known_request_count": len(ids), "selected_request_count": len(rows)}
        if problems or integrity["incomplete_requests"] or any(p.get("write_errors") or p.get("lost_control_events") or p.get("dropped_bytes") for p in processes):
            integrity["status"] = "incomplete"
        elif not processes or any(not p.get("clean_shutdown") for p in processes):
            integrity["status"] = "live_snapshot" if live else "unclean_or_running"
        elif sum(p.get("accepted_requests", 0) for p in processes) != len(ids):
            integrity["status"] = "incomplete"
            integrity["request_count_mismatch"] = True
        groups = defaultdict(list)
        for row in rows:
            key = canonical({"agent": row["labels"].get("agent", "unknown"),
                             "scenario": row["labels"].get("scenario", "unknown"),
                             "variant": row["labels"].get("variant", "unknown"), "model": row["model"],
                             "tokenizer": row["tokenizer_ref"], "path": row["path"]})
            groups[key].append(row)
        grouped = {key: distribution(group, thresholds) for key, group in groups.items()}
        load_detail = lambda row: read_json(work / row["detail_json"])
        trajectories, edges = build_trajectories(rows, load_detail, config["trajectories"]["projection_profiles"]) if config["trajectories"]["enabled"] else ([], [])
        for name, records in (("requests", rows), ("trajectories", trajectories), ("trajectory_edges", edges)):
            write_jsonl(work / (name + ".jsonl"), records)
        write_json(work / "integrity.json", integrity)
        write_json(work / "distribution.json", grouped)
        with (work / "token_usage.csv").open("w", encoding="utf-8", newline="") as stream:
            fields = ["request_id", "started_at_utc", "model", "prompt_tokens_api", "completion_tokens_api", "reasoning_tokens_local", "content_tokens_local", "tool_tokens_local", "e2e_ms", "exclusions"]
            writer = csv.DictWriter(stream, fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        with (work / "threshold_scan.csv").open("w", encoding="utf-8", newline="") as stream:
            fields = ["group", "threshold", "n", "short_fraction", "tail_fraction", "at_boundary_count", "tail_token_share", "excess_tokens"]
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            for key, stats in grouped.items():
                writer.writerows({"group": key, **scan} for scan in stats["threshold_scan"])
        render_views(work, rows, trajectories, load_detail, config["report"]["timezone"], integrity, grouped)
        analysis_identity = {"schema": "agent_observation.analysis.v1", "capture_id": manifest["capture_id"],
                             "input_files": files, "config_hash": manifest["config_hash"],
                             "from": from_time, "to": to_time, "thresholds": thresholds,
                             "annotations_sha256": annotation_hash, "tokenizers": counter.identities,
                             "parser_version": 2, "trajectory_version": 1, "integrity": integrity}
        analysis_identity["analysis_code_sha256"] = digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                                           for path in sorted(Path(__file__).parent.glob("*.py"))})
        analysis_id = digest(analysis_identity)[:20]
        write_json(work / "analysis_manifest.json", {"analysis_id": analysis_id, **analysis_identity})
        target = derived / ("live" if live else analysis_id)
        if target.exists() and not live:
            return target
        if live:
            # One reporting thread owns live/. Each file is replaced atomically, manifest last.
            target.mkdir(exist_ok=True)
            for path in sorted(work.rglob("*"), key=lambda p: p.name == "analysis_manifest.json"):
                if path.is_file():
                    dest = target / path.relative_to(work)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    path.replace(dest)
        else:
            # Publish a finished immutable report as one directory operation.
            work.rename(target)
        return target


def main():
    parser = argparse.ArgumentParser(description="Analyze passive agent captures")
    parser.add_argument("capture_dir")
    parser.add_argument("--from", dest="from_time")
    parser.add_argument("--to", dest="to_time")
    parser.add_argument("--thresholds", type=lambda text: [int(x) for x in text.split(",")])
    parser.add_argument("--annotations")
    args = parser.parse_args()
    print(analyze_capture(args.capture_dir, from_time=args.from_time, to_time=args.to_time,
                          thresholds=args.thresholds, annotations=args.annotations) / "SUMMARY.md")


if __name__ == "__main__":
    main()
