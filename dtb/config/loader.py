"""Fail-fast, startup-only loader for the DTB YAML configuration."""
from __future__ import annotations

from dataclasses import fields
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from dtb.config.schema import (
    DtbAlgorithmConfig,
    DtbHistoryStoreConfig,
    DtbObservabilityConfig,
    DtbServiceConfig,
    DtbTelemetryMode,
)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("budget_config.yaml")


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} in budget_config.yaml must be a mapping")
    return value


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            key: _freeze_value(item) for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _freeze_value(value)


@lru_cache(maxsize=None)
def load_dtb_service_config(path: str | Path = DEFAULT_CONFIG_PATH) -> DtbServiceConfig:
    """Load and validate one immutable configuration snapshot.

    The cache key is the explicit path. There is intentionally no mtime check,
    fallback table, or request-path reload. Configuration changes take effect
    only after process restart.
    """
    config_path = Path(path).resolve()
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except Exception as exc:
        raise RuntimeError(
            f"failed to load DTB configuration {config_path}: {exc}"
        ) from exc
    root = _mapping(raw, "top-level value")
    allowed_top_level = {
        "RUNTIME", "OBSERVABILITY", "SCENES", "SCENE_PRIORITY",
        "DIFFICULTY_TIERS", "HISTORY_STORE", "HISTORY_NEW_TASK",
        "HISTORY_WARM_START", "HISTORY_HOT_START",
    }
    unknown_top_level = set(root) - allowed_top_level
    if unknown_top_level:
        raise ValueError(
            "unknown top-level DTB configuration keys: "
            + ", ".join(sorted(unknown_top_level))
        )

    runtime = _mapping(root.get("RUNTIME"), "RUNTIME")
    algorithm_field_names = {item.name for item in fields(DtbAlgorithmConfig)}
    runtime_aliases = {"BUDGET_ENABLED": "budget_enabled"}
    unknown_runtime = set(runtime) - (
        algorithm_field_names | set(runtime_aliases)
    )
    if unknown_runtime:
        raise ValueError(
            "unknown RUNTIME keys in budget_config.yaml: "
            + ", ".join(sorted(unknown_runtime))
        )
    algorithm_values = {
        runtime_aliases.get(name, name): value
        for name, value in runtime.items()
    }
    algorithm = DtbAlgorithmConfig(**algorithm_values)

    observability_values = _mapping(
        root.get("OBSERVABILITY", {}), "OBSERVABILITY"
    )
    allowed_observability = {"mode", "queue_capacity", "stream_request_ids"}
    unknown_observability = set(observability_values) - allowed_observability
    if unknown_observability:
        raise ValueError(
            "unknown OBSERVABILITY keys: "
            + ", ".join(sorted(unknown_observability))
        )
    try:
        mode = DtbTelemetryMode(str(observability_values.get("mode", "off")))
    except ValueError as exc:
        raise ValueError(
            "OBSERVABILITY.mode must be off, phase, or stream_audit"
        ) from exc
    request_ids = observability_values.get("stream_request_ids", [])
    if not isinstance(request_ids, list) or not all(
        isinstance(item, str) and item for item in request_ids
    ):
        raise ValueError("OBSERVABILITY.stream_request_ids must be a string list")
    observability = DtbObservabilityConfig(
        mode=mode,
        queue_capacity=int(observability_values.get("queue_capacity", 2048)),
        stream_request_ids=frozenset(request_ids),
    )

    history_store_values = _mapping(
        root.get("HISTORY_STORE", {}), "HISTORY_STORE"
    )
    allowed_history_store = {
        "path", "max_entries", "max_age_days", "queue_capacity"
    }
    unknown_history_store = set(history_store_values) - allowed_history_store
    if unknown_history_store:
        raise ValueError(
            "unknown HISTORY_STORE keys: "
            + ", ".join(sorted(unknown_history_store))
        )
    history_path = Path(history_store_values.get(
        "path", "../data/history/inference_history.jsonl"
    ))
    if not history_path.is_absolute():
        history_path = (config_path.parent / history_path).resolve()
    history_store = DtbHistoryStoreConfig(
        path=str(history_path),
        max_entries=int(history_store_values.get("max_entries", 5000)),
        max_age_days=int(history_store_values.get("max_age_days", 7)),
        queue_capacity=int(history_store_values.get("queue_capacity", 1024)),
    )

    scenes = _mapping(root.get("SCENES"), "SCENES")
    priority = root.get("SCENE_PRIORITY")
    if not isinstance(priority, list) or not all(
        isinstance(item, str) and item for item in priority
    ):
        raise ValueError("SCENE_PRIORITY must be a string list")
    difficulty_tiers = _mapping(
        root.get("DIFFICULTY_TIERS"), "DIFFICULTY_TIERS"
    )
    return DtbServiceConfig(
        algorithm=algorithm,
        observability=observability,
        history_store=history_store,
        scenes=_frozen_mapping({
            name: _frozen_mapping(_mapping(value, f"SCENES.{name}"))
            for name, value in scenes.items()
        }),
        scene_priority=tuple(priority),
        difficulty_tiers=_frozen_mapping({
            name: _frozen_mapping(_mapping(value, f"DIFFICULTY_TIERS.{name}"))
            for name, value in difficulty_tiers.items()
        }),
        history_new_task=_frozen_mapping(
            _mapping(root.get("HISTORY_NEW_TASK", {}), "HISTORY_NEW_TASK")
        ),
        history_warm_start=_frozen_mapping(
            _mapping(root.get("HISTORY_WARM_START", {}), "HISTORY_WARM_START")
        ),
        history_hot_start=_frozen_mapping(
            _mapping(root.get("HISTORY_HOT_START", {}), "HISTORY_HOT_START")
        ),
    )


def clear_dtb_config_cache() -> None:
    """Test-only cache reset; production reloads configuration by restart."""
    load_dtb_service_config.cache_clear()
