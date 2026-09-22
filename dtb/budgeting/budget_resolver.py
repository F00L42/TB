"""Resolve fixed, scene/difficulty, and history inputs into one DTB budget."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from dtb.budgeting.budget_decision import (
    DecisionBranch,
    DtbBudgetDecision,
    ThinkingBudget,
)
from dtb.budgeting.difficulty.budget import compute_budget_from_difficulty
from dtb.budgeting.difficulty.estimator import estimate_difficulty
from dtb.budgeting.scene_router import classify_scene
from dtb.config.schema import (
    DtbAlgorithmConfig,
    DtbAlgorithmPolicy,
    DtbServiceConfig,
)
if TYPE_CHECKING:
    from dtb.observability.phase_recorder import DtbPhaseRecorder


_VECTOR_FIELDS = (
    "check_length", "min_think_budget", "num_thinking", "think_stride"
)
_DEFAULT_CONFIDENCE_THRESHOLD = 0.95
_DEFAULT_PERCENTILE_STEP = 0.75


@dataclass(frozen=True)
class InterventionThreshold:
    tokens: int
    source: str
    scene_info: dict[str, Any] | None = None


def _round_half_up_average(left: int, right: int) -> int:
    return (int(left) + int(right) + 1) // 2


def _optional_vector_value(
    values: dict[str, Any], field_name: str
) -> int | None:
    value = values.get(field_name)
    if value is None and field_name == "min_think_budget":
        value = values.get("min_think_tokens")
    if value is None:
        return None
    return max(1, int(value))


class DtbBudgetResolver:
    def __init__(
        self,
        service_config: DtbServiceConfig,
        policy: DtbAlgorithmPolicy | None = None,
    ) -> None:
        self.service_config = service_config
        self.policy = policy or DtbAlgorithmPolicy()

    def intervention_threshold(
        self, prompt_text: str, config: DtbAlgorithmConfig,
        phase_recorder: DtbPhaseRecorder | None = None,
        *, scene_info: dict[str, Any] | None = None,
    ) -> InterventionThreshold:
        """No difficulty/history/full decision on the streaming short path."""
        if config.fixed_check_length > 0:
            return InterventionThreshold(config.fixed_check_length, "fixed_check_length", scene_info)
        if config.fixed_budget_tokens > 0 or not config.enable_scene_budget:
            return InterventionThreshold(config.check_length, "runtime", scene_info)
        if scene_info is None:
            scene_info = self._match_scene(prompt_text, config, phase_recorder)
        if scene_info.get("scene_matched"):
            values = self.service_config.scenes.get(str(scene_info["scene"]), {})
            return InterventionThreshold(
                int(values.get("check_length", config.check_length)), "scene", scene_info)
        return InterventionThreshold(config.check_length, "runtime", scene_info)

    def _match_scene(
        self,
        prompt_text: str,
        config: DtbAlgorithmConfig,
        phase_recorder: DtbPhaseRecorder | None,
    ) -> dict[str, Any]:
        kwargs = {
            "min_score": config.scene_match_min_score,
            "min_margin": config.scene_match_min_margin,
            "scene_override": config.scene_override,
            "service_config": self.service_config,
        }
        if phase_recorder is None:
            scene_info = classify_scene(prompt_text, **kwargs)
        else:
            with phase_recorder.measure("decision.scene_router"):
                scene_info = classify_scene(prompt_text, **kwargs)
        scene_info["scene_matched"] = bool(
            config.enable_scene_budget and scene_info.get("scene_matched")
        )
        if not config.enable_scene_budget:
            scene_info["scene"] = "general"
            scene_info["reject_reason"] = "scene_budget_disabled"
        return scene_info

    @staticmethod
    def _clamp_budget(
        budget: ThinkingBudget,
        hard_limit: int,
        source_by_field: dict[str, Any],
    ) -> ThinkingBudget:
        clamped: dict[str, int] = {}
        for field_name, value in budget.to_dict().items():
            final_value = max(1, min(int(value), hard_limit))
            clamped[field_name] = final_value
            if final_value != value:
                previous = source_by_field.get(field_name)
                source_by_field[field_name] = {
                    "source": previous,
                    "clamped_from": value,
                    "hard_limit": hard_limit,
                    "final": final_value,
                }
        return ThinkingBudget(**clamped)

    def resolve(
        self,
        prompt_text: str,
        config: DtbAlgorithmConfig,
        phase_recorder: DtbPhaseRecorder | None,
        *,
        hard_token_limit: int | None = None,
        reference: str = "",
        decision_trace: dict[str, Any] | None = None,
        threshold: InterventionThreshold | None = None,
    ) -> DtbBudgetDecision:
        policy = self.policy
        scene_info = (
            threshold.scene_info if threshold is not None and threshold.scene_info is not None
            else self._match_scene(prompt_text, config, phase_recorder)
        )
        scene = str(scene_info.get("scene", "general"))
        scene_matched = bool(scene_info.get("scene_matched"))
        default_budget = config.default_budget
        source_by_field: dict[str, Any] = {}
        difficulty_info: dict[str, Any] | None = None
        history_info: dict[str, Any] | None = None
        scene_values: dict[str, Any] = {}
        branch_confidence_threshold = _DEFAULT_CONFIDENCE_THRESHOLD
        branch_confidence_source = "runtime_default"
        if config.percentile_step != _DEFAULT_PERCENTILE_STEP:
            effective_percentile_step = config.percentile_step
            percentile_source = "percentile_step_override"
        else:
            effective_percentile_step = config.history_stride_percentile
            percentile_source = "history_stride_percentile"

        if config.fixed_budget_tokens > 0:
            branch = DecisionBranch.FIXED
            fixed_check_length = (
                config.fixed_check_length
                if config.fixed_check_length > 0
                else default_budget.check_length
            )
            fixed_stride = (
                config.fixed_think_stride
                if config.fixed_think_stride > 0
                else default_budget.think_stride
            )
            check_length = min(fixed_check_length, config.fixed_budget_tokens)
            think_stride = min(fixed_stride, config.fixed_budget_tokens)
            if config.fixed_num_thinking > 0:
                num_thinking = config.fixed_num_thinking
            else:
                remaining = max(config.fixed_budget_tokens - check_length, 0)
                num_thinking = max(
                    1,
                    (remaining + max(think_stride, 1) - 1)
                    // max(think_stride, 1),
                )
            budget = ThinkingBudget(
                check_length=check_length,
                min_think_budget=(
                    config.fixed_min_think_tokens
                    if config.fixed_min_think_tokens > 0
                    else config.fixed_budget_tokens
                ),
                num_thinking=num_thinking,
                think_stride=think_stride,
            )
            source_by_field.update({
                field_name: {
                    "source": "fixed_budget",
                    "fixed_budget_tokens": config.fixed_budget_tokens,
                    "final": getattr(budget, field_name),
                }
                for field_name in _VECTOR_FIELDS
            })
        elif scene_matched:
            branch = DecisionBranch.SCENE_DIFFICULTY
            scene_values = dict(self.service_config.scenes.get(
                scene, self.service_config.scenes.get("general", {})
            ))
            scene_budget = ThinkingBudget.from_mapping(
                scene_values, default_budget
            )
            scene_threshold = scene_values.get(
                "confidence_threshold", scene_values.get("prob_th")
            )
            if isinstance(scene_threshold, (int, float)):
                branch_confidence_threshold = max(
                    0.0, min(1.0, float(scene_threshold))
                )
                branch_confidence_source = "scene"
            if config.enable_difficulty_estimator:
                difficulty_info, difficulty_values = self._difficulty_budget(
                    prompt_text,
                    reference,
                    scene,
                    scene_info,
                    config,
                    phase_recorder,
                )
                merged: dict[str, int] = {}
                raw_sources: dict[str, tuple[int | None, int | None]] = {}
                for field_name in _VECTOR_FIELDS:
                    scene_value = _optional_vector_value(
                        scene_values, field_name
                    )
                    difficulty_value = _optional_vector_value(
                        difficulty_values, field_name
                    )
                    raw_sources[field_name] = scene_value, difficulty_value
                    if scene_value is not None and difficulty_value is not None:
                        merged[field_name] = _round_half_up_average(
                            scene_value, difficulty_value
                        )
                    elif scene_value is not None:
                        merged[field_name] = scene_value
                    elif difficulty_value is not None:
                        merged[field_name] = difficulty_value
                    else:
                        merged[field_name] = getattr(default_budget, field_name)
                budget = ThinkingBudget(**merged)
                for field_name in _VECTOR_FIELDS:
                    scene_value, difficulty_value = raw_sources[field_name]
                    if scene_value is not None and difficulty_value is not None:
                        source = "scene+difficulty"
                    elif scene_value is not None:
                        source = "scene"
                    elif difficulty_value is not None:
                        source = "difficulty"
                    else:
                        source = "default"
                    source_by_field[field_name] = {
                        "source": source,
                        "scene": scene_value,
                        "difficulty": difficulty_value,
                        "rounding": "half_up",
                        "final": getattr(budget, field_name),
                    }
            else:
                budget = scene_budget
                source_by_field.update({
                    field_name: {
                        "source": "scene",
                        "final": getattr(budget, field_name),
                    }
                    for field_name in _VECTOR_FIELDS
                })
        else:
            branch = DecisionBranch.HISTORY
            if config.enable_history_budget:
                history_info = self._history_budget(
                    prompt_text,
                    scene,
                    config,
                    effective_percentile_step,
                    phase_recorder,
                )
                budget = ThinkingBudget.from_mapping(
                    history_info, default_budget
                )
                history_source = history_info.get("budget_mode", "history")
                history_confidence = history_info.get(
                    "confidence_threshold", history_info.get("prob_th")
                )
                if isinstance(history_confidence, (int, float)):
                    branch_confidence_threshold = max(
                        0.0, min(1.0, float(history_confidence))
                    )
                    branch_confidence_source = "history"
            else:
                budget = default_budget
                history_info = {
                    "budget_mode": "history_disabled",
                    "fallback_mode": "default_budget",
                    "reject_reason": scene_info.get("reject_reason"),
                }
                history_source = "default"
            source_by_field.update({
                field_name: {
                    "source": history_source,
                    "final": getattr(budget, field_name),
                }
                for field_name in _VECTOR_FIELDS
            })

        fixed_overrides = {
            "check_length": config.fixed_check_length,
            "min_think_budget": config.fixed_min_think_tokens,
            "num_thinking": config.fixed_num_thinking,
            "think_stride": config.fixed_think_stride,
        }
        fixed_source_names = {
            "check_length": "fixed_check_length",
            "min_think_budget": "fixed_min_think_tokens",
            "num_thinking": "fixed_num_thinking",
            "think_stride": "fixed_think_stride",
        }
        budget_values = budget.to_dict()
        for field_name, fixed_value in fixed_overrides.items():
            if fixed_value > 0:
                budget_values[field_name] = fixed_value
                source_by_field[field_name] = {
                    "source": fixed_source_names[field_name],
                    "final": fixed_value,
                }
        if threshold is not None:
            budget_values["check_length"] = threshold.tokens
            source_by_field["check_length"] = {
                "source": "intervention_threshold", "threshold_source": threshold.source,
                "final": threshold.tokens,
            }
        budget = ThinkingBudget(**budget_values)

        hard_limit_candidates = {
            "config.max_thinking_tokens": config.max_thinking_tokens,
        }
        if hard_token_limit is not None and hard_token_limit > 0:
            hard_limit_candidates["context/request"] = hard_token_limit
        if config.fixed_budget_tokens > 0:
            hard_limit_candidates["fixed_budget_tokens"] = (
                config.fixed_budget_tokens
            )
        elif scene_matched:
            scene_hard_limit = scene_values.get("max_think_tokens")
            if isinstance(scene_hard_limit, (int, float)):
                scene_hard_limit = int(scene_hard_limit)
                if scene_hard_limit > 0:
                    hard_limit_candidates["scene.max_think_tokens"] = (
                        scene_hard_limit
                    )
        hard_limit = max(1, min(hard_limit_candidates.values()))
        hard_limit_sources = [
            source for source, value in hard_limit_candidates.items()
            if value == hard_limit
        ]
        budget = self._clamp_budget(budget, hard_limit, source_by_field)
        if config.fixed_prob_th > 0:
            confidence_threshold = config.fixed_prob_th
            confidence_source = "fixed_prob_th"
        elif config.confidence_threshold != _DEFAULT_CONFIDENCE_THRESHOLD:
            confidence_threshold = config.confidence_threshold
            confidence_source = "confidence_threshold_override"
        else:
            confidence_threshold = branch_confidence_threshold
            confidence_source = branch_confidence_source
        source_by_field["confidence_threshold"] = {
            "source": confidence_source,
            "final": confidence_threshold,
        }
        source_by_field["percentile_step"] = {
            "source": percentile_source,
            "final": effective_percentile_step,
        }
        source_by_field["hard_think_token_limit"] = {
            "source": "+".join(hard_limit_sources),
            "candidates": hard_limit_candidates,
            "final": hard_limit,
        }
        if decision_trace is not None:
            decision_trace.update({
                "route": scene_info,
                "difficulty": difficulty_info,
                "history": history_info,
                "decision_modules": (
                    phase_recorder.module_snapshot()
                    if phase_recorder is not None else {}
                ),
            })
        return DtbBudgetDecision(
            scene=scene,
            scene_matched=scene_matched,
            decision_branch=branch,
            budget=budget,
            confidence_threshold=confidence_threshold,
            percentile_step=effective_percentile_step,
            source_by_field=source_by_field,
            policy_version=policy.version,
            hard_think_token_limit=hard_limit,
        )

    @staticmethod
    def _difficulty_budget(
        prompt_text: str,
        reference: str,
        scene: str,
        scene_info: dict[str, Any],
        config: DtbAlgorithmConfig,
        phase_recorder: DtbPhaseRecorder | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        def calculate() -> tuple[dict[str, Any], dict[str, Any]]:
            difficulty = estimate_difficulty(
                prompt_text,
                reference,
                scene=scene,
                scene_typical_tokens=int(scene_info.get("typical_tokens", 0)),
            )
            values = compute_budget_from_difficulty(
                float(difficulty.get("difficulty", 0.5)),
                base_check_length=config.difficulty_check_length_base,
                max_check_length=config.difficulty_check_length_max,
                base_min_think=config.difficulty_min_think_base,
                max_min_think=config.difficulty_min_think_max,
                base_stride=config.difficulty_stride_base,
                max_stride=config.difficulty_stride_max,
                base_num_thinking=config.difficulty_num_thinking_base,
                max_num_thinking=config.difficulty_num_thinking_max,
            )
            return difficulty, values

        if phase_recorder is None:
            return calculate()
        with phase_recorder.measure("decision.difficulty"):
            return calculate()

    @staticmethod
    def _history_budget(
        prompt_text: str,
        scene: str,
        config: DtbAlgorithmConfig,
        effective_percentile_step: float,
        phase_recorder: DtbPhaseRecorder | None,
    ) -> dict[str, Any]:
        from dtb.history.budget_policy import compute_history_budget

        values = {
            "history_budget_multiplier": config.history_budget_multiplier,
            "history_stride_percentile": effective_percentile_step,
            "history_warmup_count": config.history_warmup_count,
            "history_warmup_strategy": config.history_warmup_strategy,
            "new_task_unlimited": config.new_task_unlimited,
        }
        if phase_recorder is None:
            return compute_history_budget(prompt_text, scene=scene, config=values)
        with phase_recorder.measure("decision.history"):
            return compute_history_budget(prompt_text, scene=scene, config=values)
