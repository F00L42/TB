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

try:
    from .observation.storage import RollingJsonlWriter
except ImportError:  # Existing direct execution from proxy/ remains supported.
    from observation.storage import RollingJsonlWriter


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FullLogWriteResult:
    """Location and size facts for one persisted full-log record."""

    path: Path
    byte_offset: int
    byte_length: int
    oversized: bool


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
        self._writer = RollingJsonlWriter(self.root_dir, max_bytes, fsync_interval=float("inf"))

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

        with self._lock:
            session_dir = self.root_dir / f"proxy_session_{proxy_session_id}"
            result = self._writer.append(f"proxy_session_{proxy_session_id}", record)
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
                path=Path(result["path"]),
                byte_offset=result["byte_offset"],
                byte_length=result["byte_length"],
                oversized=result["oversized"],
            )

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
