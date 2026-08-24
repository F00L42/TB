"""Opt-in, low-cardinality DTB phase telemetry (schema 3)."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterator, Mapping

from dtb.observability.event_writer import DtbEventWriter


def _interval_ms(start: object, end: object) -> float | None:
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    if start <= 0 or end <= 0 or end < start:
        return None
    return round((end - start) * 1000, 3)


class DtbPhaseRecorder:
    """Request-scoped recorder created only for enabled telemetry modes."""

    def __init__(self, writer: DtbEventWriter, policy_version: str) -> None:
        self._writer = writer
        self._policy_version = policy_version
        self._started_at = perf_counter()
        self._durations_s: dict[str, float] = defaultdict(float)
        self._calls: dict[str, int] = defaultdict(int)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started_at = perf_counter()
        try:
            yield
        finally:
            self._durations_s[name] += perf_counter() - started_at
            self._calls[name] += 1

    def module_snapshot(self) -> dict[str, object]:
        total_s = perf_counter() - self._started_at
        return {
            name: {
                "duration_ms": round(duration * 1000, 3),
                "calls": self._calls[name],
                "share_of_total": round(duration / total_s, 6) if total_s else 0.0,
            }
            for name, duration in sorted(self._durations_s.items())
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
        budget_decision: Mapping[str, Any] | None = None,
        decision_details: Mapping[str, Any] | None = None,
        confidence_method: str | None = None,
        average_confidence: float | None = None,
        value_min_raw: float | None = None,
        pair_count: int | None = None,
        value_pair_count: int | None = None,
        accept_reason: str | None = None,
        final_answer_reason: str | None = None,
        completion_token_limit: int | None = None,
        emitted_completion_tokens_before: int | None = None,
        pending_boundary_tokens: int | None = None,
        external_remaining_tokens: int | None = None,
        answer_limit: int | None = None,
        stop_reason: object = None,
        **_: Any,
    ) -> dict[str, object]:
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
            "schema": 3,
            "parent_request_id": parent_request_id,
            "child_request_id": child_request_id,
            "variant": "dtb",
            "policy_version": self._policy_version,
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
        if budget_decision is not None:
            effective = dict(budget_decision)
            record.update({
                "scene": effective.get("scene"),
                "scene_matched": effective.get("scene_matched"),
                "decision_branch": effective.get("decision_branch"),
                "budget_decision": effective,
            })
        if decision_details is not None:
            record["decision_details"] = dict(decision_details)
        if phase == "confidence_probe":
            record.update({
                "confidence_method": confidence_method,
                "average_confidence": (
                    round(average_confidence, 6)
                    if average_confidence is not None else None
                ),
                "value_min_raw": (
                    round(value_min_raw, 6) if value_min_raw is not None else None
                ),
                "pair_count": max(pair_count, 0) if pair_count is not None else None,
                "value_pair_count": (
                    max(value_pair_count, 0)
                    if value_pair_count is not None else None
                ),
                "accept_reason": accept_reason,
            })
        if phase == "final_answer":
            record.update({
                "final_answer_reason": final_answer_reason,
                "completion_token_limit": completion_token_limit,
                "emitted_completion_tokens_before": (
                    emitted_completion_tokens_before
                ),
                "pending_boundary_tokens": pending_boundary_tokens,
                "external_remaining_tokens": external_remaining_tokens,
                "answer_limit": answer_limit,
            })
        self._writer.publish("DTB_PHASE", record)
        return record

    def record_request_summary(
        self,
        *,
        parent_request_id: str,
        state: Any,
        error: str | None,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "schema": 1,
            "parent_request_id": parent_request_id,
            "variant": "dtb",
            "policy_version": self._policy_version,
            "completion_status": state.completion_status,
            "completion_token_limit": state.completion_token_limit,
            "emitted_completion_tokens": state.emitted_completion_tokens,
            "completion_tokens_remaining": max(
                0,
                state.completion_token_limit
                - state.emitted_completion_tokens,
            ),
            "thinking_tokens": state.thinking_tokens,
            "hidden_probe_tokens": state.hidden_probe_tokens,
            "final_answer_tokens": state.final_answer_tokens,
            "total_model_generated_tokens": (
                state.thinking_tokens
                + state.hidden_probe_tokens
                + state.final_answer_tokens
            ),
            "child_generate_calls": state.child_generate_calls,
            "modules": self.module_snapshot(),
            "error": error,
        }
        self._writer.publish("DTB_REQUEST_SUMMARY", record)
        return record
