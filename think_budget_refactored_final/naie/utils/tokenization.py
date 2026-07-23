"""Tokenizer-backed token counting helpers.

The API server and budget core both run in an environment with the model
tokenizer available. Token counts therefore must come from the tokenizer
instead of character-count approximations.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from naie.config.settings import NAIE_CONFIG

_tokenizer: Optional[Any] = None


def get_tokenizer() -> Any:
    global _tokenizer
    if _tokenizer is None:
        tokenizer_path = NAIE_CONFIG["tokenizer_path"]
        if not os.path.isdir(tokenizer_path):
            raise RuntimeError(f"Tokenizer path not available: {tokenizer_path}")
        from transformers import AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)
    return _tokenizer


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(get_tokenizer().encode(text, add_special_tokens=False))
