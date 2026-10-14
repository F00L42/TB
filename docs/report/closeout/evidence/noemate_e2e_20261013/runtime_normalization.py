"""Narrow sensitivity analysis of the observed main-system runtime metadata.

Call only on a string system-message content, never on serialized full bodies,
user messages, tool histories, or responses. Exact primary hashes remain intact.
"""
from __future__ import annotations

import re

MODEL_DATE_LINE = re.compile(r"(?m)^(  model=[^;\n]+; date=)\d{4}-\d{2}-\d{2}$")
LINUX_DATE_LINE = re.compile(r"(?m)^(  linux [^\n]+ \(America/Chicago, )\d{4}-\d{2}-\d{2}(\))$")
KNOWN_ALIAS_LINE = re.compile(r"(?m)^(  model=)netmo-qwen3\.6-27b(; date=<DATE>)$")


def normalize_runtime(system: str, alias: bool = False) -> str:
    result = MODEL_DATE_LINE.sub(r"\1<DATE>", system)
    result = LINUX_DATE_LINE.sub(r"\1<DATE>\2", result)
    if alias:
        result = KNOWN_ALIAS_LINE.sub(r"\1qwen3.6-27b\2", result)
    return result


normalize_runtime_system = normalize_runtime
