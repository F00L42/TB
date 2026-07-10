import os

MODEL_CONFIGS = {
    "DeepSeek-R1-108": {"token": os.getenv("DEEPSEEK_R1_108_KEY", "sk-xxx"), "url": os.getenv("DEEPSEEK_R1_108_URL", "http://oneapi.rnd.huawei.com/v1")},
    "DeepSeek-R1": {"token": os.getenv("DEEPSEEK_R1_KEY", "sk-xxx"), "url": os.getenv("DEEPSEEK_R1_URL", "http://oneapi.rnd.huawei.com/v1")},
    "DeepSeek-R1-Distill-Llama-70B": {"token": os.getenv("DEEPSEEK_R1_LLAMA_KEY", "sk-xxx"), "url": os.getenv("DEEPSEEK_R1_LLAMA_URL", "http://oneapi.rnd.huawei.com/v1")},
    "Qwen3-32B": {"token": os.getenv("QWEN3_32B_KEY", "sk-gDdTT07VGCb6keLFBeD8C24145F343B3AeC515Db920d6c0b"), "url": os.getenv("QWEN3_32B_URL", "http://oneapi.rnd.huawei.com/v1")},
    "Qwen3-30B-A3B": {"token": os.getenv("QWEN3_30B_KEY", ""), "url": os.getenv("QWEN3_30B_URL", "http://oneapi.rnd.huawei.com/v1")},
    "minimax-m2.7": {"token": os.getenv("MINIMAX_M27_KEY", "sk-gDdTT07VGCb6keLFBeD8C24145F343B3AeC515Db920d6c0b"), "url": os.getenv("MINIMAX_M27_URL", "http://oneapi.rnd.huawei.com/v1")},
    "MiniMax-M2.7": {"token": os.getenv("MINIMAX_M27_ALT_KEY", "ah-625bc34bdb0169905cfcbc27479cc4db8bb572f15ec656d80f4414109c272384"), "url": os.getenv("MINIMAX_M27_ALT_URL", "http://70.248.198.1:8080/v1")},
    "mg2hg": {"token": os.getenv("MG2HG_KEY", "sk-gDdTT07VGCb6keLFBeD8C24145F343B3AeC515Db920d6c0b"), "url": os.getenv("MG2HG_URL", "http://141.73.2.25:73/v1")},
    "netmo-qwen3p6-27b": {"token": os.getenv("netmo-qwen3p6-27b_KEY", "sk-gDdTT07VGCb6keLFBeD8C24145F343B3AeC515Db920d6c0b"), "url": os.getenv("netmo-qwen3p6-27b_URL", "https://71.77.65.6:32880/rest/netrsn/v1")},
    "minimax27": {"token": os.getenv("MINIMAX27_KEY", "sk-gDdTT07VGCb6keLFBeD8C24145F343B3AeC515Db920d6c0b"), "url": os.getenv("MINIMAX27_URL", "http://oneapi.rnd.huawei.com/v1")},
    "codemate/MiniMax-M2.7": {"token": os.getenv("CODEMATE_KEY", "xxx"), "url": os.getenv("CODEMATE_URL", "http://127.0.0.1:31943/v1/")},
}


def get_model_config(model_name: str) -> dict:
    if model_name in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_name]
    return {"token": os.getenv("FALLBACK_KEY", "sk-xxx"), "url": os.getenv("FALLBACK_URL", "http://oneapi.rnd.huawei.com/v1")}
