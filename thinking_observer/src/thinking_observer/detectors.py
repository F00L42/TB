"""Wiring examples only. No repetition detection algorithm is implemented."""
from dataclasses import dataclass

from .api import CONTINUE, ObserverContext, Outcome, ReasoningDelta, StopAndAnswer


class _NoopState:
    def observe(self, delta: ReasoningDelta):
        return CONTINUE

    def close(self, outcome: Outcome):
        pass


class NoopDetector:
    def start(self, context: ObserverContext):
        return _NoopState()


@dataclass(frozen=True)
class TokenLimitDetector:
    """Deterministic integration fixture, not a quality/repetition policy."""

    limit: int = 128
    answer_cue: str | None = None

    def __post_init__(self):
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("limit must be a positive integer")

    def start(self, context: ObserverContext):
        return _TokenLimitState(self.limit, self.answer_cue)


@dataclass
class _TokenLimitState(_NoopState):
    limit: int
    answer_cue: str | None

    def observe(self, delta: ReasoningDelta):
        if delta.end >= self.limit:
            return StopAndAnswer("integration_token_limit", self.limit,
                                 answer_cue=self.answer_cue)
        return CONTINUE
