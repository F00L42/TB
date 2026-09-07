"""Reference-aligned structural-token filtering for Value Min confidence."""
from __future__ import annotations

import re


DEFAULT_STRUCT_TOKENS = frozenset({
    # JSON syntax
    "{", "}", "[", "]", '"', "'", ":", ",", "```", "json", "JSON",
    # Mathematical / code operators
    "=", "+", "-", "*", "/", ">", "<", "≥", "≤", "≈", "≠", "→", "=>",
    # Code structure
    ";", "!", "#", "%", "@", "&", "^", "~",
})
DEFAULT_KEY_NAMES: frozenset[str] = frozenset()

_WHITESPACE_RE = re.compile(r"^\s+$")
_SPECIAL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|im_sep|>")
_SINGLE_PUNCT = '{}[]"\':,._-|/\\()'

_struct_tokens = set(DEFAULT_STRUCT_TOKENS)
_task_key_names: set[str] = set(DEFAULT_KEY_NAMES)

_NAIE_KEY_NAMES = {
    "tool", "faultNumber", "faultIMSI", "faultPort", "faultType",
    "fault", "Number", "IMSI", "Url", "Ip", "APN", "Time", "Range",
    "Caller", "result", "answer", "name", "type", "value",
    "faultUrl", "faultIp", "faultAPN", "faultTimeRange",
    "isCaller", "status", "code", "message", "id", "data",
    "error", "success", "query", "response",
}


def set_task_key_names(names: set[str] | list[str]) -> None:
    global _task_key_names
    _task_key_names = {name.lower() for name in names}


def set_extra_struct_tokens(tokens: set[str] | list[str]) -> None:
    global _struct_tokens
    _struct_tokens = set(DEFAULT_STRUCT_TOKENS) | set(tokens)


def get_key_names() -> set[str]:
    return set(_task_key_names)


def is_structural_token(
    token: str,
    key_names: set[str] | None = None,
    extra_struct_tokens: set[str] | None = None,
) -> bool:
    """Match the synchronous reference set, excluding ``</think>`` by policy."""
    if not token:
        return True
    if token == "</think>":
        return False
    struct_tokens = (
        _struct_tokens
        if extra_struct_tokens is None
        else set(DEFAULT_STRUCT_TOKENS) | set(extra_struct_tokens)
    )
    if token in struct_tokens:
        return True
    if _WHITESPACE_RE.match(token):
        return True
    if token in _SPECIAL_TOKENS:
        return True
    cleaned = token.strip().lower()
    effective_keys = (
        _task_key_names
        if key_names is None
        else {name.lower() for name in key_names}
    )
    if cleaned in effective_keys:
        return True
    return len(cleaned) <= 1 and cleaned in _SINGLE_PUNCT


def extract_value_logprobs(
    logprobs: list[float | None],
    tokens: list[str | None],
) -> list[float]:
    if not logprobs or not tokens:
        return []
    return [
        logprob
        for logprob, token in zip(logprobs, tokens)
        if logprob is not None
        and token is not None
        and not is_structural_token(token)
    ]


def mean_bottom_k_logprobs(values: list[float], bottom_k: int) -> float:
    """Aggregate already-filtered values in the reference summation order."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    count = min(max(1, bottom_k), len(ordered))
    return sum(ordered[:count]) / count


def cal_value_min_confidence(
    logprobs: list[float | None],
    tokens: list[str | None],
    bottom_k: int = 3,
) -> float:
    value_logprobs = extract_value_logprobs(logprobs, tokens)
    if not value_logprobs:
        valid = [value for value in logprobs if value is not None]
        return min(valid) if valid else 0.0
    return mean_bottom_k_logprobs(value_logprobs, bottom_k)


def cal_reasoning_zone_confidence(
    logprobs: list[float | None],
    tokens: list[str | None],
    min_percentile: float = 0.1,
) -> float:
    value_logprobs = sorted(extract_value_logprobs(logprobs, tokens))
    if not value_logprobs:
        valid = [value for value in logprobs if value is not None]
        return min(valid) if valid else 0.0
    count = max(1, int(len(value_logprobs) * min_percentile))
    count = min(count, len(value_logprobs))
    return sum(value_logprobs[:count]) / count


# The synchronous online entrypoint injects these names at import time.
set_task_key_names(_NAIE_KEY_NAMES)
