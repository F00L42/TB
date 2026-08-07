"""Think Budget algorithm configuration and immutable decision structures.

The algorithm layer intentionally has no dependency on DEER configuration
types.  vLLM routing and request-lifecycle objects live in
``deer_think_budget.context``; this module contains only values that can be
unit-tested without importing vLLM.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Mapping


TRIAL_ANSWER_LENGTH = 50
POLICY_VERSION = "think-budget-performance-v1"


class DecisionBranch(str, Enum):
    FIXED = "fixed"
    SCENE_DIFFICULTY = "scene_difficulty"
    HISTORY = "history"


class MissingConfidencePolicy(str, Enum):
    FAIL_OPEN = "fail_open"


class ConfidenceScope(str, Enum):
    CURRENT_TRIAL = "current_trial"


@dataclass(frozen=True)
class PerformancePolicy:
    """Reviewed algorithm deviations that are not runtime tuning knobs."""

    version: str = POLICY_VERSION
    trial_answer_length: int = TRIAL_ANSWER_LENGTH
    clean_context_only: bool = True
    fresh_final_answer: bool = True
    retain_answer_list: bool = False
    enable_soft_truncation: bool = False
    confidence_missing_input: MissingConfidencePolicy = (
        MissingConfidencePolicy.FAIL_OPEN
    )
    confidence_scope: ConfidenceScope = ConfidenceScope.CURRENT_TRIAL
    enable_safety_amplification: bool = False
    enable_safety_candidate_fallback: bool = False

    def __post_init__(self) -> None:
        if self.trial_answer_length != TRIAL_ANSWER_LENGTH:
            raise ValueError(
                f"trial_answer_length is fixed at {TRIAL_ANSWER_LENGTH}")
        invariant_flags = {
            "clean_context_only": self.clean_context_only,
            "fresh_final_answer": self.fresh_final_answer,
        }
        disabled_flags = {
            "retain_answer_list": self.retain_answer_list,
            "enable_soft_truncation": self.enable_soft_truncation,
            "enable_safety_amplification": self.enable_safety_amplification,
            "enable_safety_candidate_fallback":
                self.enable_safety_candidate_fallback,
        }
        if not all(invariant_flags.values()) or any(disabled_flags.values()):
            raise ValueError("PerformancePolicy invariants are not configurable")

    def telemetry(self) -> dict[str, Any]:
        values = asdict(self)
        values["confidence_missing_input"] = (
            self.confidence_missing_input.value)
        values["confidence_scope"] = self.confidence_scope.value
        return values


_POLICY_LOG_LOCK = Lock()
_POLICY_LOGGED = False


def log_performance_policy_once(policy: PerformancePolicy) -> None:
    global _POLICY_LOGGED
    with _POLICY_LOG_LOCK:
        if _POLICY_LOGGED:
            return
        print(
            "TB_POLICY "
            f"version={policy.version} values={policy.telemetry()}",
            flush=True,
        )
        _POLICY_LOGGED = True


@dataclass(frozen=True)
class BudgetVector:
    """The four generation-budget fields that may be merged arithmetically."""

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
        fallback: "BudgetVector",
    ) -> "BudgetVector":
        def get_int(primary: str, legacy: str | None, default: int) -> int:
            value = values.get(primary)
            if value is None and legacy is not None:
                value = values.get(legacy)
            return max(1, int(default if value is None else value))

        return cls(
            check_length=get_int(
                "check_length", None, fallback.check_length),
            min_think_budget=get_int(
                "min_think_budget", "min_think_tokens",
                fallback.min_think_budget),
            num_thinking=get_int(
                "num_thinking", None, fallback.num_thinking),
            think_stride=get_int(
                "think_stride", None, fallback.think_stride),
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class BudgetPlan:
    scene: str
    scene_matched: bool
    decision_branch: DecisionBranch
    budget: BudgetVector
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

    def telemetry(self) -> dict[str, Any]:
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


@dataclass
class AlgorithmConfig:
    """Server-side Think Budget configuration using algorithm terminology."""

    check_length: int = 200
    min_think_budget: int = 100
    num_thinking: int = 10
    think_stride: int = 100

    confidence_threshold: float = 0.95
    percentile_step: float = 0.75
    enable_value_min_confidence: bool = True
    value_min_bottom_k: int = 5

    enable_scene_budget: bool = True
    enable_difficulty_estimator: bool = False
    enable_history_budget: bool = False
    scene_override: str = ""
    scene_match_min_score: float = 3.0
    scene_match_min_margin: float = 1.0

    difficulty_check_length_base: int = 400
    difficulty_check_length_max: int = 6000
    difficulty_min_think_base: int = 100
    difficulty_min_think_max: int = 6000
    difficulty_stride_base: int = 400
    difficulty_stride_max: int = 6000
    difficulty_num_thinking_base: int = 10
    difficulty_num_thinking_max: int = 20

    history_budget_multiplier: float = 1.2
    history_stride_percentile: float = 0.75
    history_warmup_count: int = 3
    history_warmup_strategy: str = "moderate"
    new_task_unlimited: bool = True

    fixed_budget_tokens: int = 0
    fixed_num_thinking: int = 0
    fixed_think_stride: int = 0
    fixed_check_length: int = 0
    fixed_prob_th: float = 0.0
    fixed_min_think_tokens: int = 0

    enable_safety_gate: bool = False
    baseline_time_limit: float = 90.0
    max_internal_calls: int = 20
    max_thinking_tokens: int = 65536
    final_answer_max_tokens: int = 2048
    budget_enabled: bool = True
    emit_phase_metrics: bool = True
    verbose_stage_logging: bool = False

    def __post_init__(self) -> None:
        positive_fields = (
            "check_length",
            "min_think_budget",
            "num_thinking",
            "think_stride",
            "value_min_bottom_k",
            "difficulty_check_length_base",
            "difficulty_check_length_max",
            "difficulty_min_think_base",
            "difficulty_min_think_max",
            "difficulty_stride_base",
            "difficulty_stride_max",
            "difficulty_num_thinking_base",
            "difficulty_num_thinking_max",
            "history_warmup_count",
            "max_thinking_tokens",
            "final_answer_max_tokens",
        )
        for name in positive_fields:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_internal_calls < 0:
            raise ValueError("max_internal_calls must be non-negative")
        if 0 < self.max_internal_calls < 2:
            raise ValueError(
                "max_internal_calls must be 0 or at least 2")
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be in [0, 1]")
        if not 0 < self.percentile_step <= 1:
            raise ValueError("percentile_step must be in (0, 1]")
        if not 0 < self.history_stride_percentile <= 1:
            raise ValueError(
                "history_stride_percentile must be in (0, 1]")
        if self.history_budget_multiplier <= 0:
            raise ValueError("history_budget_multiplier must be positive")
        if self.scene_match_min_score < 0:
            raise ValueError("scene_match_min_score must be non-negative")
        if self.scene_match_min_margin < 0:
            raise ValueError("scene_match_min_margin must be non-negative")
        if self.baseline_time_limit < 0:
            raise ValueError("baseline_time_limit must be non-negative")
        for base_name, maximum_name in (
            ("difficulty_check_length_base",
             "difficulty_check_length_max"),
            ("difficulty_min_think_base",
             "difficulty_min_think_max"),
            ("difficulty_stride_base", "difficulty_stride_max"),
            ("difficulty_num_thinking_base",
             "difficulty_num_thinking_max"),
        ):
            if getattr(self, base_name) > getattr(self, maximum_name):
                raise ValueError(
                    f"{base_name} must not exceed {maximum_name}")
        for name in (
            "fixed_budget_tokens",
            "fixed_num_thinking",
            "fixed_think_stride",
            "fixed_check_length",
            "fixed_min_think_tokens",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not 0 <= self.fixed_prob_th <= 1:
            raise ValueError("fixed_prob_th must be in [0, 1]")

    @property
    def default_budget(self) -> BudgetVector:
        return BudgetVector(
            check_length=self.check_length,
            min_think_budget=self.min_think_budget,
            num_thinking=self.num_thinking,
            think_stride=self.think_stride,
        )


# Compatibility for integrations that imported the old public name.  This is
# an alias, not an inherited DEER configuration type.
BudgetConfig = AlgorithmConfig
