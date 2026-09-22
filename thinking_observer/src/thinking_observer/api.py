"""Public extension API; no vLLM or DTB imports."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class Tokenizer(Protocol):
    def encode(self, text: str, *, add_special_tokens: bool = False) -> Sequence[int]: ...


@dataclass(frozen=True)
class BoundaryProtocol:
    """Single-token structural markers bound to the active native parsers."""

    think_end_id: int
    tool_start_id: int
    name: str = "qwen3"

    def find(self, ids: Sequence[int]) -> tuple[int, str] | None:
        for index, token in enumerate(ids):
            if token == self.think_end_id:
                return index, "think_end"
            if token == self.tool_start_id:
                return index, "tool_call"
        return None


@dataclass(frozen=True)
class ObserverContext:
    request_id: str
    model: str
    protocol: BoundaryProtocol
    completion_limit: int
    max_model_len: int
    prompt_tokens: int
    # Service-owned tokenizer. Extensions may encode, but must not mutate it.
    tokenizer: Tokenizer


@dataclass(frozen=True)
class ReasoningDelta:
    """Offsets are zero-based, end-exclusive reasoning TOKEN positions.

    Text is an incremental detokenizer fragment, not a per-token mapping.
    On a mixed boundary batch, text=None means no safe textual split exists.
    emitted_tokens counts parser-facing completion IDs accepted by the SDK;
    it is not an acknowledgement that the HTTP peer received those bytes.
    """

    token_ids: tuple[int, ...]
    text: str | None
    start: int
    end: int
    emitted_tokens: int
    boundary: str | None = None
    terminal: bool = False


class Continue(Enum):
    CONTINUE = "continue"


CONTINUE = Continue.CONTINUE


@dataclass(frozen=True)
class StopAndAnswer:
    reason: str
    detected_at: int
    repeat_start: int | None = None
    # Internal continuation prompt only; never inserted into public content.
    answer_cue: str | None = None


Decision = Continue | StopAndAnswer


@dataclass(frozen=True)
class ChildRecord:
    request_id: str
    phase: str
    prompt_tokens: int
    generated_tokens: int
    finish_reason: str | None
    wall_ms: float | None


@dataclass(frozen=True)
class Outcome:
    status: str
    finish_reason: str | None
    emitted_tokens: int
    reasoning_tokens: int
    detected_at: int | None
    repeat_start: int | None
    actual_handoff: int | None
    emitted_at_detection: int | None
    overshoot_tokens: int | None
    decision_reason: str | None
    detector_error: str | None
    skipped_reason: str | None
    callback_ms: float
    abort_drain_ms: float | None
    handoff_ms: float | None
    cue_tokens: int
    answer_limit: int | None
    children: tuple[ChildRecord, ...]


class DetectorState(Protocol):
    """One state per request; synchronous, bounded, ordered callbacks."""

    def observe(self, delta: ReasoningDelta) -> Decision: ...
    def close(self, outcome: Outcome) -> None: ...


class Detector(Protocol):
    """Service-owned factory. start MUST allocate a fresh state per request."""

    def start(self, context: ObserverContext) -> DetectorState: ...
