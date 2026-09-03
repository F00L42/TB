"""Extract algorithm inputs from native vLLM request structures."""
from __future__ import annotations

from typing import Any


def merge_stop_token_ids(params: Any, required_ids: tuple[int, ...]) -> None:
    """Mutate a post-init vLLM SamplingParams stop set atomically.

    vLLM 0.23 materializes ``_all_stop_token_ids`` during ``post_init`` and
    later uses that set for ``min_tokens`` masking.  DTB receives an already
    initialized object from ``ChatCompletionRequest.to_sampling_params()``, so
    both representations must be updated together.
    """
    current = list(getattr(params, "stop_token_ids", None) or ())
    for token_id in required_ids:
        if token_id not in current:
            current.append(token_id)

    all_stop_ids = getattr(params, "_all_stop_token_ids", None)
    if all_stop_ids is not None and not isinstance(all_stop_ids, set):
        raise RuntimeError(
            "vLLM SamplingParams._all_stop_token_ids is not a mutable set"
        )
    merged_all_stop_ids = (
        set(current) if all_stop_ids is None else set(all_stop_ids)
    )
    merged_all_stop_ids.update(required_ids)

    if not set(current).issubset(merged_all_stop_ids):
        raise RuntimeError(
            "vLLM SamplingParams stop_token_ids/all_stop_token_ids diverged"
        )

    # Commit only after all representations have validated.  The public list
    # remains stable-ordered; the private set may also contain EOS supplied by
    # generation config and therefore must not be replaced on native objects.
    params.stop_token_ids = current
    if all_stop_ids is None:
        # Lightweight test doubles do not run vLLM's post_init.  Creating the
        # same private field keeps the adapter invariant observable in tests.
        params._all_stop_token_ids = merged_all_stop_ids
    else:
        all_stop_ids.update(required_ids)


def visible_user_text(
    conversation: list[Any], fallback: str
) -> tuple[str, str]:
    """Return the latest visible user text, never the rendered chat template."""

    def flatten(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        parts.append(value)
                else:
                    value = getattr(item, "text", None)
                    if isinstance(value, str):
                        parts.append(value)
            return "\n".join(parts)
        return ""

    for message in reversed(conversation or []):
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        else:
            role = getattr(message, "role", None)
            content = getattr(message, "content", None)
        if role == "user":
            text = flatten(content).strip()
            if text:
                return text, "latest_user_message"
    return fallback, "decoded_prompt_fallback"
