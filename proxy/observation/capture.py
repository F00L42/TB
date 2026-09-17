"""Nonblocking capture with bounded data/control queues and explicit gaps."""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import importlib.metadata
import logging
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .config import digest
from .storage import RollingJsonlWriter, write_json

log = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class RequestContext:
    request_id: str
    bucket: str
    start_ns: int
    seq: int = 0
    ended: bool = False
    gap: bool = False
    dropped_bytes: int = 0
    offsets: dict = field(default_factory=lambda: {"request": 0, "response": 0})


class Capture:
    def __init__(self, config):
        self.config = config
        cap = config["capture"]
        self.capture_id = datetime.now(timezone.utc).strftime("agent_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
        self.process_id = uuid.uuid4().hex
        self.root = Path(cap["output_root"]) / self.capture_id
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / ".gitignore").write_text("*\n", encoding="utf-8")
        self.writer = RollingJsonlWriter(self.root / "archive", cap["shard_max_bytes"], cap["fsync_interval_ms"] / 1000)
        self.queue = deque()
        self.condition = threading.Condition()
        self.queued_bytes = self.controls = self.peak_bytes = 0
        self.lost_control = self.write_errors = self.dropped_bytes = 0
        self.accepted = self.finished = 0
        self.written_events = 0
        self.stopping = False
        self.last_process_save = time.monotonic()
        self.process_path = self.root / "processes" / f"{self.process_id}.json"
        identity = {}
        for args, key in [(["git", "rev-parse", "HEAD"], "repo_commit"), (["git", "status", "--porcelain"], "repo_status")]:
            try:
                result = subprocess.run(args, cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=5, check=True)
                identity[key] = result.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                identity[key] = None
        packages = {}
        for name in ("httpx", "uvicorn", "transformers", "tokenizers"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = None
        write_json(self.root / "manifest.json", {"schema": "agent_observation.capture.v1",
                   "capture_id": self.capture_id, "experiment_role": "agent_distribution",
                   "started_at_utc": utc_now(), "config": config, "config_hash": digest(config),
                   "packages": packages, **identity})
        self._save_process(False)
        self.thread = threading.Thread(target=self._run, name="observation-writer", daemon=True)
        self.thread.start()

    def _save_process(self, clean):
        write_json(self.process_path, {"process_id": self.process_id, "clean_shutdown": clean,
                   "updated_at_utc": utc_now(), "accepted_requests": self.accepted,
                   "finished_requests": self.finished, "lost_control_events": self.lost_control,
                   "write_errors": self.write_errors, "dropped_bytes": self.dropped_bytes,
                   "queue_peak_bytes": self.peak_bytes})

    def begin(self, metadata):
        self.accepted += 1
        started = utc_now()
        context = RequestContext(uuid.uuid4().hex, started[:10] + "/" + started[11:13], time.monotonic_ns())
        self.emit(context, "request_started", {**metadata, "started_at_utc": started,
                  "arrival_seq": self.accepted, "process_id": self.process_id, "bucket": context.bucket})
        return context

    def emit(self, ctx, kind, payload, size=0):
        event = {"schema": "agent_observation.event.v1", "request_id": ctx.request_id,
                 "event_seq": ctx.seq, "kind": kind, "at_utc": utc_now(),
                 "elapsed_ns": time.monotonic_ns() - ctx.start_ns, "payload": payload}
        ctx.seq += 1
        cost = size + 512 if size else 0  # Bound tiny-chunk object overhead as well as body bytes.
        with self.condition:
            if size:
                if ctx.gap or self.queued_bytes + cost > self.config["capture"]["queue_max_bytes"]:
                    ctx.gap = True
                    ctx.dropped_bytes += size
                    self.dropped_bytes += size
                    return False
            elif self.controls >= self.config["capture"]["control_queue_max_events"]:
                ctx.gap = True
                self.lost_control += 1
                log.error("Capture control queue full: %s", ctx.request_id)
                return False
            self.queue.append((ctx.bucket, event, size))
            self.queued_bytes += cost
            self.controls += not bool(size)
            self.peak_bytes = max(self.peak_bytes, self.queued_bytes)
            self.condition.notify()
        return True

    def body(self, ctx, direction, data):
        ctx.offsets.setdefault(direction, 0)
        step = self.config["capture"]["body_fragment_bytes"]
        for start in range(0, len(data), step):
            chunk = data[start:start + step]
            offset = ctx.offsets[direction]
            ctx.offsets[direction] += len(chunk)
            # Base64 encoding and disk writes stay in the writer thread.
            self.emit(ctx, f"{direction}_body_fragment", {"offset": offset, "data": chunk}, len(chunk))

    def finish(self, ctx, status, **extra):
        if ctx.ended:
            return
        ctx.ended = True
        self.finished += 1
        self.emit(ctx, "request_terminal", {"transport_status": status,
                  "capture_status": "incomplete" if ctx.gap else "complete",
                  "dropped_bytes": ctx.dropped_bytes, "body_bytes": ctx.offsets,
                  "completed_at_utc": utc_now(), **extra})

    def _run(self):
        while True:
            with self.condition:
                if not self.queue and not self.stopping:
                    self.condition.wait(self.config["capture"]["flush_interval_ms"] / 1000)
                if not self.queue:
                    if self.stopping:
                        break
                    item = None
                else:
                    item = self.queue.popleft()
                    self.queued_bytes -= item[2] + 512 if item[2] else 0
                    self.controls -= not bool(item[2])
            try:
                if item:
                    bucket, event, size = item
                    if size:
                        event["payload"]["data_b64"] = base64.b64encode(event["payload"].pop("data")).decode("ascii")
                    self.writer.append(bucket, event)
                    self.written_events += 1
                elif time.monotonic() - self.last_process_save >= self.config["capture"]["fsync_interval_ms"] / 1000:
                    self.writer.sync()
                    self._save_process(False)
                    self.last_process_save = time.monotonic()
            except Exception:
                self.write_errors += 1
                log.exception("Observation storage failed; forwarding continues")
        try:
            self.writer.sync()
            self._save_process(self.write_errors == 0 and self.lost_control == 0 and self.accepted == self.finished)
        except Exception:
            log.exception("Cannot finalize capture")

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()
        self.thread.join()
