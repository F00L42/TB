"""Deterministic scene matcher with an explicit rejection gate."""
from __future__ import annotations

import re
from functools import lru_cache
from itertools import islice
from typing import Any, Dict, Iterable

from dtb.config.loader import load_dtb_service_config
from dtb.config.schema import DtbServiceConfig


_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_CODE_SIGNATURE_RE = re.compile(
    r"(?m)^\s*(?:def|class|import|from)\s+[A-Za-z_]")
_LATEX_RE = re.compile(
    r"\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\(?:frac|sum|int|sqrt|prod)\b")
_MULTI_STEP_RE = re.compile(
    r"(?:第[一二三四五六七八九十\d]+步|step\s*\d+|phase\s*\d+)",
    re.IGNORECASE,
)
_CHOICE_LINE_RE = re.compile(
    r"(?mi)^\s*([A-H])[\.\)、:：]\s*\S+")
_COUNT_FIELD_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*_count\b")
_SQL_RE = re.compile(
    r"(?i)\b(?:select|insert|update|delete)\s+(?:from|into|set|\*)?")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_TEMPLATE_MATCH_SCORE = 100.0


@lru_cache(maxsize=2048)
def _compile_keyword(keyword: str) -> str | re.Pattern[str]:
    """Cache only immutable keyword rules, never request text or route results.

    Chinese/symbol phrases use casefolded substring matching. Other phrases
    retain Python regex IGNORECASE and Unicode word-boundary semantics.
    """
    if not keyword:
        return ""
    if _CJK_RE.search(keyword) or not any(char.isalnum() for char in keyword):
        return keyword.casefold()
    parts = [re.escape(part) for part in keyword.split()]
    phrase = r"\s+".join(parts)
    if keyword[0].isalnum():
        phrase = r"(?<![\w])" + phrase
    if keyword[-1].isalnum():
        phrase += r"(?![\w])"
    return re.compile(phrase, flags=re.IGNORECASE)


def _matches(
    text: str, keyword: str, *, folded_text: str | None = None,
) -> bool:
    """Match with the original phrase rules and an optional request-local fold."""
    matcher = _compile_keyword(str(keyword).strip())
    if isinstance(matcher, str):
        return bool(matcher) and matcher in (
            text.casefold() if folded_text is None else folded_text
        )
    return matcher.search(text) is not None


def _matched_keywords(
    text: str,
    keywords: Iterable[str],
    *,
    folded_text: str | None = None,
) -> list[str]:
    return [
        str(keyword) for keyword in keywords
        if _matches(text, keyword, folded_text=folded_text)
    ]


def _structural_scores(text: str) -> Dict[str, list[tuple[str, float]]]:
    scores: Dict[str, list[tuple[str, float]]] = {}

    def add(scene: str, evidence: str, score: float) -> None:
        scores.setdefault(scene, []).append((evidence, score))

    if _CODE_BLOCK_RE.search(text):
        add("code_generation", "structure:code_block", 4.0)
    if _CODE_SIGNATURE_RE.search(text):
        add("code_generation", "structure:code_signature", 3.0)
    if _LATEX_RE.search(text):
        add("math_reasoning", "structure:latex", 3.0)
    # Scores saturate at three steps / two count fields. Do not allocate every
    # match from a long prompt after the score is already determined.
    step_count = sum(1 for _ in islice(_MULTI_STEP_RE.finditer(text), 3))
    if step_count:
        add("planning", "structure:numbered_steps", min(2.0 * step_count, 6.0))
    choice_labels = set(_CHOICE_LINE_RE.findall(text))
    if len(choice_labels) >= 3:
        add("classification", "structure:choice_list", 3.0)
    if sum(1 for _ in islice(_COUNT_FIELD_RE.finditer(text), 2)) >= 2:
        add("statistics_aggregation", "structure:count_fields", 3.0)
    if _SQL_RE.search(text):
        add("tool_call", "structure:sql_statement", 3.0)
    return scores


