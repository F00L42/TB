"""Request-scoped latency telemetry for the Think Budget decision chain."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator


@dataclass
class TimingCollector:
    """Collect wall-clock timings without sharing mutable state across requests."""

    started_at: float = field(default_factory=perf_counter)
    durations_s: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started_at = perf_counter()
        try:
            yield
        finally:
            self.durations_s[name] += perf_counter() - started_at
            self.calls[name] += 1

    def snapshot(self) -> dict[str, object]:
        total_s = perf_counter() - self.started_at
        modules = {
            name: {
                "duration_ms": round(duration * 1000, 3),
                "calls": self.calls[name],
                "share_of_total": round(duration / total_s, 6) if total_s else 0.0,
            }
            for name, duration in sorted(self.durations_s.items())
        }
        return {"total_ms": round(total_s * 1000, 3), "modules": modules}
