"""Clean Context 管理 + Induced Prompt 生成。

从 naie/optimizations/context_control.py 移植，自包含。

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

from think_budget_vllm_adapter.deer_think_budget.confidence import get_think_closing_tag, is_think_tag_paired


def make_induced_prompt(base_prompt: str) -> str:
    """生成诱导提示 — 闭合 think tag 引导模型输出答案。"""
    close_tag = get_think_closing_tag(base_prompt)
    return base_prompt + f"... \n OK, I have completed my thinking and can now provide the final answer.\n{close_tag}\n"


def apply_clean_context(
    enable_clean_context: bool,
    prompt: str,
    induced_prompt: str,
    o: str,
    output_len: int,
    token_num: int,
) -> tuple[str, int]:
    """Clean Context — 继续思考时丢弃 induced 部分节省 token。

    enable_clean_context=True:  保留原始 prompt (不含 induced + trial answer), 省token
    enable_clean_context=False: 保留 induced + trial answer, 让模型看到更多上下文

    Returns:
        (prompt, output_len)
    """
    if enable_clean_context:
        return prompt, output_len
    else:
        return induced_prompt + o, output_len + token_num
