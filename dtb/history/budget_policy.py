"""Derive a scene budget from unlabeled online token history."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from dtb.config.loader import load_dtb_service_config
from dtb.history.store import (
    get_cv_for_scene,
    get_scene_stats,
)


def _num_thinking(
    budget: float,
    check_length: int,
    *,
    minimum: int = 3,
    maximum: int = 20,
) -> int:
    remaining = max(0.0, float(budget) - check_length)
    return max(
        minimum,
        min(maximum, int(math.ceil(remaining / max(check_length, 1)))),
    )


def compute_history_budget(
    question: str,
    *,
    scene: str = "general",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Use token distributions only; online correctness is not assumed."""
    del question  # Content never enters the history key or persistence path.
    cfg = config or {}
    service_config = load_dtb_service_config()
    new_config = service_config.history_new_task
    warm_config = service_config.history_warm_start
    hot_config = service_config.history_hot_start
    base = service_config.scenes.get(
        scene, service_config.scenes.get("general", {})
    )

    unlimited = bool(cfg.get(
        "new_task_unlimited", new_config.get("unlimited", True)))
    unlimited_placeholder = int(
        new_config.get("unlimited_budget_placeholder", 99999))
    multiplier = float(cfg.get(
        "history_budget_multiplier",
        warm_config.get("multiplier", 1.2),
    ))
    warmup_count = int(cfg.get(
        "history_warmup_count",
        warm_config.get("warmup_count", 3),
    ))
    warmup_strategy = str(cfg.get(
        "history_warmup_strategy",
        warm_config.get("strategy", "moderate"),
    ))
    percentile = float(cfg.get(
        "history_stride_percentile",
        hot_config.get("stride_percentile", 0.75),
    ))

    stats = get_scene_stats(scene)
    count = int(stats.get("count", 0))
    average = float(stats.get("avg_tokens", 0.0))
    p75 = float(stats.get("p75_tokens", 0.0))
    snapshot_id = f"{scene}:{count}:{round(p75, 1)}"
    common = {
        "history_count": count,
        "history_avg_tokens": round(average, 1),
        "history_p75_tokens": round(p75, 1),
        "quality_count": int(stats.get("quality_count", 0)),
        "history_snapshot_id": snapshot_id,
        "history_signal": "thinking_tokens_without_online_quality",
    }

    if count == 0:
        budget = (
            unlimited_placeholder
            if unlimited
            else int(base.get("typical_tokens", 3000))
        )
        return {
            **common,
            "history_think_token_limit": budget,
            "check_length": int(base.get("check_length", 500)),
            "think_stride": int(base.get("think_stride", 200)),
            "num_thinking": (
                99 if unlimited else int(base.get("num_thinking", 8))
            ),
            "min_think_tokens": int(base.get("min_think_tokens", 200)),
            "check_answer_len": int(base.get("check_answer_len", 3000)),
            "prob_th": float(base.get("prob_th", 0.93)),
            "budget_mode": (
                "first_full_observation" if unlimited else "cold_start"
            ),
            "is_new_scene": True,
        }

    if count <= warmup_count:
        coefficient_of_variation = get_cv_for_scene(scene)
        variance_boosts = warm_config.get("high_variance_boost", {})
        effective_multiplier = multiplier
        if coefficient_of_variation > 0.5:
            effective_multiplier *= float(
                variance_boosts.get(warmup_strategy, 1.1))
        elif coefficient_of_variation < 0.3 and warmup_strategy == "aggressive":
            effective_multiplier = max(1.0, multiplier * 0.9)
        budget = int(max(
            average * effective_multiplier,
            float(stats.get("max_tokens", average)),
        ))
        check_length = min(
            budget, int(base.get("check_length", 500)))
        rounds = _num_thinking(budget, check_length)
        stride = max(
            50,
            int(max(0, budget - check_length) / max(rounds, 1)),
        )
        return {
            **common,
            "history_think_token_limit": budget,
            "check_length": check_length,
            "think_stride": stride,
            "num_thinking": rounds,
            "min_think_tokens": max(
                int(base.get("min_think_tokens", 100)),
                int(budget * 0.3),
            ),
            "check_answer_len": int(base.get("check_answer_len", 3000)),
            "prob_th": float(base.get("prob_th", 0.93)),
            "budget_mode": "warm_start",
            "is_new_scene": False,
            "warmup_cv": round(coefficient_of_variation, 3),
            "warmup_multiplier_used": round(effective_multiplier, 3),
        }

    effective_tokens = int(p75 if p75 > 0 else average)
    check_length = min(
        max(1, int(effective_tokens * 0.3)),
        int(base.get("check_length", 500)),
    )
    rounds = _num_thinking(effective_tokens, check_length)
    percentile_tokens = float(
        stats.get(f"p{int(percentile * 100)}_tokens", effective_tokens))
    stride = max(
        50,
        int(max(effective_tokens, percentile_tokens) / max(rounds, 1)),
    )
    return {
        **common,
        "history_think_token_limit": effective_tokens,
        "check_length": check_length,
        "think_stride": stride,
        "num_thinking": rounds,
        "min_think_tokens": max(
            int(base.get("min_think_tokens", 100)),
            int(effective_tokens * 0.2),
        ),
        "check_answer_len": int(base.get("check_answer_len", 3000)),
        "prob_th": float(base.get("prob_th", 0.93)),
        "budget_mode": "hot_start",
        "is_new_scene": False,
    }
