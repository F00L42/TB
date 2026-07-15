from naie.models.confidence import get_think_closing_tag, is_think_tag_paired


def make_induced_prompt(base_prompt: str) -> str:
    """生成诱导提示 — 闭合 think tag 引导模型输出答案。"""
    close_tag = get_think_closing_tag(base_prompt)
    return base_prompt + f"... \n OK, I have completed my thinking and can now provide the final answer.\n{close_tag}\n"


def apply_clean_context(enable_clean_context: bool, prompt: str, induced_prompt: str,
                        o: str, output_len: int, token_num: int):
    if enable_clean_context:
        return prompt, output_len
    else:
        return induced_prompt + o, output_len + token_num