def classify_scene(
    question: str,
    *,
    min_score: float = 3.0,
    min_margin: float = 1.0,
    scene_override: str = "",
    service_config: DtbServiceConfig | None = None,
) -> Dict[str, Any]:
    """Map visible request text directly to a scene, or reject to ``general``."""
    service_config = service_config or load_dtb_service_config()
    definitions = service_config.scenes
    priority = service_config.scene_priority
    priority_rank = {
        scene: index for index, scene in enumerate(priority)}
    structural = _structural_scores(question)
    folded_question = question.casefold()
    scores: Dict[str, float] = {}
    strong_matches: Dict[str, list[str]] = {}
    weak_matches: Dict[str, list[str]] = {}
    required_matches: Dict[str, Dict[str, list[str]]] = {}
    missing_required: Dict[str, Dict[str, list[str]]] = {}
    structural_matches: Dict[str, list[str]] = {}

    for scene, definition in definitions.items():
        indicators = definition.get("indicators", {})
        required_all = [
            str(keyword) for keyword in indicators.get("required_all", [])
        ]
        required_any = [
            str(keyword) for keyword in indicators.get("required_any", [])
        ]
        matched_required_all = _matched_keywords(
            question, required_all, folded_text=folded_question)
        matched_required_any = _matched_keywords(
            question, required_any, folded_text=folded_question)
        has_required_gate = bool(required_all or required_any)
        required_gate_matched = (
            len(matched_required_all) == len(required_all)
            and (not required_any or bool(matched_required_any))
        )
        strong = _matched_keywords(
            question, indicators.get("strong", []), folded_text=folded_question)
        weak = _matched_keywords(
            question, indicators.get("weak", []), folded_text=folded_question)
        contributions = structural.get(scene, [])
        if has_required_gate and not required_gate_matched:
            score = 0.0
        else:
            score = (
                (_TEMPLATE_MATCH_SCORE if has_required_gate else 0.0)
                + 3.0 * len(strong)
                + 1.0 * len(weak)
                + sum(value for _, value in contributions)
            )
        scores[scene] = round(score, 3)
        if has_required_gate:
            required_matches[scene] = {
                "all": matched_required_all,
                "any": matched_required_any,
            }
            missing_required[scene] = {
                "all": [
                    keyword for keyword in required_all
                    if keyword not in matched_required_all
                ],
                "any": [] if matched_required_any else required_any,
            }
        if strong:
            strong_matches[scene] = strong
        if weak:
            weak_matches[scene] = weak
        if contributions:
            structural_matches[scene] = [
                evidence for evidence, _ in contributions]

    ranked = sorted(
        (
            (scene, score)
            for scene, score in scores.items()
            if scene != "general"
        ),
        key=lambda item: (
            -item[1],
            priority_rank.get(item[0], len(priority)),
            item[0],
        ),
    )
    candidate_scene, top_score = (
        ranked[0] if ranked else ("general", 0.0))
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    score_margin = round(top_score - second_score, 3)

    reject_reason: str | None = None
    if scene_override:
        if scene_override != "general" and scene_override in definitions:
            selected_scene = scene_override
            scene_matched = True
            candidate_scene = scene_override
            reject_reason = None
        else:
            selected_scene = "general"
            scene_matched = False
            reject_reason = "unknown_scene_override"
    elif top_score <= 0:
        selected_scene = "general"
        scene_matched = False
        reject_reason = "no_evidence"
    elif top_score < min_score:
        selected_scene = "general"
        scene_matched = False
        reject_reason = "below_min_score"
    elif score_margin < min_margin:
        selected_scene = "general"
        scene_matched = False
        reject_reason = "below_min_margin"
    else:
        selected_scene = candidate_scene
        scene_matched = True

    selected = definitions.get(selected_scene, definitions.get("general", {}))
    return {
        "scene": selected_scene,
        "scene_matched": scene_matched,
        "candidate_scene": candidate_scene,
        "top_score": round(top_score, 3),
        "second_score": round(second_score, 3),
        "score_margin": score_margin,
        "min_score": float(min_score),
        "min_margin": float(min_margin),
        "reject_reason": reject_reason,
        "scores": scores,
        "required_matches": required_matches,
        "missing_required": missing_required,
        "strong_matches": strong_matches,
        "weak_matches": weak_matches,
        "structural_matches": structural_matches,
        "typical_tokens": int(selected.get("typical_tokens", 3000)),
        "token_range": list(selected.get("token_range", [500, 15000])),
    }


def get_scene_typical_tokens(
    scene: str,
    service_config: DtbServiceConfig | None = None,
) -> int:
    service_config = service_config or load_dtb_service_config()
    selected = service_config.scenes.get(
        scene, service_config.scenes.get("general", {})
    )
    return int(selected.get("typical_tokens", 3000))
