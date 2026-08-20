"""Non-blocking process writer used only when DTB telemetry is enabled."""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any


_LOGGER = logging.getLogger(__name__)
_STOP = object()


class DtbEventWriter:
    """Serialize and write telemetry outside request coroutines."""

    def __init__(
        self,
        capacity: int,
        sink: Callable[[str], None] | None = None,
    ) -> None:
        self._queue: queue.Queue[object] = queue.Queue(maxsize=capacity)
        self._sink = sink or self._stdout_sink
        self._dropped = 0
        self._reported_drops = 0
        self._last_drop_warning = 0.0
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="dtb-telemetry-writer",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _stdout_sink(line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def publish(self, event_name: str, record: dict[str, Any]) -> bool:
        """Queue a record without waiting or serializing on the caller thread."""
        if self._closed:
            return False
        try:
            self._queue.put_nowait((event_name, record))
            return True
        except queue.Full:
            self._dropped += 1
            return False

    def _warn_about_drops(self) -> None:
        dropped = self._dropped
        if dropped <= self._reported_drops:
            return
        now = time.monotonic()
        if self._reported_drops == 0 or now - self._last_drop_warning >= 60.0:
            self._reported_drops = dropped
            self._last_drop_warning = now
            _LOGGER.warning(
                "DTB telemetry queue full; dropped_events=%d", dropped
            )

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                event_name, record = item
                line = event_name + " " + json.dumps(
                    record, ensure_ascii=False, separators=(",", ":")
                )
                self._sink(line)
            except Exception:
                _LOGGER.exception("failed to write DTB telemetry event")
            finally:
                self._queue.task_done()
                self._warn_about_drops()

    def flush(self, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.001)
        return True

    def close(self, timeout_s: float = 2.0) -> bool:
        if self._closed:
            return True
        flushed = self.flush(timeout_s)
        self._closed = True
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            return False
        self._thread.join(max(timeout_s, 0.0))
        return flushed and not self._thread.is_alive()
