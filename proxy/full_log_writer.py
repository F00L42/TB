"""Bounded, proxy-session-aware storage for request/response records."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PART_RE = re.compile(r"^part-(\d{5})\.jsonl$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FullLogWriteResult:
    """Location and size facts for one persisted full-log record."""

    path: Path
    byte_offset: int
    byte_length: int
    oversized: bool


@dataclass
class _ShardState:
    part_index: int
    size_bytes: int


class FullLogShardWriter:
    """Append JSONL records to bounded per-proxy-session shard files.

    The size limit is a target for a shard, not permission to split a JSON
    record. A record larger than ``max_bytes`` is stored alone and reported as
    oversized. Writes are serialized within the process so concurrent request
    completions cannot interleave JSONL lines.
    """

    def __init__(self, root_dir: str | Path, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.root_dir = Path(root_dir)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._states: dict[str, _ShardState] = {}

    def append(self, record: Mapping[str, Any]) -> FullLogWriteResult:
        proxy_session_id = str(
            record.get("proxy_session_id")
            or record.get("session_id")
            or "unassigned"
        )
        if not _SESSION_ID_RE.fullmatch(proxy_session_id):
            raise ValueError(
                "unsafe proxy_session_id for full-log path: "
                f"{proxy_session_id!r}"
            )

        encoded = (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        with self._lock:
            session_dir = self.root_dir / f"proxy_session_{proxy_session_id}"
            session_dir.mkdir(parents=True, exist_ok=True)
            state = self._states.get(proxy_session_id)
            if state is None:
                state = self._load_state(session_dir)

            if state.size_bytes and state.size_bytes + len(encoded) > self.max_bytes:
                state = _ShardState(state.part_index + 1, 0)

            path = self._part_path(session_dir, state.part_index)
            byte_offset = state.size_bytes
            with path.open("ab") as target:
                target.write(encoded)

            state.size_bytes += len(encoded)
            self._states[proxy_session_id] = state
            try:
                self._write_manifest_once(
                    session_dir, record, proxy_session_id
                )
            except OSError as exc:
                # The JSONL part is authoritative. A convenience manifest must
                # never turn a successful request record into a logging loss.
                logger.warning(
                    "full-log manifest write failed for proxy session %s: %s",
                    proxy_session_id,
                    exc,
                )
            return FullLogWriteResult(
                path=path,
                byte_offset=byte_offset,
                byte_length=len(encoded),
                oversized=len(encoded) > self.max_bytes,
            )

    def _load_state(self, session_dir: Path) -> _ShardState:
        parts = []
        for path in session_dir.iterdir():
            match = _PART_RE.fullmatch(path.name)
            if match and path.is_file():
                parts.append((int(match.group(1)), path))
        if not parts:
            return _ShardState(part_index=1, size_bytes=0)

        part_index, path = max(parts, key=lambda item: item[0])
        size_bytes = path.stat().st_size
        if size_bytes and not self._has_complete_tail(path):
            # Preserve a crash-truncated shard for diagnosis. New records start
            # in a fresh part instead of being appended to an invalid JSON line.
            return _ShardState(part_index=part_index + 1, size_bytes=0)
        return _ShardState(part_index=part_index, size_bytes=size_bytes)

    @staticmethod
    def _write_manifest_once(
        session_dir: Path,
        record: Mapping[str, Any],
        proxy_session_id: str,
    ) -> None:
        manifest_path = session_dir / "manifest.json"
        if manifest_path.exists():
            return
        correlation = record.get("correlation")
        manifest = {
            "schema": 1,
            "proxy_session_id": proxy_session_id,
            "first_request_id": record.get("request_id"),
            "first_timestamp": record.get("timestamp"),
            "correlation": correlation if isinstance(correlation, Mapping) else {},
        }
        temporary_path = session_dir / f"manifest.{os.getpid()}.tmp"
        with temporary_path.open("w", encoding="utf-8") as target:
            json.dump(manifest, target, ensure_ascii=False, indent=2)
            target.write("\n")
        os.replace(temporary_path, manifest_path)

    @staticmethod
    def _has_complete_tail(path: Path) -> bool:
        with path.open("rb") as source:
            source.seek(-1, 2)
            return source.read(1) == b"\n"

    @staticmethod
    def _part_path(session_dir: Path, part_index: int) -> Path:
        return session_dir / f"part-{part_index:05d}.jsonl"
