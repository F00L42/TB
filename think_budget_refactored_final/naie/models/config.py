"""模型配置 — 从环境变量或 models_config.yaml 加载 API token 和 URL。

安全原则:
  - 真实密钥仅通过环境变量或 models_config.yaml 提供
  - 源码中不硬编码任何真实 token/URL
  - 默认值为空字符串或占位符，确保未配置时显式失败而非静默使用泄露密钥
"""
import os
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ─── 模型配置文件路径 ───
_MODELS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'models_config.yaml')

# ─── 模型配置缓存 ───
_models_config_cache: Optional[Dict] = None

# ─── 环境变量名→模型名映射 ───
# 格式: model_name → (token_env_var, url_env_var, default_url)
# default_url 仅作为开发便利，生产环境应通过环境变量或 YAML 配置
_ENV_MAP = {
    "DeepSeek-R1-108": ("DEEPSEEK_R1_108_KEY", "DEEPSEEK_R1_108_URL", "http://oneapi.rnd.huawei.com/v1"),
    "DeepSeek-R1": ("DEEPSEEK_R1_KEY", "DEEPSEEK_R1_URL", "http://oneapi.rnd.huawei.com/v1"),
    "DeepSeek-R1-Distill-Llama-70B": ("DEEPSEEK_R1_LLAMA_KEY", "DEEPSEEK_R1_LLAMA_URL", "http://oneapi.rnd.huawei.com/v1"),
    "Qwen3-32B": ("QWEN3_32B_KEY", "QWEN3_32B_URL", "http://oneapi.rnd.huawei.com/v1"),
    "Qwen3-30B-A3B": ("QWEN3_30B_KEY", "QWEN3_30B_URL", "http://oneapi.rnd.huawei.com/v1"),
    "minimax-m2.7": ("MINIMAX_M27_KEY", "MINIMAX_M27_URL", "http://oneapi.rnd.huawei.com/v1"),
    "MiniMax-M2.7": ("MINIMAX_M27_ALT_KEY", "MINIMAX_M27_ALT_URL", "http://70.248.198.1:8080/v1"),
    "mg2hg": ("MG2HG_KEY", "MG2HG_URL", "http://141.73.2.25:73/v1"),
    "netmo-qwen3p6-27b": ("netmo-qwen3p6-27b_KEY", "netmo-qwen3p6-27b_URL", "https://71.77.65.6:32880/rest/netrsn/v1"),
    "minimax27": ("MINIMAX27_KEY", "MINIMAX27_URL", "http://oneapi.rnd.huawei.com/v1"),
    "codemate/MiniMax-M2.7": ("CODEMATE_KEY", "CODEMATE_URL", "http://127.0.0.1:31943/v1/"),
}

# ─── 回退配置 ───
_FALLBACK_KEY_ENV = "FALLBACK_KEY"
_FALLBACK_URL_ENV = "FALLBACK_URL"
_FALLBACK_URL_DEFAULT = "http://oneapi.rnd.huawei.com/v1"


def _load_models_yaml() -> Dict:
    """从 models_config.yaml 加载模型配置(带缓存)。"""
    global _models_config_cache
    if _models_config_cache is not None:
        return _models_config_cache

    if not os.path.exists(_MODELS_CONFIG_PATH):
        _models_config_cache = {}
        return _models_config_cache

    try:
        import yaml
        with open(_MODELS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        _models_config_cache = config.get('MODELS', {})
        return _models_config_cache
    except ImportError:
        logger.debug("PyYAML 未安装，跳过 models_config.yaml")
        _models_config_cache = {}
        return _models_config_cache
    except Exception as e:
        logger.warning(f"加载 models_config.yaml 失败: {e}")
        _models_config_cache = {}
        return _models_config_cache


def _build_model_configs() -> Dict[str, Dict]:
    """构建模型配置字典 — 优先级: 环境变量 > YAML > 默认URL。

    安全: token 默认值为空字符串，不硬编码任何真实密钥。
    """
    yaml_configs = _load_models_yaml()
    result = {}

    for model_name, (key_env, url_env, default_url) in _ENV_MAP.items():
        # Token: 环境变量 > YAML > 空字符串(安全默认)
        token = os.getenv(key_env, "")
        if not token and model_name in yaml_configs:
            token = yaml_configs[model_name].get("token", "")

        # URL: 环境变量 > YAML > default_url
        url = os.getenv(url_env, "")
        if not url and model_name in yaml_configs:
            url = yaml_configs[model_name].get("url", "")
        if not url:
            url = default_url

        result[model_name] = {"token": token, "url": url}

    return result


# ─── 懒加载 MODEL_CONFIGS ───
_MODEL_CONFIGS_INSTANCE: Optional[Dict] = None


def _get_model_configs() -> Dict[str, Dict]:
    """获取模型配置(懒加载，首次调用时构建)。"""
    global _MODEL_CONFIGS_INSTANCE
    if _MODEL_CONFIGS_INSTANCE is None:
        _MODEL_CONFIGS_INSTANCE = _build_model_configs()
    return _MODEL_CONFIGS_INSTANCE


# 兼容旧代码: MODEL_CONFIGS 作为属性访问
class _ModelConfigsProxy:
    """代理类 — 让 MODEL_CONFIGS 保持 dict 接口但使用懒加载。"""
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
    """获取指定模型的 token 和 url 配置。

    优先级: MODEL_CONFIGS[model_name] > FALLBACK 环境变量 > 默认
    """
    configs = _get_model_configs()
    if model_name in configs:
        return configs[model_name]
    # 回退到通用配置
    return {
        "token": os.getenv(_FALLBACK_KEY_ENV, ""),
        "url": os.getenv(_FALLBACK_URL_ENV, _FALLBACK_URL_DEFAULT),
    }
