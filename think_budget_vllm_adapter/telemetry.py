"""Low-cardinality, request-scoped telemetry for Think Budget.

The API server emits one ``TB_PHASE`` JSON object per controller phase.  The
record deliberately contains counts and timings only: prompts and generated
text never enter the performance log.
"""
from __future__ import annotations

import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator


def _interval_ms(start: object, end: object) -> float | None:
    """Return a monotonic timestamp interval, or ``None`` when unavailable."""
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    if start <= 0 or end <= 0 or end < start:
        return None
    return round((end - start) * 1000, 3)


@dataclass
class BudgetLedger:
    """Account for model work that OpenAI completion usage does not expose."""

    visible_think_tokens: int = 0
    hidden_probe_tokens: int = 0
    final_answer_tokens: int = 0
    generate_calls: int = 0

    def record(self, phase: str, generated_tokens: int) -> None:
        if phase == "decision":
            return
        self.generate_calls += 1
        if phase in {"initial_think", "iterative_think"}:
            self.visible_think_tokens += generated_tokens
        elif phase == "confidence_probe":
            self.hidden_probe_tokens += generated_tokens
        elif phase == "final_answer":
            self.final_answer_tokens += generated_tokens

    def snapshot(self) -> dict[str, int]:
        return {
            "visible_think_tokens": self.visible_think_tokens,
            "hidden_probe_tokens": self.hidden_probe_tokens,
            "final_answer_tokens": self.final_answer_tokens,
            "total_model_generated_tokens": (
                self.visible_think_tokens
                + self.hidden_probe_tokens
                + self.final_answer_tokens
            ),
            "generate_calls": self.generate_calls,
        }


@dataclass
class TimingCollector:
    """Collect wall-clock timings without sharing mutable state across requests."""

    started_at: float = field(default_factory=perf_counter)
    durations_s: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    ledger: BudgetLedger = field(default_factory=BudgetLedger)
    emit_phase_metrics: bool = True

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
        return {
            "total_ms": round(total_s * 1000, 3),
            "modules": modules,
            "ledger": self.ledger.snapshot(),
        }

    def record_phase(
        self,
        *,
        parent_request_id: str,
        child_request_id: str,
        phase: str,
        round_id: int,
        wall_ms: float,
        controller_cpu_ms: float,
        prompt_tokens: int,
        generated_tokens: int,
        visible: bool,
        output: Any = None,
        confidence: float | None = None,
        accepted: bool | None = None,
        stop_reason: object = None,
    ) -> dict[str, object]:
        """Build and optionally emit one schema-stable phase record.

        vLLM v0.23.0 exposes request timestamps on ``RequestOutput.metrics``.
        Older builds or ``--disable-log-stats`` may omit them; in that case the
        corresponding JSON fields remain ``null`` instead of pretending to be
        zero-cost phases.
        """
        metrics = getattr(output, "metrics", None)
        queue_ms = _interval_ms(
            getattr(metrics, "queued_ts", None),
            getattr(metrics, "scheduled_ts", None),
        )
        prefill_ms = _interval_ms(
            getattr(metrics, "scheduled_ts", None),
            getattr(metrics, "first_token_ts", None),
        )
        decode_ms = _interval_ms(
            getattr(metrics, "first_token_ts", None),
            getattr(metrics, "last_token_ts", None),
        )
        metric_generated_tokens = getattr(metrics, "num_generation_tokens", None)
        if isinstance(metric_generated_tokens, int) and metric_generated_tokens >= 0:
            generated_tokens = metric_generated_tokens

        cached_prompt_tokens = getattr(output, "num_cached_tokens", None)
        if not isinstance(cached_prompt_tokens, int):
            cached_prompt_tokens = None

        if stop_reason is None and output is not None:
            try:
                completion = output.outputs[0]
                stop_reason = (
                    getattr(completion, "stop_reason", None)
                    or getattr(completion, "finish_reason", None)
                )
            except (AttributeError, IndexError, TypeError):
                stop_reason = None
        if stop_reason is not None and not isinstance(
            stop_reason, (str, int, float, bool)
        ):
            stop_reason = str(stop_reason)

        record: dict[str, object] = {
            "schema": 1,
            "parent_request_id": parent_request_id,
            "child_request_id": child_request_id,
            "variant": "think_budget",
            "phase": phase,
            "round": round_id,
            "wall_ms": round(max(wall_ms, 0.0), 3),
            "queue_ms": queue_ms,
            "prefill_ms": prefill_ms,
            "decode_ms": decode_ms,
            "controller_cpu_ms": round(max(controller_cpu_ms, 0.0), 3),
            "prompt_tokens": max(prompt_tokens, 0),
            "cached_prompt_tokens": cached_prompt_tokens,
            "generated_tokens": max(generated_tokens, 0),
            "visible": visible,
            "confidence": round(confidence, 6) if confidence is not None else None,
            "accepted": accepted,
            "stop_reason": stop_reason,
        }
        self.ledger.record(phase, max(generated_tokens, 0))
        if self.emit_phase_metrics:
            print(
                "TB_PHASE "
                + json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )
        return record
