"""Confidence primitives and exact sampled-token logprob extraction."""
from __future__ import annotations

import math
from typing import Any


def cal_confidence(logprobs: list[float | None]) -> float:
    """Reference average probability.

    ``None`` entries do not contribute to the numerator but remain in the
    denominator, matching the synchronous online reference implementation.
    """
    if not logprobs:
        return 0.0
    return sum(
        math.exp(logprob)
        for logprob in logprobs
        if logprob is not None
    ) / len(logprobs)


def cal_perplexity(logprobs: list[float | None]) -> float:
    if not logprobs:
        return 0.0
    valid = [value for value in logprobs if value is not None]
    if not valid:
        return 0.0
    return math.exp(-sum(valid) / len(valid))


def _matches_sampled_token_id(candidate_id: Any, sampled_token_id: int) -> bool:
    if candidate_id == sampled_token_id:
        return True
    try:
        return int(candidate_id) == sampled_token_id
    except (TypeError, ValueError):
        return False


def _sampled_logprob_entry(position: Any, sampled_token_id: int) -> Any:
    if not hasattr(position, "items"):
        return None
    for key in (sampled_token_id, str(sampled_token_id)):
        try:
            entry = position.get(key)
        except AttributeError:
            entry = None
        if entry is not None:
            return entry
    for key, entry in position.items():
        if _matches_sampled_token_id(key, sampled_token_id):
            return entry
        if getattr(entry, "token_id", None) == sampled_token_id:
            return entry
    return None


def extract_token_logprobs_and_texts(
    logprobs_list: list[Any | None] | None,
    sampled_token_ids: list[int] | tuple[int, ...] | None,
) -> tuple[list[float | None], list[str | None]]:
    """Resolve each position against the actual sampled token id."""
    if logprobs_list is None:
        return [], []
    sampled_token_ids = sampled_token_ids or ()
    token_logprobs: list[float | None] = []
    token_texts: list[str | None] = []
    for index, position in enumerate(logprobs_list):
        if position is None or index >= len(sampled_token_ids):
            token_logprobs.append(None)
            token_texts.append(None)
            continue
        entry = _sampled_logprob_entry(position, sampled_token_ids[index])
        if entry is None or not hasattr(entry, "logprob"):
            token_logprobs.append(None)
            token_texts.append(None)
            continue
        token_logprobs.append(entry.logprob)
        token_texts.append(getattr(entry, "decoded_token", None))
    return token_logprobs, token_texts
