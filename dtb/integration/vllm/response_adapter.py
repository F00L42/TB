"""Preserve native vLLM streaming and full-response parser paths."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, TYPE_CHECKING

from dtb.reasoning.request_context import DtbRequestContext

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.protocol import (
        ChatCompletionRequest,
        RequestResponseMetadata,
    )
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


async def stream_queue(
    queue: asyncio.Queue, context: DtbRequestContext
) -> AsyncIterator[RequestOutput]:
    while True:
        item = await queue.get()
        if item is None:
            if context.orchestration_error:
                raise RuntimeError("DTB orchestration failed; see server logs")
            break
        yield item


async def aggregate_queue(
    queue: asyncio.Queue, context: DtbRequestContext
) -> AsyncIterator[RequestOutput]:
    item = await queue.get()
    if item is None:
        if context.orchestration_error:
            raise RuntimeError("DTB orchestration failed; see server logs")
        yield None
        return
    while True:
        following = await queue.get()
        if following is None:
            if context.orchestration_error:
                raise RuntimeError("DTB orchestration failed; see server logs")
            break
        item.outputs[0].text += following.outputs[0].text
        item.outputs[0].token_ids.extend(following.outputs[0].token_ids)
        if following.outputs[0].finish_reason is not None:
            item.outputs[0].finish_reason = following.outputs[0].finish_reason
            item.outputs[0].stop_reason = following.outputs[0].stop_reason
    if item.outputs[0].finish_reason is None:
        item.outputs[0].finish_reason = "stop"
    item.finished = True
    yield item


async def native_chat_response(
    serving_chat: OpenAIServingChat,
    request: ChatCompletionRequest,
    response_generator: AsyncGenerator[RequestOutput, None],
    request_id: str,
    model_name: str,
    conversation: list,
    tokenizer: TokenizerLike,
    request_metadata: RequestResponseMetadata,
    reasoning_parser: Any = None,
    response_parser: Any = None,
    chat_template_kwargs: dict[str, Any] | None = None,
) -> Any:
    if request.stream:
        return serving_chat.chat_completion_stream_generator(
            request,
            response_generator,
            request_id,
            model_name,
            conversation,
            tokenizer,
            request_metadata,
            reasoning_parser=reasoning_parser,
            chat_template_kwargs=chat_template_kwargs,
        )
    try:
        return await serving_chat.chat_completion_full_generator(
            request,
            response_generator,
            request_id,
            model_name,
            conversation,
            tokenizer,
            request_metadata,
            parser=response_parser,
        )
    except (ValueError, RuntimeError) as exc:
        return serving_chat.create_error_response(str(exc))
