"""External model-endpoint configuration for the standalone research client.

No endpoint or credential is embedded in source code.  Set the matching
``*_URL`` and ``*_KEY`` process environment variables before starting a client.
"""
import os


def _external_model(key_env: str, url_env: str) -> dict[str, str]:
    return {
        "token": os.getenv(key_env, ""),
        "url": os.getenv(url_env, ""),
        "key_env": key_env,
        "url_env": url_env,
    }


MODEL_CONFIGS = {
    "DeepSeek-R1-108": _external_model("DEEPSEEK_R1_108_KEY", "DEEPSEEK_R1_108_URL"),
    "DeepSeek-R1": _external_model("DEEPSEEK_R1_KEY", "DEEPSEEK_R1_URL"),
    "DeepSeek-R1-Distill-Llama-70B": _external_model(
        "DEEPSEEK_R1_LLAMA_KEY", "DEEPSEEK_R1_LLAMA_URL"),
    "Qwen3-32B": _external_model("QWEN3_32B_KEY", "QWEN3_32B_URL"),
    "Qwen3-30B-A3B": _external_model("QWEN3_30B_KEY", "QWEN3_30B_URL"),
    "minimax-m2.7": _external_model("MINIMAX_M27_KEY", "MINIMAX_M27_URL"),
    "MiniMax-M2.7": _external_model("MINIMAX_M27_ALT_KEY", "MINIMAX_M27_ALT_URL"),
    "mg2hg": _external_model("MG2HG_KEY", "MG2HG_URL"),
    "netmo-qwen3p6-27b": _external_model(
        "NETMO_QWEN3P6_27B_KEY", "NETMO_QWEN3P6_27B_URL"),
    "minimax27": _external_model("MINIMAX27_KEY", "MINIMAX27_URL"),
    "codemate/MiniMax-M2.7": _external_model("CODEMATE_KEY", "CODEMATE_URL"),
}


def get_model_config(model_name: str) -> dict[str, str]:
    """Return a configured model endpoint or fail before an HTTP request.

    An unknown model deliberately uses the explicit fallback variables rather
    than silently routing production data to a source-code default endpoint.
    """
    config = dict(MODEL_CONFIGS.get(
        model_name, _external_model("FALLBACK_KEY", "FALLBACK_URL")))
    if not config["token"] or not config["url"]:
        raise ValueError(
            f"Model {model_name!r} is not configured. Set {config['key_env']} "
            f"and {config['url_env']} in the service environment.")
    return config
