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
    cal_value_min_confidence,
    extract_value_logprobs,
)


@dataclass(frozen=True)
class ConfidenceAssessment:
    score: float | None
    average_confidence: float
    perplexity: float
    value_min_raw: float | None
    method: str
    available: bool
    pair_count: int
    value_pair_count: int


def assess_trial_confidence(
    observation: TrialObservation,
    *,
    enable_value_min_confidence: bool,
    value_min_bottom_k: int,
) -> ConfidenceAssessment:
    """Evaluate only ``observation``; no prior probe or thought can leak in."""
    logprobs, tokens = extract_token_logprobs_and_texts(
        observation.sample_logprobs,
        observation.sampled_token_ids,
    )
    valid_pairs = [
        (logprob, token)
        for logprob, token in zip(logprobs, tokens)
        if logprob is not None
    ]
    average = cal_confidence(logprobs)
    perplexity = cal_perplexity(logprobs)
    if not valid_pairs:
        return ConfidenceAssessment(
            score=None,
            average_confidence=0.0,
            perplexity=0.0,
            value_min_raw=None,
            method="confidence_unavailable_fail_open",
            available=False,
            pair_count=0,
            value_pair_count=0,
        )

    value_logprobs = extract_value_logprobs(logprobs, tokens)
    raw = (
        cal_value_min_confidence(
            logprobs, tokens, bottom_k=value_min_bottom_k)
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
            pair_count=len(valid_pairs),
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
        pair_count=len(valid_pairs),
        value_pair_count=len(value_logprobs),
    )
