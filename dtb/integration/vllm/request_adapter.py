"""Extract algorithm inputs from native vLLM request structures."""
from __future__ import annotations

from typing import Any


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
