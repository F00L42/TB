"""Extract algorithm inputs from native vLLM request structures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dtb.config.routing import MESSAGE_LIMIT, SYSTEM_PREFIX_LIMIT


def message_field(message: Any, field: str) -> Any:
    return message.get(field) if isinstance(message, dict) else getattr(message, field, None)


@dataclass(frozen=True)
class SystemIdentityView:
    prefix: str = ""
    first_user: Any = None
    reject_reason: str | None = None


def system_identity_view(messages: Any) -> SystemIdentityView:
    """Read bounded role metadata and one system head, never history bodies."""
    if not isinstance(messages, (list, tuple)) or not messages:
        return SystemIdentityView(reject_reason="missing_messages")
    if len(messages) > MESSAGE_LIMIT:
        return SystemIdentityView(reject_reason="message_limit")
    if message_field(messages[0], "role") != "system":
        return SystemIdentityView(reject_reason="missing_leading_system")
    first_user = None
    for index, message in enumerate(messages):
        role = message_field(message, "role")
        if role == "developer" or (index > 0 and role == "system"):
            return SystemIdentityView(reject_reason="conflicting_instruction_roles")
        if role == "user" and first_user is None:
            first_user = message
    content = message_field(messages[0], "content")
    if not isinstance(content, str):
        return SystemIdentityView(reject_reason="unsupported_system_content")
    return SystemIdentityView(content[:SYSTEM_PREFIX_LIMIT], first_user)


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
    conversation: list[Any] | None, fallback: str | None
) -> tuple[str, str]:
    """Select existing text only; the fallback must come from native serving."""

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
    if fallback and fallback.strip():
        return fallback, "serving_prompt_fallback"
    return "", "missing"


@dataclass(frozen=True)
class PromptTextViews:
    """Existing request text, never a mutable continuation prompt mirror."""

    classification_text: str
    classification_source: str
    initial_text: str | None
    initial_text_source: str


def extract_prompt_text_views(
    conversation: list[Any] | None, initial_text: str | None
) -> PromptTextViews:
    text, source = visible_user_text(conversation, initial_text)
    return PromptTextViews(
        classification_text=text,
        classification_source=source,
        initial_text=initial_text,
        initial_text_source=(
            "serving_prompt" if initial_text is not None else "missing"
        ),
    )
