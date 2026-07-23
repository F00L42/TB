"""Model endpoint configuration.

Runtime priority:
1. Model-specific environment variables.
2. ``naie/config/models_config.yaml``.
3. Fallback placeholder endpoint.

Unknown model names are intentionally allowed. They use the fallback URL/key so
an OpenAI-compatible server can expose multiple model IDs without requiring a
code change for every alias.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen3.6-27B"
DEFAULT_API_KEY = "sk-gDdTT07VGCb6keLFBeD8C24145F343B3AeC515Db920d6c0b"
DEFAULT_BASE_URL = "http://10.44.84.60:8000/v1"

_MODELS_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "models_config.yaml"
)

_ENV_MAP = {
    "Qwen3.6-27B": ("QWEN3_6_27B_KEY", "QWEN3_6_27B_URL", DEFAULT_BASE_URL),
    "DeepSeek-R1-108": ("DEEPSEEK_R1_108_KEY", "DEEPSEEK_R1_108_URL", DEFAULT_BASE_URL),
    "DeepSeek-R1": ("DEEPSEEK_R1_KEY", "DEEPSEEK_R1_URL", DEFAULT_BASE_URL),
    "DeepSeek-R1-Distill-Llama-70B": (
        "DEEPSEEK_R1_LLAMA_KEY",
        "DEEPSEEK_R1_LLAMA_URL",
        DEFAULT_BASE_URL,
    ),
    "Qwen3-32B": ("QWEN3_32B_KEY", "QWEN3_32B_URL", DEFAULT_BASE_URL),
    "Qwen3-30B-A3B": ("QWEN3_30B_KEY", "QWEN3_30B_URL", DEFAULT_BASE_URL),
    "minimax-m2.7": ("MINIMAX_M27_KEY", "MINIMAX_M27_URL", DEFAULT_BASE_URL),
    "MiniMax-M2.7": ("MINIMAX_M27_ALT_KEY", "MINIMAX_M27_ALT_URL", DEFAULT_BASE_URL),
    "mg2hg": ("MG2HG_KEY", "MG2HG_URL", DEFAULT_BASE_URL),
    "netmo-qwen3p6-27b": ("NETMO_QWEN3P6_27B_KEY", "NETMO_QWEN3P6_27B_URL", DEFAULT_BASE_URL),
    "minimax27": ("MINIMAX27_KEY", "MINIMAX27_URL", DEFAULT_BASE_URL),
    "codemate/MiniMax-M2.7": ("CODEMATE_KEY", "CODEMATE_URL", DEFAULT_BASE_URL),
}

_FALLBACK_KEY_ENV = "FALLBACK_KEY"
_FALLBACK_URL_ENV = "FALLBACK_URL"

_models_config_cache: Optional[Dict[str, Dict[str, str]]] = None
_MODEL_CONFIGS_INSTANCE: Optional[Dict[str, Dict[str, str]]] = None


def _load_models_yaml() -> Dict[str, Dict[str, str]]:
    """Load model settings from YAML once."""
    global _models_config_cache
    if _models_config_cache is not None:
        return _models_config_cache

    if not os.path.exists(_MODELS_CONFIG_PATH):
        _models_config_cache = {}
        return _models_config_cache

    try:
        import yaml

        with open(_MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        _models_config_cache = config.get("MODELS", {}) or {}
    except ImportError:
        logger.debug("PyYAML is not installed; skipping models_config.yaml")
        _models_config_cache = {}
    except Exception as exc:  # noqa: BLE001 - config load failure should degrade.
        logger.warning("Failed to load models_config.yaml: %s", exc)
        _models_config_cache = {}
    return _models_config_cache


def _build_model_configs() -> Dict[str, Dict[str, str]]:
    yaml_configs = _load_models_yaml()
    result: Dict[str, Dict[str, str]] = {}

    all_model_names = set(_ENV_MAP) | set(yaml_configs)
    for model_name in sorted(all_model_names):
        key_env, url_env, default_url = _ENV_MAP.get(
            model_name,
            (_FALLBACK_KEY_ENV, _FALLBACK_URL_ENV, DEFAULT_BASE_URL),
        )
        yaml_model = yaml_configs.get(model_name, {})
        token = os.getenv(key_env) or yaml_model.get("token") or DEFAULT_API_KEY
        url = os.getenv(url_env) or yaml_model.get("url") or default_url
        result[model_name] = {"token": token, "url": _normalize_base_url(url)}

    return result


def _normalize_base_url(url: str) -> str:
    """Ensure OpenAI SDK receives a URL with scheme and no trailing slash."""
    value = (url or DEFAULT_BASE_URL).strip()
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def _get_model_configs() -> Dict[str, Dict[str, str]]:
    global _MODEL_CONFIGS_INSTANCE
    if _MODEL_CONFIGS_INSTANCE is None:
        _MODEL_CONFIGS_INSTANCE = _build_model_configs()
    return _MODEL_CONFIGS_INSTANCE


class _ModelConfigsProxy:
    """Dict-like lazy proxy kept for compatibility with older imports."""

    def __getitem__(self, key):
        return _get_model_configs()[key]

    def __contains__(self, key):
        return key in _get_model_configs()

    def get(self, key, default=None):
        return _get_model_configs().get(key, default)

    def keys(self):
        return _get_model_configs().keys()

    def values(self):
        return _get_model_configs().values()

    def items(self):
        return _get_model_configs().items()

    def __iter__(self):
        return iter(_get_model_configs())

    def __len__(self):
        return len(_get_model_configs())

    def __repr__(self):
        return repr(_get_model_configs())


MODEL_CONFIGS = _ModelConfigsProxy()


def get_model_config(model_name: str) -> dict:
    """Return token/base_url for a model name.

    Unknown names are routed to the fallback endpoint so clients may pass
    deployment-specific model IDs through the API server.
    """
    configs = _get_model_configs()
    if model_name in configs:
        return configs[model_name]
    return {
        "token": os.getenv(_FALLBACK_KEY_ENV, DEFAULT_API_KEY),
        "url": _normalize_base_url(os.getenv(_FALLBACK_URL_ENV, DEFAULT_BASE_URL)),
    }
