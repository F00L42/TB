"""Startup validation and compilation of scene actions and identity rules."""
from __future__ import annotations

import math
from typing import Any

from dtb.config.schema import CompiledSceneRule, DtbAlgorithmConfig, DtbRoutingConfig


SYSTEM_PREFIX_LIMIT = 512
FIRST_USER_PREFIX_LIMIT = 256
MESSAGE_LIMIT = 4096
_BUDGET_FIELDS = {
    "check_length", "think_stride", "num_thinking", "min_think_tokens",
    "min_think_budget", "prob_th", "confidence_threshold", "max_think_tokens",
    "check_answer_len",
}
_SCENE_FIELDS = _BUDGET_FIELDS | {
    "description", "typical_tokens", "token_range", "indicators", "action", "match",
}


def compile_routing(raw: Any, scenes: dict, algorithm: DtbAlgorithmConfig):
    if not isinstance(raw, dict) or set(raw) - {"mode", "on_unmatched"}:
        raise ValueError("ROUTING must contain only mode and on_unmatched")
    routing = DtbRoutingConfig(**raw)
    system_mode = routing.mode == "system_prefix"
    if system_mode and (not algorithm.enable_scene_budget
                        or algorithm.enable_history_budget
                        or algorithm.enable_difficulty_estimator
                        or algorithm.scene_override):
        raise ValueError("system_prefix requires scene budget, no history/difficulty/scene_override")
    rules = []
    native = routing.on_unmatched == "native"
    for name, definition in scenes.items():
        if not isinstance(name, str) or not name or not isinstance(definition, dict):
            raise ValueError("SCENES must map nonempty names to mappings")
        if set(definition) - _SCENE_FIELDS:
            raise ValueError(f"unknown SCENES.{name} fields: {set(definition) - _SCENE_FIELDS}")
        action = definition.get("action", "dtb")
        if action not in ("dtb", "native"):
            raise ValueError(f"SCENES.{name}.action must be dtb or native")
        native |= action == "native"
        if action == "native" and _BUDGET_FIELDS.intersection(definition):
            raise ValueError(f"native scene {name} must not contain budget fields")
        if not system_mode:
            if "match" in definition:
                raise ValueError("match requires ROUTING.mode=system_prefix")
            continue
        if "indicators" in definition:
            raise ValueError("system_prefix does not accept indicators")
        if name == "general":
            if set(definition) - {"description"}:
                raise ValueError("system_prefix general is only an unmatched label")
            continue
        match = definition.get("match")
        if not isinstance(match, dict) or set(match) - {
            "system_prefix", "first_user_prefix", "tools_empty"
        }:
            raise ValueError(f"invalid SCENES.{name}.match")
        for field, limit in (("system_prefix", SYSTEM_PREFIX_LIMIT),
                             ("first_user_prefix", FIRST_USER_PREFIX_LIMIT)):
            if field == "first_user_prefix" and field not in match:
                continue
            value = match.get(field)
            if not isinstance(value, str) or not value or len(value) > limit:
                raise ValueError(f"{name}.{field} must have 1..{limit} characters")
        if "tools_empty" in match and type(match["tools_empty"]) is not bool:
            raise ValueError(f"{name}.tools_empty must be boolean")
        prefix = match["system_prefix"]
        if any(prefix.startswith(rule.system_prefix) or
               rule.system_prefix.startswith(prefix) for rule in rules):
            raise ValueError(f"overlapping system prefixes: {name}")
        if action == "dtb":
            for field in ("check_length", "think_stride", "num_thinking",
                          "min_think_tokens", "max_think_tokens"):
                value = definition.get(field)
                if type(value) is not int or value < 1:
                    raise ValueError(f"{name}.{field} must be a positive integer")
            probability = definition.get("prob_th")
            if (type(probability) not in (int, float) or not math.isfinite(probability)
                    or not 0 <= probability <= 1):
                raise ValueError(f"{name}.prob_th must be in [0, 1]")
            if definition["check_length"] > definition["max_think_tokens"]:
                raise ValueError(f"{name}.check_length exceeds max_think_tokens")
            if {"min_think_budget", "confidence_threshold", "check_answer_len"}.intersection(definition):
                raise ValueError(f"{name}: use min_think_tokens/prob_th; trial length is code-owned")
        rules.append(CompiledSceneRule(name, action, prefix,
                                      match.get("first_user_prefix"), match.get("tools_empty")))
    if system_mode and not rules:
        raise ValueError("system_prefix requires at least one scene rule")
    return routing, tuple(rules), native
