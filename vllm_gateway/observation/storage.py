"""Atomic JSON output and bounded, partitioned JSONL append."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class RollingJsonlWriter:
    def __init__(self, root, max_bytes, fsync_interval=1.0):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.fsync_interval = fsync_interval
        self.lock = threading.Lock()
        self.states = {}
        self.dirty = set()
        self.last_sync = time.monotonic()

    def append(self, partition, record):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", partition) or any(p in {".", ".."} for p in partition.split("/")):
            raise ValueError("unsafe partition")
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock:
            directory = self.root / partition
            directory.mkdir(parents=True, exist_ok=True)
            if partition not in self.states:
                parts = [(int(p.stem[5:]), p) for p in directory.glob("part-*.jsonl") if re.fullmatch(r"part-\d+", p.stem)]
                index, path = max(parts) if parts else (1, directory / "part-00001.jsonl")
                size = path.stat().st_size if path.exists() else 0
                if size:
                    with path.open("rb") as source:
                        source.seek(-1, 2)
                        if source.read(1) != b"\n":
                            index, size = index + 1, 0
                self.states[partition] = index, size
            index, size = self.states[partition]
            if size and size + len(encoded) > self.max_bytes:
                index, size = index + 1, 0
            path = directory / f"part-{index:05d}.jsonl"
            with path.open("ab") as target:
                target.write(encoded)
            self.states[partition] = index, size + len(encoded)
            if self.fsync_interval != float("inf"):
                self.dirty.add(path)
            self._sync_due()
            return {"path": str(path), "byte_offset": size, "byte_length": len(encoded),
                    "oversized": len(encoded) > self.max_bytes}

    def _sync_due(self, force=False):
        if force or time.monotonic() - self.last_sync >= self.fsync_interval:
            for path in self.dirty:
                with path.open("ab") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
            self.dirty.clear()
            self.last_sync = time.monotonic()

    def sync(self):
        with self.lock:
            self._sync_due(force=True)
