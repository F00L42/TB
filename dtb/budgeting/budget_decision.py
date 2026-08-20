"""Immutable values produced by DTB budget resolution."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class DecisionBranch(str, Enum):
    FIXED = "fixed"
    SCENE_DIFFICULTY = "scene_difficulty"
    HISTORY = "history"


@dataclass(frozen=True)
class ThinkingBudget:
    """The generation limits consumed by the DTB state machine."""

    check_length: int
    min_think_budget: int
    num_thinking: int
    think_stride: int

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        fallback: "ThinkingBudget",
    ) -> "ThinkingBudget":
        def get_int(primary: str, legacy: str | None, default: int) -> int:
            value = values.get(primary)
            if value is None and legacy is not None:
                value = values.get(legacy)
            return max(1, int(default if value is None else value))

        return cls(
            check_length=get_int("check_length", None, fallback.check_length),
            min_think_budget=get_int(
                "min_think_budget", "min_think_tokens",
                fallback.min_think_budget,
            ),
            num_thinking=get_int("num_thinking", None, fallback.num_thinking),
            think_stride=get_int("think_stride", None, fallback.think_stride),
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class DtbBudgetDecision:
    """A resolved, auditable budget decision for one external request."""

    scene: str
    scene_matched: bool
    decision_branch: DecisionBranch
    budget: ThinkingBudget
    confidence_threshold: float
    percentile_step: float
    source_by_field: Mapping[str, Any]
    policy_version: str
    hard_think_token_limit: int

    def __post_init__(self) -> None:
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be in [0, 1]")
        if not 0 < self.percentile_step <= 1:
            raise ValueError("percentile_step must be in (0, 1]")
        if self.hard_think_token_limit < 1:
            raise ValueError("hard_think_token_limit must be positive")

    def to_telemetry(self) -> dict[str, Any]:
        return {
            "scene": self.scene,
            "scene_matched": self.scene_matched,
            "decision_branch": self.decision_branch.value,
            "budget": self.budget.to_dict(),
            "confidence_threshold": self.confidence_threshold,
            "percentile_step": self.percentile_step,
            "source_by_field": dict(self.source_by_field),
            "policy_version": self.policy_version,
            "hard_think_token_limit": self.hard_think_token_limit,
        }
