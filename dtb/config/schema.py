"""Validated DTB server configuration and fixed algorithm invariants."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from dtb.budgeting.budget_decision import ThinkingBudget


TRIAL_ANSWER_LENGTH = 50
POLICY_VERSION = "dtb-algorithm-v4-trial-close-exp1"


class MissingConfidencePolicy(str, Enum):
    FAIL_OPEN = "fail_open"


class ConfidenceScope(str, Enum):
    CURRENT_TRIAL = "current_trial"


class DtbTelemetryMode(str, Enum):
    OFF = "off"
    PHASE = "phase"
    STREAM_AUDIT = "stream_audit"


class ReasoningProtocolAdapter(str, Enum):
    """Model/parser protocol adapters supported by the DTB controller."""

    QWEN3 = "qwen3"


@dataclass(frozen=True)
class DtbReasoningProtocolConfig:
    """Startup-only structural token contract for one model deployment."""

    adapter: ReasoningProtocolAdapter
    think_end_token_id: int
    tool_call_begin_token_id: int

    def __post_init__(self) -> None:
        for name in ("think_end_token_id", "tool_call_begin_token_id"):
            value = getattr(self, name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    f"reasoning_protocol.{name} must be a non-negative integer"
                )
        if self.think_end_token_id == self.tool_call_begin_token_id:
            raise ValueError(
                "reasoning protocol boundary token ids must be distinct"
            )


@dataclass(frozen=True)
class DtbObservabilityConfig:
    """Process-level telemetry configuration; requests cannot override it."""

    mode: DtbTelemetryMode = DtbTelemetryMode.OFF
    queue_capacity: int = 2048
    stream_request_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.queue_capacity < 1:
            raise ValueError("observability.queue_capacity must be positive")
        if (
            self.mode is DtbTelemetryMode.STREAM_AUDIT
            and not self.stream_request_ids
        ):
            raise ValueError(
                "stream_audit mode requires an explicit stream_request_ids allowlist"
            )


@dataclass(frozen=True)
class DtbHistoryStoreConfig:
    """Startup-only persistence settings for the optional history module."""

    path: str
    max_entries: int = 5000
    max_age_days: int = 7
    queue_capacity: int = 1024

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("history_store.path must not be empty")
        for name in ("max_entries", "max_age_days", "queue_capacity"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"history_store.{name} must be positive")


@dataclass(frozen=True)
class DtbServiceConfig:
    """Immutable startup snapshot of YAML configuration."""

    algorithm: "DtbAlgorithmConfig"
    reasoning_protocol: DtbReasoningProtocolConfig
    observability: DtbObservabilityConfig
    history_store: DtbHistoryStoreConfig
    scenes: Mapping[str, Mapping[str, Any]]
    scene_priority: tuple[str, ...]
    difficulty_tiers: Mapping[str, Mapping[str, Any]]
    history_new_task: Mapping[str, Any]
    history_warm_start: Mapping[str, Any]
    history_hot_start: Mapping[str, Any]


@dataclass(frozen=True)
class DtbAlgorithmPolicy:
    """Code-owned invariants; these are deliberately not YAML knobs."""

    version: str = POLICY_VERSION
    trial_answer_length: int = TRIAL_ANSWER_LENGTH
    confidence_missing_input: MissingConfidencePolicy = (
        MissingConfidencePolicy.FAIL_OPEN
    )
    confidence_scope: ConfidenceScope = ConfidenceScope.CURRENT_TRIAL

    def __post_init__(self) -> None:
        if self.version != POLICY_VERSION:
            raise ValueError("DTB algorithm policy version is code-owned")
        if self.trial_answer_length != TRIAL_ANSWER_LENGTH:
            raise ValueError("DTB trial answer length is code-owned")
        if self.confidence_missing_input is not MissingConfidencePolicy.FAIL_OPEN:
            raise ValueError("DTB missing-confidence policy is code-owned")
        if self.confidence_scope is not ConfidenceScope.CURRENT_TRIAL:
            raise ValueError("DTB confidence scope is code-owned")


@dataclass(frozen=True)
class DtbAlgorithmConfig:
    """Server-side DTB algorithm configuration loaded once at startup."""

    check_length: int = 200
    min_think_budget: int = 100
    num_thinking: int = 10
    think_stride: int = 100
    confidence_threshold: float = 0.95
    percentile_step: float = 0.75
    enable_value_min_confidence: bool = False
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
    budget_enabled: bool = True

    def __post_init__(self) -> None:
        positive_fields = (
            "check_length", "min_think_budget", "num_thinking",
            "think_stride", "value_min_bottom_k",
            "difficulty_check_length_base", "difficulty_check_length_max",
            "difficulty_min_think_base", "difficulty_min_think_max",
            "difficulty_stride_base", "difficulty_stride_max",
            "difficulty_num_thinking_base", "difficulty_num_thinking_max",
            "history_warmup_count", "max_thinking_tokens",
        )
        for name in positive_fields:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_internal_calls < 0 or 0 < self.max_internal_calls < 2:
            raise ValueError("max_internal_calls must be 0 or at least 2")
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be in [0, 1]")
        if not 0 < self.percentile_step <= 1:
            raise ValueError("percentile_step must be in (0, 1]")
        if not 0 < self.history_stride_percentile <= 1:
            raise ValueError("history_stride_percentile must be in (0, 1]")
        if self.history_budget_multiplier <= 0:
            raise ValueError("history_budget_multiplier must be positive")
        if self.scene_match_min_score < 0 or self.scene_match_min_margin < 0:
            raise ValueError("scene match thresholds must be non-negative")
        if self.baseline_time_limit < 0:
            raise ValueError("baseline_time_limit must be non-negative")
        for base_name, maximum_name in (
            ("difficulty_check_length_base", "difficulty_check_length_max"),
            ("difficulty_min_think_base", "difficulty_min_think_max"),
            ("difficulty_stride_base", "difficulty_stride_max"),
            ("difficulty_num_thinking_base", "difficulty_num_thinking_max"),
        ):
            if getattr(self, base_name) > getattr(self, maximum_name):
                raise ValueError(f"{base_name} must not exceed {maximum_name}")
        for name in (
            "fixed_budget_tokens", "fixed_num_thinking", "fixed_think_stride",
            "fixed_check_length", "fixed_min_think_tokens",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not 0 <= self.fixed_prob_th <= 1:
            raise ValueError("fixed_prob_th must be in [0, 1]")
        if (self.fixed_budget_tokens > 0 and self.fixed_check_length > 0
                and self.fixed_check_length > self.fixed_budget_tokens):
            raise ValueError("fixed_check_length must not exceed fixed_budget_tokens")

    @property
    def default_budget(self) -> ThinkingBudget:
        return ThinkingBudget(
            check_length=self.check_length,
            min_think_budget=self.min_think_budget,
            num_thinking=self.num_thinking,
            think_stride=self.think_stride,
        )
