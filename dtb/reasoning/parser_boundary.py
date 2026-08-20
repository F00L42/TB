"""Preserve parser-facing text/token continuity at DTB reasoning boundaries."""
from __future__ import annotations

import copy
from typing import Any

from dtb.reasoning.request_context import DtbRequestContext
from dtb.reasoning.request_state import ReasoningBoundary


THINK_END_TAG = "</think>"
TOOL_CALL_START_TAG = "<tool_call>"


def _append_stops(params: Any, *required_stops: str) -> None:
    current_stop = getattr(params, "stop", None)
    stops = [current_stop] if isinstance(current_stop, str) else list(
        current_stop or []
    )
    for stop in required_stops:
        if stop not in stops:
            stops.append(stop)
    params.stop = stops


def configure_thinking_stop(params: Any) -> None:
    _append_stops(params, THINK_END_TAG, TOOL_CALL_START_TAG)
    params.include_stop_str_in_output = True


def configure_final_answer_stop(params: Any) -> None:
    _append_stops(params, THINK_END_TAG)
    params.include_stop_str_in_output = False


def _last_chunk_contains_boundary(
    last_chunk: object, boundary: ReasoningBoundary
) -> bool:
    try:
        output = last_chunk.outputs[0]
        return boundary.marker in (getattr(output, "text", "") or "")
    except (AttributeError, IndexError, TypeError):
        return False


def detect_reasoning_boundary(
    context: DtbRequestContext,
    text: str,
    token_ids: list[int],
    stop_reason: object = None,
) -> ReasoningBoundary | None:
    text_candidates: list[tuple[int, ReasoningBoundary]] = []
    token_candidates: list[tuple[int, ReasoningBoundary]] = []
    stop_candidate: ReasoningBoundary | None = None
    for boundary in ReasoningBoundary:
        text_index = text.find(boundary.marker)
        marker_ids = list(context.encode_prompt_suffix(boundary.marker))
        token_index = context._find_token_subsequence(token_ids, marker_ids)
        if text_index >= 0:
            text_candidates.append((text_index, boundary))
        if token_index is not None:
            token_candidates.append((token_index, boundary))
        if stop_reason == boundary.marker:
            stop_candidate = boundary
    if stop_candidate is not None:
        return stop_candidate
    if text_candidates:
        return min(text_candidates, key=lambda item: item[0])[1]
    if token_candidates:
        return min(token_candidates, key=lambda item: item[0])[1]
    return None


def align_stop_boundary_delta(
    context: DtbRequestContext,
    text: str,
    token_ids: list[int],
    boundary: ReasoningBoundary | None,
    stop_reason: object,
    *,
    prior_text: str = "",
    prior_token_ids: tuple[int, ...] | list[int] = (),
) -> tuple[str, list[int], bool]:
    """Drop speculative token suffixes hidden by a string stop boundary."""
    if boundary is None or stop_reason != boundary.marker:
        return text, token_ids, False
    cumulative_text = prior_text + text
    if not cumulative_text.endswith(boundary.marker):
        return text, token_ids, False
    prior_token_count = len(prior_token_ids)
    cumulative_token_ids = [*prior_token_ids, *token_ids]
    marker_ids = list(context.encode_prompt_suffix(boundary.marker))
    if not marker_ids:
        raise RuntimeError(
            f"reasoning stop boundary encoded to no token ids: {boundary.marker}"
        )
    marker_token_start = next((
        index
        for index in range(
            len(cumulative_token_ids) - len(marker_ids), -1, -1
        )
        if cumulative_token_ids[index:index + len(marker_ids)] == marker_ids
    ), None)
    if marker_token_start is None:
        raise RuntimeError(
            "reasoning stop boundary is visible in text but absent from "
            f"token ids: {boundary.marker}"
        )
    marker_token_end = marker_token_start + len(marker_ids) - prior_token_count
    if marker_token_end <= 0:
        raise RuntimeError(
            "reasoning stop boundary token ids ended outside the terminal "
            "delta; already-forwarded token ids cannot be retracted"
        )
    if marker_token_end >= len(token_ids):
        return text, token_ids, False
    return text, token_ids[:marker_token_end], True


async def emit_reasoning_boundary_once(
    context: DtbRequestContext,
    boundary: ReasoningBoundary = ReasoningBoundary.THINK_END,
) -> bool:
    """Emit one controller-selected boundary without copying request metrics."""
    if boundary is not ReasoningBoundary.THINK_END:
        raise ValueError("only </think> may be synthesized by the controller")
    if context.reasoning_boundary_emitted:
        return False
    last_chunk = context.last_chunk
    if last_chunk is None:
        return False
    if _last_chunk_contains_boundary(last_chunk, boundary):
        context.reasoning_boundary_emitted = True
        return False
    result = copy.copy(last_chunk)
    output = copy.copy(last_chunk.outputs[0])
    result.outputs = [output, *last_chunk.outputs[1:]]
    output.text = boundary.marker
    output.token_ids = list(context.encode_prompt_suffix(boundary.marker))
    output.finish_reason = None
    output.stop_reason = None
    result.finished = False
    await context.stream_output(
        result,
        phase="reasoning_boundary",
        child_request_id=f"{context.request_id}_reasoning_boundary",
        event="synthetic_boundary",
        controller_state="thinking",
        next_state="free_final_answer",
        reasoning_boundary=boundary,
    )
    context.reasoning_boundary_emitted = True
    return True
