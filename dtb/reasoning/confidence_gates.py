"""Current-trial confidence evaluation with an explicit fail-open chain."""
from __future__ import annotations

import math
from dataclasses import dataclass

from dtb.reasoning.confidence import (
    cal_confidence,
    cal_perplexity,
    extract_token_logprobs_and_texts,
)
from dtb.reasoning.request_state import TrialObservation
from dtb.reasoning.token_filter import (
    mean_bottom_k_logprobs,
    extract_value_logprobs,
)


@dataclass(frozen=True)
class ConfidenceAssessment:
    score: float | None
    average_confidence: float
    perplexity: float | None
    value_min_raw: float | None
    method: str
    available: bool
    pair_count: int
    value_pair_count: int | None


def assess_trial_confidence(
    observation: TrialObservation,
    *,
    enable_value_min_confidence: bool,
    value_min_bottom_k: int,
    collect_diagnostics: bool = True,
) -> ConfidenceAssessment:
    """Evaluate the current trial; optionally omit decision-independent work.

    Direct callers retain full diagnostics by default. The production caller
    requests them only for phase telemetry; uncomputed values are None, not zero.
    """
    logprobs, tokens = extract_token_logprobs_and_texts(
        observation.sample_logprobs,
        observation.sampled_token_ids,
    )
    pair_count = sum(logprob is not None for logprob in logprobs)
    average = cal_confidence(logprobs)
    perplexity = cal_perplexity(logprobs) if collect_diagnostics else None
    if not pair_count:
        return ConfidenceAssessment(
            score=None,
            average_confidence=0.0,
            perplexity=perplexity,
            value_min_raw=None,
            method="confidence_unavailable_fail_open",
            available=False,
            pair_count=0,
            value_pair_count=0,
        )

    if not enable_value_min_confidence and not collect_diagnostics:
        return ConfidenceAssessment(
            score=average,
            average_confidence=average,
            perplexity=None,
            value_min_raw=None,
            method="average_fallback",
            available=True,
            pair_count=pair_count,
            value_pair_count=None,
        )

    value_logprobs = extract_value_logprobs(logprobs, tokens)
    raw = (
        mean_bottom_k_logprobs(value_logprobs, value_min_bottom_k)
        if value_logprobs else None
    )
    if (
        enable_value_min_confidence
        and len(value_logprobs) >= max(1, value_min_bottom_k)
    ):
        assert raw is not None
        return ConfidenceAssessment(
            score=min(math.exp(raw), 1.0),
            average_confidence=average,
            perplexity=perplexity,
            value_min_raw=raw,
            method="value_min",
            available=True,
            pair_count=pair_count,
            value_pair_count=len(value_logprobs),
        )

    # Missing sampled pairs, structural-only output, or too few Value tokens
    # degrades to the synchronous reference average rather than rejecting.
    return ConfidenceAssessment(
        score=average,
        average_confidence=average,
        perplexity=perplexity,
        # Keep the counterfactual raw value observable even when Value-Min is
        # disabled or has too few value pairs to control the decision.
        value_min_raw=raw,
        method="average_fallback",
        available=True,
        pair_count=pair_count,
        value_pair_count=len(value_logprobs),
    )
