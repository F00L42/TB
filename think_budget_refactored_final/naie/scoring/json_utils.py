"""JSON comparison helpers used by the evaluator.

The evaluator is deliberately permissive about JSON null values, but must not
rewrite literal user data (for example a tool parameter named ``nullifier``).
"""
import ast
import json
from collections import Counter
from typing import Any


def preprocess_json_string(json_str):
    """Trim a JSON string without changing the values it contains.

    Older code replaced every occurrence of ``null`` and ``None``.  That made
    valid payloads compare incorrectly and could turn malformed JSON into a
    different request.  Parsing and normalisation now happen structurally.
    """
    return json_str.strip() if isinstance(json_str, str) else json_str


def _parse_json_like(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = preprocess_json_string(value)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Retain compatibility with historic Python-literal reference answers,
        # but never evaluate arbitrary code.
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return value


def _normalise(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_normalise(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def is_list_equal_unordered(list1, list2):
    """Compare lists as multisets, preserving duplicate element counts."""
    if len(list1) != len(list2):
        return False
    return Counter(_canonical(item) for item in list1) == Counter(
        _canonical(item) for item in list2
    )


def compute_similarity(a, b):
    a = _parse_json_like(a)
    b = _parse_json_like(b)

    if isinstance(a, list) and isinstance(b, list):
        return 1.0 if is_list_equal_unordered(a, b) else 0.0
    return 1.0 if _normalise(a) == _normalise(b) else 0.0


def flatten_json(value, prefix=''):
    items = {}
    value = _normalise(value)

    if isinstance(value, dict):
        for key, item in value.items():
            new_key = f"{prefix}.{key}" if prefix else key
            items.update(flatten_json(item, new_key))
    elif isinstance(value, list):
        # Arrays are order-insensitive for this task, while duplicates remain
        # visible because they are retained as separate indexed values.
        for index, item in enumerate(sorted(value, key=_canonical)):
            items.update(flatten_json(item, f"{prefix}[{index}]"))
    else:
        items[prefix] = value
    return items


def full_compare_json_entities(json1_str, json2_str):
    value1 = _parse_json_like(json1_str)
    value2 = _parse_json_like(json2_str)
    return {
        0: {
            'similarity': compute_similarity(value1, value2),
            'value1': value1,
            'value2': value2,
        }
    }


def compare_json_entities(json1_str, json2_str):
    value1 = _parse_json_like(json1_str)
    value2 = _parse_json_like(json2_str)
    if not isinstance(value1, (dict, list)) or not isinstance(value2, (dict, list)):
        return full_compare_json_entities(value1, value2)

    flat_json1 = flatten_json(value1)
    flat_json2 = flatten_json(value2)
    similarities = {}
    for key in set(flat_json1) | set(flat_json2):
        missing = object()
        val1 = flat_json1.get(key, missing)
        val2 = flat_json2.get(key, missing)
        if val1 is missing or val2 is missing:
            similarities[key] = {
                'similarity': 0.0,
                'message': 'JSON field is missing',
            }
        else:
            similarities[key] = {
                'similarity': compute_similarity(val1, val2),
                'value1': val1,
                'value2': val2,
            }
    return similarities
