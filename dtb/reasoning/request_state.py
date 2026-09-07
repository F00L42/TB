"""Mutable state and the single model-work ledger for one DTB request."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dtb.budgeting.budget_decision import DtbBudgetDecision


class ReasoningState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ReasoningBoundary(str, Enum):
    THINK_END = "think_end"
    TOOL_CALL = "tool_call"

    @property
    def marker(self) -> str:
        return {
            ReasoningBoundary.THINK_END: "</think>",
            ReasoningBoundary.TOOL_CALL: "<tool_call>",
        }[self]


class FinalAnswerReason(str, Enum):
    CONFIDENCE_ACCEPTED = "confidence_accepted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    MODEL_THINK_END = "model_think_end"
    MODEL_TOOL_CALL = "model_tool_call"


@dataclass
class TrialObservation:
    full_text: str = ""
    all_token_ids: list[int] = field(default_factory=list)
    sample_logprobs: list[Any | None] = field(default_factory=list)
    sampled_token_ids: list[int] = field(default_factory=list)
    generated_tokens: int = 0
    finish_reason: Any = None
    stop_reason: Any = None
    last_output: Any = None
    output_kind: str = "DELTA"


@dataclass
class DtbRequestState:
    completion_token_limit: int
    reasoning_state: ReasoningState = ReasoningState.OPEN
    reasoning_boundary: ReasoningBoundary | None = None
    emitted_completion_tokens: int = 0
    thinking_tokens: int = 0
    hidden_probe_tokens: int = 0
    final_answer_tokens: int = 0
    child_generate_calls: int = 0
    completion_status: str = "running"
    probe_round: int = 0
    current_trial: TrialObservation | None = None
    last_confidence: float | None = None
    budget_decision: DtbBudgetDecision | None = None
    thinking_segments: int = 0
    final_answer_reason: FinalAnswerReason | None = None
    assistant_completion_epoch: int = 1
    final_terminal_count: int = 0
    unclassified_completion_tokens: int = 0
    intervention_telemetry: dict | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.completion_token_limit, int)
            or isinstance(self.completion_token_limit, bool)
            or self.completion_token_limit < 1
        ):
            raise ValueError("completion_token_limit must be a positive integer")
        if (
            not isinstance(self.emitted_completion_tokens, int)
            or isinstance(self.emitted_completion_tokens, bool)
            or self.emitted_completion_tokens < 0
            or self.emitted_completion_tokens > self.completion_token_limit
        ):
            raise ValueError(
                "emitted_completion_tokens must be an integer within the "
                "completion token limit"
            )
