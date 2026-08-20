"""Map an estimated difficulty score to DTB budget fields."""
from __future__ import annotations

from typing import Dict


def _lerp(a: float, b: float, t: float) -> float:
    return a + t * (b - a)


def _piecewise(
    difficulty: float,
    base: int,
    maximum: int,
) -> int:
    if difficulty < 0.3:
        at_30 = int(_lerp(base, maximum, 0.3))
        return int(_lerp(base, at_30, difficulty / 0.3))
    if difficulty < 0.6:
        t = (difficulty - 0.3) / 0.3
        at_30 = int(_lerp(base, maximum, 0.3))
        at_60 = int(_lerp(base, maximum, 0.6))
        return int(_lerp(at_30, at_60, t))
    t = (difficulty - 0.6) / 0.4
    at_60 = int(_lerp(base, maximum, 0.6))
    return int(_lerp(at_60, maximum, t ** 0.7))


def compute_budget_from_difficulty(
    difficulty: float,
    base_check_length: int = 150,
    max_check_length: int = 4000,
    base_min_think: int = 100,
    max_min_think: int = 4000,
    base_stride: int = 50,
    max_stride: int = 600,
    base_num_thinking: int = 3,
    max_num_thinking: int = 15,
    base_answer_len: int = 500,
    max_answer_len: int = 4000,
) -> Dict[str, int]:
    """Preserve the original piecewise mapping independently of estimation."""
    difficulty = max(0.0, min(float(difficulty), 1.0))
    check_length = _piecewise(
        difficulty, base_check_length, max_check_length
    )
    min_think = _piecewise(difficulty, base_min_think, max_min_think)
    stride = _piecewise(difficulty, base_stride, max_stride)
    num_thinking = _piecewise(
        difficulty, base_num_thinking, max_num_thinking
    )
    if difficulty < 0.3:
        answer_len = int(_lerp(
            base_answer_len,
            max_answer_len,
            difficulty / 0.3 * 0.2,
        ))
    elif difficulty < 0.6:
        t = (difficulty - 0.3) / 0.3
        answer_len = int(_lerp(
            base_answer_len + 0.2 * (max_answer_len - base_answer_len),
            base_answer_len + 0.5 * (max_answer_len - base_answer_len),
            t,
        ))
    else:
        t = (difficulty - 0.6) / 0.4
        answer_len = int(_lerp(
            base_answer_len + 0.5 * (max_answer_len - base_answer_len),
            max_answer_len,
            t ** 0.7,
        ))
    return {
        "check_length": max(
            base_check_length, min(check_length, max_check_length)
        ),
        "min_think_tokens": max(
            base_min_think, min(min_think, max_min_think)
        ),
        "think_stride": max(base_stride, min(stride, max_stride)),
        "num_thinking": max(
            base_num_thinking, min(num_thinking, max_num_thinking)
        ),
        "check_answer_len": max(
            base_answer_len, min(answer_len, max_answer_len)
        ),
    }
