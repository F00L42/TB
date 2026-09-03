"""Preserve parser-facing text/token continuity at DTB reasoning boundaries."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Sequence

from dtb.config.schema import (
    DtbReasoningProtocolConfig,
    ReasoningProtocolAdapter,
)
from dtb.integration.vllm.request_adapter import merge_stop_token_ids
from dtb.reasoning.request_context import DtbRequestContext
from dtb.reasoning.request_state import ReasoningBoundary


THINK_END_TAG = "</think>"
TOOL_CALL_START_TAG = "<tool_call>"


@dataclass(frozen=True)
class BoundaryTokenSpec:
    boundary: ReasoningBoundary
    token_id: int
    marker: str | None = None


@dataclass(frozen=True)
class ReasoningBoundaryProtocol:
    """Request-local protocol after config/parser/tokenizer binding succeeds."""

    adapter: ReasoningProtocolAdapter
    natural_boundaries: tuple[BoundaryTokenSpec, ...]
    forced_close_token_ids: tuple[int, ...]
    forced_close_text: str = ""
    tokenizer_class: str = "unknown"
    reasoning_parser_class: str = "unknown"
    response_parser_class: str = "unknown"
    tool_parser_class: str = "unknown"

    def __post_init__(self) -> None:
        if not self.natural_boundaries:
            raise ValueError("reasoning protocol requires natural boundaries")
        ids = [spec.token_id for spec in self.natural_boundaries]
        roles = [spec.boundary for spec in self.natural_boundaries]
        if len(ids) != len(set(ids)) or len(roles) != len(set(roles)):
            raise ValueError("reasoning protocol boundaries must be unique")
        if len(self.forced_close_token_ids) != 1:
            raise ValueError("forced reasoning close must be one token")
        if self.forced_close_token_ids != (
            self.spec_for(ReasoningBoundary.THINK_END).token_id,
        ):
            raise ValueError("forced close token must be the think-end boundary")

    @property
    def natural_boundary_token_ids(self) -> tuple[int, ...]:
        return tuple(spec.token_id for spec in self.natural_boundaries)

    def spec_for(self, boundary: ReasoningBoundary) -> BoundaryTokenSpec:
        for spec in self.natural_boundaries:
            if spec.boundary is boundary:
                return spec
        raise KeyError(f"reasoning boundary is not configured: {boundary.value}")

    def classify_stop(
        self,
        stop_reason: object,
        delta_token_ids: Sequence[int],
    ) -> ReasoningBoundary | None:
        if not isinstance(stop_reason, int) or isinstance(stop_reason, bool):
            return None
        for spec in self.natural_boundaries:
            if stop_reason != spec.token_id:
                continue
            if not delta_token_ids or delta_token_ids[-1] != spec.token_id:
                raise RuntimeError(
                    "reasoning boundary stop token is not the final source "
                    f"token: role={spec.boundary.value} id={spec.token_id}"
                )
            return spec.boundary
        return None


def _parser_name(parser: object) -> str:
    return parser.__class__.__name__ if parser is not None else "none"


def _qualified_type_name(value: object) -> str:
    if value is None:
        return "none"
    cls = value.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


def bind_reasoning_boundary_protocol(
    config: DtbReasoningProtocolConfig,
    tokenizer: Any,
    reasoning_parser: Any,
    response_parser: Any,
    *,
    validate_tokenizer_vocab: bool = True,
) -> ReasoningBoundaryProtocol:
    """Bind configured IDs to the active Qwen parser contract, or fail closed."""
    if config.adapter is not ReasoningProtocolAdapter.QWEN3:
        raise ValueError(
            f"unsupported reasoning protocol adapter: {config.adapter.value}"
        )
    if _parser_name(reasoning_parser) != "Qwen3ReasoningParser":
        raise ValueError(
            "qwen3 DTB protocol requires Qwen3ReasoningParser, got "
            f"{_parser_name(reasoning_parser)}"
        )
    unified_reasoner = getattr(response_parser, "reasoning_parser", None)
    tool_parser = getattr(response_parser, "tool_parser", None)
    if _parser_name(unified_reasoner) != "Qwen3ReasoningParser":
        raise ValueError(
            "qwen3 DTB protocol requires the unified Qwen3ReasoningParser, got "
            f"{_parser_name(unified_reasoner)}"
        )
    if _parser_name(tool_parser) != "Qwen3CoderToolParser":
        raise ValueError(
            "qwen3 DTB protocol requires Qwen3CoderToolParser, got "
            f"{_parser_name(tool_parser)}"
        )

    if validate_tokenizer_vocab:
        try:
            vocab_ids = set(tokenizer.get_vocab().values())
        except Exception as exc:
            raise ValueError(
                "reasoning protocol requires the active tokenizer vocabulary"
            ) from exc
        configured_ids = {
            config.think_end_token_id,
            config.tool_call_begin_token_id,
        }
        missing_ids = configured_ids - vocab_ids
        if missing_ids:
            raise ValueError(
                "configured reasoning boundary IDs are absent from the active "
                f"tokenizer vocabulary: {sorted(missing_ids)}"
            )

    for name, parser in (
        ("request", reasoning_parser),
        ("unified", unified_reasoner),
    ):
        if getattr(parser, "end_token_id", None) != config.think_end_token_id:
            raise ValueError(
                f"{name} Qwen reasoning end token does not match config: "
                f"{getattr(parser, 'end_token_id', None)} != "
                f"{config.think_end_token_id}"
            )
        if not parser.is_reasoning_end_streaming(
            [], [config.think_end_token_id]
        ):
            raise ValueError(
                f"{name} Qwen parser rejected configured think-end token"
            )
        if not parser.is_reasoning_end_streaming(
            [], [config.tool_call_begin_token_id]
        ):
            raise ValueError(
                f"{name} Qwen parser rejected configured tool-start token"
            )
    if (
        getattr(tool_parser, "tool_call_start_token_id", None)
        != config.tool_call_begin_token_id
    ):
        raise ValueError(
            "Qwen tool parser start token does not match config: "
            f"{getattr(tool_parser, 'tool_call_start_token_id', None)} != "
            f"{config.tool_call_begin_token_id}"
        )

    return ReasoningBoundaryProtocol(
        adapter=config.adapter,
        natural_boundaries=(
            BoundaryTokenSpec(
                ReasoningBoundary.THINK_END,
                config.think_end_token_id,
                THINK_END_TAG,
            ),
            BoundaryTokenSpec(
                ReasoningBoundary.TOOL_CALL,
                config.tool_call_begin_token_id,
                TOOL_CALL_START_TAG,
            ),
        ),
        forced_close_token_ids=(config.think_end_token_id,),
        forced_close_text="",
        tokenizer_class=_qualified_type_name(tokenizer),
        reasoning_parser_class=_qualified_type_name(reasoning_parser),
        response_parser_class=_qualified_type_name(response_parser),
        tool_parser_class=_qualified_type_name(tool_parser),
    )


def _append_stops(params: Any, *required_stops: str) -> None:
    current_stop = getattr(params, "stop", None)
    stops = [current_stop] if isinstance(current_stop, str) else list(
        current_stop or []
    )
    for stop in required_stops:
        if stop not in stops:
            stops.append(stop)
    params.stop = stops


def configure_thinking_stop(
    params: Any,
    context: DtbRequestContext,
) -> None:
    if getattr(context.request, "stream", False):
        protocol = context.require_reasoning_protocol()
        merge_stop_token_ids(params, protocol.natural_boundary_token_ids)
        params.min_tokens = 0
    else:
        _append_stops(params, THINK_END_TAG, TOOL_CALL_START_TAG)
        params.include_stop_str_in_output = True
    context.thinking_stop_token_ids = tuple(
        getattr(params, "stop_token_ids", None) or ()
    )
    current_stop = getattr(params, "stop", None)
    context.thinking_stop_strings = tuple(
        [current_stop] if isinstance(current_stop, str) else current_stop or ()
    )
    context.thinking_include_stop_str_in_output = bool(
        getattr(params, "include_stop_str_in_output", False)
    )


def _last_chunk_contains_boundary(
    context: DtbRequestContext,
    last_chunk: object,
    boundary: ReasoningBoundary,
) -> bool:
    try:
        output = last_chunk.outputs[0]
        if getattr(context.request, "stream", False):
            boundary_id = context.require_reasoning_protocol().spec_for(
                boundary
            ).token_id
            return boundary_id in list(getattr(output, "token_ids", None) or ())
        return boundary.marker in (getattr(output, "text", "") or "")
    except (AttributeError, IndexError, TypeError):
        return False


def detect_reasoning_boundary(
    context: DtbRequestContext,
    text: str,
    token_ids: list[int],
    stop_reason: object = None,
) -> ReasoningBoundary | None:
    if getattr(context.request, "stream", False):
        protocol = context.require_reasoning_protocol()
        boundary = protocol.classify_stop(stop_reason, token_ids)
        if boundary is not None:
            return boundary
        unexpected = set(token_ids) & set(protocol.natural_boundary_token_ids)
        if unexpected:
            raise RuntimeError(
                "streaming reasoning boundary token arrived without matching "
                f"integer stop_reason: {sorted(unexpected)}"
            )
        return None

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
    if getattr(context.request, "stream", False):
        if boundary is None:
            return text, token_ids, False
        boundary_id = context.require_reasoning_protocol().spec_for(
            boundary
        ).token_id
        if stop_reason != boundary_id or not token_ids:
            raise RuntimeError(
                "streaming token boundary lost its integer stop reason/token"
            )
        if token_ids[-1] != boundary_id:
            raise RuntimeError(
                "streaming token boundary has a speculative suffix"
            )
        return text, token_ids, False

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
    if _last_chunk_contains_boundary(context, last_chunk, boundary):
        context.reasoning_boundary_emitted = True
        return False
    result = copy.copy(last_chunk)
    output = copy.copy(last_chunk.outputs[0])
    result.outputs = [output, *last_chunk.outputs[1:]]
    if getattr(context.request, "stream", False):
        protocol = context.require_reasoning_protocol()
        output.text = protocol.forced_close_text
        output.token_ids = list(protocol.forced_close_token_ids)
    else:
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
