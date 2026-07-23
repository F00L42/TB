"""OpenAI-compatible Think Budget API server.

Supported endpoints:
    POST /v1/chat/completions
    POST /v1/completions
    GET  /health
    GET  /v1/config
    POST /v1/config
    GET  /v1/overrides

The request ``model`` field is honored by both budget and non-budget paths.
External requests may only set OpenAI-compatible generation fields,
``enable_budget``, and fixed manual budget controls. Internal budget strategy
parameters remain server-side configuration and are not accepted from callers.

``stream=true`` is supported as OpenAI-compatible Server-Sent Events. The
budget path computes the full budget-controlled answer first, then emits it as
chunks, because confidence checks require complete candidate answers.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import sys
import time
import traceback
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from naie.config.settings import THINK_BUDGET_CONFIG_47
from naie.inference.think_budget import call_model as budget_call_model
from naie.inference.think_budget_stream import call_model_stream as budget_call_model_stream
from naie.models.model_client import model_client, set_httpx_pool_size
from naie.optimizations.history_module import get_category_stats, get_scene_stats
from naie.optimizations.scene_classifier import classify_scene
from naie.optimizations.task_type_budget import classify_task_type
from naie.utils.tokenization import count_tokens

os.environ["NO_PROXY"] = "localhost,127.0.0.1,10.44.84.60,141.73.2.25,71.26.76.45"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

app = FastAPI(
    title="Think Budget API",
    description="OpenAI-compatible proxy with optional Think Budget control.",
)

DEFAULT_MODEL = os.getenv("THINK_BUDGET_DEFAULT_MODEL", "Qwen3.6-27B")
DEFAULT_MAX_TOKENS = int(os.getenv("THINK_BUDGET_DEFAULT_MAX_TOKENS", THINK_BUDGET_CONFIG_47.get("max_tokens", 30000)))
FINAL_ANSWER_TRIGGER = "OK, I have completed my thinking and can now provide the final answer."

PER_REQUEST_FIXED_BUDGET_KEYS = [
    "fixed_budget_tokens",
    "fixed_num_thinking",
    "fixed_think_stride",
    "fixed_check_length",
    "fixed_prob_th",
    "fixed_min_think_tokens",
]

OPENAI_COMPATIBLE_CHAT_FIELDS = [
    "model",
    "messages",
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "n",
    "stream",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "user",
    "seed",
    "response_format",
    "logprobs",
    "top_logprobs",
]

OPENAI_COMPATIBLE_COMPLETION_FIELDS = [
    "model",
    "prompt",
    "suffix",
    "temperature",
    "max_tokens",
    "top_p",
    "n",
    "stream",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "user",
    "seed",
    "echo",
    "logprobs",
]

PUBLIC_EXTENSION_FIELDS = ["enable_budget", "enable_think_budget"]
DISALLOWED_PUBLIC_FIELDS = [
    "custom_task_types_path",
    "scene_type",
    "task_type",
    "default_task_type",
    "check_length",
    "think_stride",
    "num_thinking",
    "check_answer_len",
    "prob_th",
    "min_think_tokens",
    "max_api_calls",
    "enable_value_min_confidence",
    "enable_clean_context",
    "enable_safety_gate",
    "baseline_time_limit",
    "enable_difficulty_estimator",
    "enable_deer_trial",
    "enable_soft_truncation",
    "enable_task_type_budget",
    "enable_history_budget",
    "history_budget_multiplier",
    "history_low_success_boost",
    "history_stride_percentile",
    "new_task_unlimited",
    "history_warmup_count",
    "history_warmup_strategy",
    "auto_cluster_threshold",
    "enable_full_chain_confidence",
    "full_chain_min_percentile",
    "enable_dual_channel",
    "reasoning_min_percentile",
    "consecutive_same_stop",
    "value_min_bottom_k",
]

_inference_semaphore: Optional[asyncio.Semaphore] = None
_server_concurrency = 4


class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: List[Message]
    temperature: Optional[float] = 0
    max_tokens: Optional[int] = DEFAULT_MAX_TOKENS
    max_completion_tokens: Optional[int] = None
    top_p: Optional[float] = None
    n: Optional[int] = None
    stream: Optional[bool] = False
    stop: Optional[Any] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    logit_bias: Optional[Dict[str, float]] = None
    user: Optional[str] = None
    seed: Optional[int] = None
    response_format: Optional[Dict[str, Any]] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    enable_budget: Optional[bool] = False
    enable_think_budget: Optional[bool] = None

    fixed_budget_tokens: int = 0
    fixed_num_thinking: int = 0
    fixed_think_stride: int = 0
    fixed_check_length: int = 0
    fixed_prob_th: float = 0.0
    fixed_min_think_tokens: int = 0

    model_config = {"extra": "forbid"}


class CompletionRequest(BaseModel):
    model: str = DEFAULT_MODEL
    prompt: Any
    suffix: Optional[str] = None
    temperature: Optional[float] = 0
    max_tokens: Optional[int] = DEFAULT_MAX_TOKENS
    max_completion_tokens: Optional[int] = None
    top_p: Optional[float] = None
    n: Optional[int] = None
    stream: Optional[bool] = False
    stop: Optional[Any] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    logit_bias: Optional[Dict[str, float]] = None
    user: Optional[str] = None
    seed: Optional[int] = None
    echo: Optional[bool] = False
    logprobs: Optional[Any] = None
    enable_budget: Optional[bool] = False
    enable_think_budget: Optional[bool] = None

    fixed_budget_tokens: int = 0
    fixed_num_thinking: int = 0
    fixed_think_stride: int = 0
    fixed_check_length: int = 0
    fixed_prob_th: float = 0.0
    fixed_min_think_tokens: int = 0

    model_config = {"extra": "forbid"}


def _conversation_to_prompt(messages: List[Message]) -> str:
    if len(messages) == 1 and messages[0].role == "user":
        return messages[0].content
    return "\n\n".join(f"[{message.role}]\n{message.content}" for message in messages)


def _model_payload(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _copy_request_with_stream_disabled(request: BaseModel) -> Dict[str, Any]:
    payload = _model_payload(request)
    payload["max_tokens"] = _effective_max_tokens(request)
    payload["enable_budget"] = _budget_enabled(request)
    payload["stream"] = False
    return payload


def _effective_model(model_name: Optional[str]) -> str:
    return (model_name or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _fields_set(model: BaseModel) -> set[str]:
    fields = getattr(model, "model_fields_set", None)
    if fields is None:
        fields = getattr(model, "__fields_set__", set())
    return set(fields)


def _effective_max_tokens(request: BaseModel) -> int:
    """Resolve OpenAI token aliases without weakening schema validation.

    ``max_tokens`` remains the canonical server-side field. Chat clients that
    follow newer OpenAI payloads may send ``max_completion_tokens``; it is used
    only when the caller did not explicitly send ``max_tokens``.
    """
    fields_set = _fields_set(request)
    max_tokens = getattr(request, "max_tokens", None)
    max_completion_tokens = getattr(request, "max_completion_tokens", None)

    if "max_tokens" in fields_set and max_tokens is not None:
        return int(max_tokens)
    if max_completion_tokens is not None:
        return int(max_completion_tokens)
    if max_tokens is not None:
        return int(max_tokens)
    return DEFAULT_MAX_TOKENS


def _budget_enabled(request: BaseModel) -> bool:
    """Resolve the canonical budget switch plus its compatibility alias."""
    fields_set = _fields_set(request)
    enable_budget = getattr(request, "enable_budget", None)
    enable_think_budget = getattr(request, "enable_think_budget", None)

    if "enable_budget" in fields_set and enable_budget is not None:
        return bool(enable_budget)
    if enable_think_budget is not None:
        return bool(enable_think_budget)
    return bool(enable_budget)


def _fixed_budget_overrides(request: BaseModel) -> Dict[str, Any]:
    """Return active fixed budget controls from this request.

    Fixed controls use 0/0.0 as the single unset value across API, config, and
    budget core. Only positive values are active overrides.
    """
    overrides: Dict[str, Any] = {}
    for key in PER_REQUEST_FIXED_BUDGET_KEYS:
        value = getattr(request, key, 0)
        if value > 0:
            overrides[key] = value
    return overrides


def _validate_config_update(updates: Dict[str, Any], api_key: Optional[str]) -> None:
    """Protect process-wide settings from unauthenticated updates."""
    expected_key = os.getenv("THINK_BUDGET_ADMIN_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="Runtime configuration updates are disabled")
    if not api_key or not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(status_code=403, detail="Invalid administrator API key")

    probability_keys = {
        "prob_th",
        "fixed_prob_th",
        "CONFIDENCE_THRESHOLD",
        "history_stride_percentile",
        "PERCENTILE_STEP",
    }
    non_negative_keys = {
        "check_length",
        "think_stride",
        "num_thinking",
        "check_answer_len",
        "min_think_tokens",
        "max_api_calls",
        "fixed_budget_tokens",
        "fixed_num_thinking",
        "fixed_think_stride",
        "fixed_check_length",
        "fixed_min_think_tokens",
        "FIXED_BUDGET_TOKEN",
    }

    for key, value in updates.items():
        if key not in THINK_BUDGET_CONFIG_47:
            raise HTTPException(status_code=400, detail=f"Unknown configuration key: {key}")
        current = THINK_BUDGET_CONFIG_47[key]
        if current is not None and type(value) is not type(current):
            raise HTTPException(status_code=400, detail=f"Invalid type for {key}")
        if key in probability_keys and not 0 <= value <= 1:
            raise HTTPException(status_code=400, detail=f"{key} must be between 0 and 1")
        if key in non_negative_keys and value < 0:
            raise HTTPException(status_code=400, detail=f"{key} cannot be negative")


def _completion_prompt_to_text(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list) and all(isinstance(item, str) for item in prompt):
        return "\n\n".join(prompt)
    raise HTTPException(status_code=400, detail="prompt must be a string or a list of strings")


def _completion_request_to_chat_request(request: CompletionRequest, prompt: str) -> ChatCompletionRequest:
    payload = _copy_request_with_stream_disabled(request)
    for key in ("prompt", "suffix", "stream", "echo", "logprobs"):
        payload.pop(key, None)
    payload["messages"] = [Message(role="user", content=prompt)]
    return ChatCompletionRequest(**payload)


def _completion_response_from_chat(
    chat_response: Dict[str, Any],
    prompt: str,
    request: CompletionRequest,
) -> Dict[str, Any]:
    chat_choice = chat_response["choices"][0]
    message = chat_choice.get("message", {})
    text = message.get("content", "")
    if request.echo:
        text = prompt + text

    return {
        "id": chat_response.get("id", f"cmpl-{hash(prompt) % 1000000}"),
        "object": "text_completion",
        "created": chat_response.get("created", int(time.time())),
        "model": chat_response.get("model", _effective_model(request.model)),
        "choices": [
            {
                "text": text,
                "index": chat_choice.get("index", 0),
                "logprobs": None,
                "reasoning_content": message.get("reasoning_content"),
                "finish_reason": chat_choice.get("finish_reason", "stop"),
            }
        ],
        "usage": chat_response.get("usage", {}),
        "budget_info": chat_response.get("budget_info"),
    }


def _strip_internal_markers(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    for marker in (
        "<|im_start|>assistant",
        "<|im_start|>user",
        "<|im_end|>",
        "<think>",
        "</think>",
    ):
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()


def _clean_final_answer(text: str) -> str:
    """Remove internal budget-control prompt fragments from final answer text."""
    if not text:
        return ""

    answer = text.strip()
    if FINAL_ANSWER_TRIGGER in answer:
        answer = answer.rsplit(FINAL_ANSWER_TRIGGER, 1)[-1].strip()
    if "</think>" in answer:
        answer = answer.rsplit("</think>", 1)[-1].strip()
    if "<|im_start|>assistant" in answer:
        answer = answer.rsplit("<|im_start|>assistant", 1)[-1].strip()

    return _strip_internal_markers(answer)


def parse_think_content(full_content: str) -> tuple[str, str]:
    """Split model output into private reasoning and public final-answer text.

    Budget forcing often returns the whole internal transcript:
    user prompt + ``<think>`` + reasoning + induced final-answer prompt +
    candidate answers. Public API responses must not expose that transcript in
    ``message.content``. This parser keeps reasoning separate and extracts the
    last final-answer segment after the internal trigger.
    """
    if not full_content:
        return "", ""

    content = full_content.strip()
    if "<|im_start|>assistant" in content:
        content = content.rsplit("<|im_start|>assistant", 1)[-1].strip()

    if "</think>" in content:
        think_part, answer_part = content.split("</think>", 1)
        if "<think>" in think_part:
            think_content = think_part.split("<think>", 1)[1].strip()
        else:
            think_content = think_part.strip()
        return _strip_internal_markers(think_content), _clean_final_answer(answer_part)

    if FINAL_ANSWER_TRIGGER in content:
        think_part, answer_part = content.rsplit(FINAL_ANSWER_TRIGGER, 1)
        return _strip_internal_markers(think_part), _clean_final_answer(answer_part)

    if "<think>" in content:
        think_content = content.split("<think>", 1)[1].strip()
        return _strip_internal_markers(think_content), ""

    if content.startswith("Here's a thinking process:"):
        return _strip_internal_markers(content), ""
    else:
        return "", _clean_final_answer(content)


def _compute_thinking_tokens(text: str) -> int:
    return count_tokens(text)


def _text_chunks(text: Optional[str], chunk_size: int = 128) -> Iterable[str]:
    if not text:
        return
    for start in range(0, len(text), chunk_size):
        yield text[start:start + chunk_size]


def _sse_data(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


def _stream_headers() -> Dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


_STREAM_END = object()


def _non_budget_chat_prompt(prompt: str) -> str:
    return (
        "<|im_start|>user\n"
        + prompt
        + "<|im_end|>\n<|im_start|>assistant\n<think>\n"
    )


def _next_stream_item(iterator):
    return next(iterator, _STREAM_END)


async def _iter_non_budget_model_stream(
    prompt: str,
    max_tokens: int,
    model_name: str,
) -> AsyncIterator[Dict[str, Any]]:
    def _make_iterator():
        client = model_client(model_name)
        return iter(client.model_call_stream(
            _non_budget_chat_prompt(prompt),
            max_tokens,
        ))

    iterator = await asyncio.to_thread(_make_iterator)
    while True:
        item = await asyncio.to_thread(_next_stream_item, iterator)
        if item is _STREAM_END:
            break
        yield item


async def _run_chat_with_semaphore(request: ChatCompletionRequest) -> Dict[str, Any]:
    if _inference_semaphore is None:
        _init_semaphore()
    async with _inference_semaphore:
        return await _do_chat_completions(request)


async def _chat_stream_events(request: ChatCompletionRequest) -> AsyncIterator[str]:
    prompt = _conversation_to_prompt(request.messages)
    max_tokens = _effective_max_tokens(request)
    model_name = _effective_model(request.model)
    if not _budget_enabled(request):
        chunk_base = {
            "id": f"chatcmpl-{hash(prompt) % 1000000}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
        }
        if _inference_semaphore is None:
            _init_semaphore()
        async with _inference_semaphore:
            yield _sse_data({
                **chunk_base,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            })
            finish_reason = "stop"
            async for item in _iter_non_budget_model_stream(prompt, max_tokens, model_name):
                piece = item.get("content", "")
                if piece:
                    yield _sse_data({
                        **chunk_base,
                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                    })
                if item.get("finish_reason"):
                    finish_reason = item["finish_reason"]
            yield _sse_data({
                **chunk_base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            })
            yield _sse_done()
        return

    scene_info = classify_scene(prompt)
    cfg, effective_scene_type, classified_task_type = _build_budget_config(
        request,
        prompt,
        scene_info,
    )
    chunk_base = {
        "id": f"chatcmpl-{hash(prompt) % 1000000}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
    }
    if _inference_semaphore is None:
        _init_semaphore()
    async with _inference_semaphore:
        yield _sse_data({
            **chunk_base,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        })
        final_usage = None
        final_budget_info = None
        async for event in budget_call_model_stream(
            prompt=prompt,
            max_tokens=max_tokens,
            budget_config=cfg,
            reference="",
        ):
            event_type = event.get("type")
            if event_type == "reasoning_delta":
                yield _sse_data({
                    **chunk_base,
                    "choices": [{"index": 0, "delta": {"reasoning_content": event.get("content", "")}, "finish_reason": None}],
                })
            elif event_type == "content_delta":
                yield _sse_data({
                    **chunk_base,
                    "choices": [{"index": 0, "delta": {"content": event.get("content", "")}, "finish_reason": None}],
                })
            elif event_type == "done":
                final_usage = event.get("usage")
                final_budget_info = event.get("budget_info", {})
                final_budget_info.update({
                    "difficulty": final_budget_info.get("difficulty"),
                    "scene_type": effective_scene_type,
                    "scene_confidence": scene_info.get("scene_confidence", 0),
                    "task_type": classified_task_type,
                    "model": model_name,
                    "fixed_budget_mode": _fixed_budget_overrides(request).get("fixed_budget_tokens", 0) > 0,
                    "fixed_overrides": _fixed_budget_overrides(request),
                })

        final_payload = {
            **chunk_base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        if final_usage is not None:
            final_payload["usage"] = final_usage
        if final_budget_info is not None:
            final_payload["budget_info"] = final_budget_info
        yield _sse_data(final_payload)
        yield _sse_done()


async def _completion_stream_events(request: CompletionRequest, prompt: str) -> AsyncIterator[str]:
    if not _budget_enabled(request):
        max_tokens = _effective_max_tokens(request)
        model_name = _effective_model(request.model)
        chunk_base = {
            "id": f"cmpl-{hash(prompt) % 1000000}",
            "object": "text_completion",
            "created": int(time.time()),
            "model": model_name,
        }
        if _inference_semaphore is None:
            _init_semaphore()
        async with _inference_semaphore:
            if request.echo:
                yield _sse_data({
                    **chunk_base,
                    "choices": [{
                        "text": prompt,
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": None,
                    }],
                })
            finish_reason = "stop"
            async for item in _iter_non_budget_model_stream(prompt, max_tokens, model_name):
                piece = item.get("content", "")
                if piece:
                    yield _sse_data({
                        **chunk_base,
                        "choices": [{
                            "text": piece,
                            "index": 0,
                            "logprobs": None,
                            "finish_reason": None,
                        }],
                    })
                if item.get("finish_reason"):
                    finish_reason = item["finish_reason"]
            yield _sse_data({
                **chunk_base,
                "choices": [{
                    "text": "",
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": finish_reason,
                }],
            })
            yield _sse_done()
        return

    chat_request = _completion_request_to_chat_request(request, prompt)
    scene_info = classify_scene(prompt)
    cfg, effective_scene_type, classified_task_type = _build_budget_config(
        chat_request,
        prompt,
        scene_info,
    )
    max_tokens = _effective_max_tokens(request)
    model_name = _effective_model(request.model)
    chunk_base = {
        "id": f"cmpl-{hash(prompt) % 1000000}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model_name,
    }
    if _inference_semaphore is None:
        _init_semaphore()
    async with _inference_semaphore:
        if request.echo:
            yield _sse_data({
                **chunk_base,
                "choices": [{"text": prompt, "index": 0, "logprobs": None, "finish_reason": None}],
            })
        final_usage = None
        final_budget_info = None
        async for event in budget_call_model_stream(
            prompt=prompt,
            max_tokens=max_tokens,
            budget_config=cfg,
            reference="",
        ):
            event_type = event.get("type")
            if event_type == "reasoning_delta":
                yield _sse_data({
                    **chunk_base,
                    "choices": [{
                        "text": "",
                        "index": 0,
                        "logprobs": None,
                        "reasoning_content": event.get("content", ""),
                        "finish_reason": None,
                    }],
                })
            elif event_type == "content_delta":
                yield _sse_data({
                    **chunk_base,
                    "choices": [{
                        "text": event.get("content", ""),
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": None,
                    }],
                })
            elif event_type == "done":
                final_usage = event.get("usage")
                final_budget_info = event.get("budget_info", {})
                final_budget_info.update({
                    "scene_type": effective_scene_type,
                    "scene_confidence": scene_info.get("scene_confidence", 0),
                    "task_type": classified_task_type,
                    "model": model_name,
                    "fixed_budget_mode": _fixed_budget_overrides(chat_request).get("fixed_budget_tokens", 0) > 0,
                    "fixed_overrides": _fixed_budget_overrides(chat_request),
                })

        final_payload = {
            **chunk_base,
            "choices": [{"text": "", "index": 0, "logprobs": None, "finish_reason": "stop"}],
        }
        if final_usage is not None:
            final_payload["usage"] = final_usage
        if final_budget_info is not None:
            final_payload["budget_info"] = final_budget_info
        yield _sse_data(final_payload)
        yield _sse_done()


def _init_semaphore() -> None:
    global _inference_semaphore
    _inference_semaphore = asyncio.Semaphore(_server_concurrency)
    logger.info("API Server concurrency=%s", _server_concurrency)


@app.on_event("startup")
async def startup_event():
    _init_semaphore()
    pool_size = _server_concurrency * 4
    set_httpx_pool_size(
        max_connections=pool_size,
        max_keepalive_connections=_server_concurrency * 2,
    )


def _build_budget_config(request: ChatCompletionRequest, prompt: str, scene_info: Dict[str, Any]) -> tuple[Dict[str, Any], str, str]:
    effective_scene_type = scene_info.get("scene_type", "general")
    task_info = classify_task_type(prompt, scene_info)
    classified_task_type = task_info.get("task_type", "general")

    cat_stats = get_category_stats(
        prompt[:100],
        task_type=classified_task_type,
        scene_type=effective_scene_type,
    )
    scene_stats = get_scene_stats(effective_scene_type, task_type=classified_task_type)
    history_avg_tokens = cat_stats.get("avg_tokens", 0) or scene_stats.get("avg_tokens", 0)
    history_failure_rate = cat_stats.get("failure_rate", 0) or scene_stats.get("failure_rate", 0)
    history_count = cat_stats.get("count", 0) or scene_stats.get("count", 0)

    cfg = dict(THINK_BUDGET_CONFIG_47)
    cfg["model_name"] = _effective_model(request.model)
    cfg.update(_fixed_budget_overrides(request))

    cfg["history_avg_tokens"] = history_avg_tokens
    cfg["history_failure_rate"] = history_failure_rate
    cfg["history_category_avg_tokens"] = cat_stats.get("avg_tokens", 0)
    cfg["history_category_failure_rate"] = cat_stats.get("failure_rate", 0)
    cfg["history_count"] = history_count
    return cfg, effective_scene_type, classified_task_type


async def _call_non_budget_model(request: ChatCompletionRequest, prompt: str, max_tokens: int, model_name: str) -> Dict[str, Any]:
    def _sync_call():
        client = model_client(model_name)
        chat_prompt = _non_budget_chat_prompt(prompt)
        return client.model_call(chat_prompt, max_tokens), chat_prompt

    response, _chat_prompt = await asyncio.to_thread(_sync_call)
    raw_content = response.get("content", "")
    thinking_content, answer_content = parse_think_content(raw_content)
    reasoning_tokens = _compute_thinking_tokens(thinking_content)
    display_content = answer_content if answer_content else raw_content
    return {
        "id": f"chatcmpl-{hash(prompt) % 1000000}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": display_content,
                    "reasoning_content": thinking_content if thinking_content else None,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": response.get("prompt_tokens", 0),
            "completion_tokens": response.get("token_num", 0),
            "reasoning_tokens": reasoning_tokens,
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")
    if request.stream:
        if request.messages[-1].role != "user":
            raise HTTPException(status_code=400, detail="last message must be from user")
        return StreamingResponse(
            _chat_stream_events(request),
            media_type="text/event-stream",
            headers=_stream_headers(),
        )
    if request.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="last message must be from user")

    return await _run_chat_with_semaphore(request)


async def _do_chat_completions(request: ChatCompletionRequest):
    try:
        prompt = _conversation_to_prompt(request.messages)
        max_tokens = _effective_max_tokens(request)
        model_name = _effective_model(request.model)
        enable_budget = _budget_enabled(request)
        fixed_overrides = _fixed_budget_overrides(request)

        if not enable_budget:
            return await _call_non_budget_model(request, prompt, max_tokens, model_name)

        scene_info = classify_scene(prompt)

        cfg, effective_scene_type, classified_task_type = _build_budget_config(
            request,
            prompt,
            scene_info,
        )

        start_time = time.time()
        result = await asyncio.to_thread(
            budget_call_model,
            prompt=prompt,
            max_tokens=max_tokens,
            budget_config=cfg,
            reference="",
        )
        elapsed_time = time.time() - start_time

        content = result.get("content", "")
        thinking_content, answer_content = parse_think_content(content)
        reasoning_tokens = result.get("thinking_tokens")
        if reasoning_tokens is None:
            reasoning_tokens = _compute_thinking_tokens(thinking_content)
        display_content = answer_content if answer_content else _clean_final_answer(content)

        response = {
            "id": f"chatcmpl-{hash(prompt) % 1000000}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": display_content,
                        "reasoning_content": thinking_content if thinking_content else None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": result.get("prompt_tokens", 0),
                "completion_tokens": result.get("token_num", 0),
                "reasoning_tokens": reasoning_tokens,
            },
            "budget_info": {
                "accept_reason": result.get("accept_reason", ""),
                "confidence": result.get("confidence"),
                "perplexity": result.get("perplexity"),
                "value_min_confidence": result.get("value_min_confidence"),
                "api_call_count": result.get("api_call_count", 0),
                "thinking_rounds": result.get("thinking_rounds", 0),
                "thinking_tokens": reasoning_tokens,
                "actual_len": result.get("actual_len"),
                "budget_plan": result.get("budget_plan"),
                "elapsed_time": round(elapsed_time, 2),
                "difficulty": result.get("complexity"),
                "scene_type": effective_scene_type,
                "scene_confidence": scene_info.get("scene_confidence", 0),
                "task_type": classified_task_type,
                "model": model_name,
                "fixed_budget_mode": fixed_overrides.get("fixed_budget_tokens", 0) > 0,
                "fixed_overrides": fixed_overrides,
            },
        }
        logger.info(
            "Budget result model=%s accept=%s think=%s elapsed=%.1fs scene=%s",
            model_name,
            result.get("accept_reason", ""),
            reasoning_tokens,
            elapsed_time,
            effective_scene_type,
        )
        return response

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - expose useful API error type.
        logger.error("chat_completions error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/v1/completions")
async def completions(request: CompletionRequest):
    prompt = _completion_prompt_to_text(request.prompt)
    if request.suffix:
        prompt = f"{prompt}{request.suffix}"

    if request.stream:
        return StreamingResponse(
            _completion_stream_events(request, prompt),
            media_type="text/event-stream",
            headers=_stream_headers(),
        )

    chat_response = await _run_chat_with_semaphore(_completion_request_to_chat_request(request, prompt))
    return _completion_response_from_chat(chat_response, prompt, request)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "default_model": DEFAULT_MODEL,
        "default_max_tokens": DEFAULT_MAX_TOKENS,
        "default_enable_budget": False,
        "concurrency": _server_concurrency,
        "semaphore_available": _inference_semaphore._value if _inference_semaphore else None,
    }


@app.get("/v1/overrides")
async def get_overrides():
    return {
        "default_model": DEFAULT_MODEL,
        "default_max_tokens": DEFAULT_MAX_TOKENS,
        "default_enable_budget": False,
        "openai_compatible_chat_fields": OPENAI_COMPATIBLE_CHAT_FIELDS,
        "openai_compatible_completion_fields": OPENAI_COMPATIBLE_COMPLETION_FIELDS,
        "extension_fields": PUBLIC_EXTENSION_FIELDS,
        "fixed_budget_fields": PER_REQUEST_FIXED_BUDGET_KEYS,
        "disallowed_public_fields": DISALLOWED_PUBLIC_FIELDS,
        "runtime_config_endpoint": {
            "path": "/v1/config",
            "method": "POST",
            "requires_header": "X-API-Key",
            "allowed_keys": list(THINK_BUDGET_CONFIG_47.keys()),
        },
    }


@app.get("/v1/config")
async def get_config():
    return {"config": THINK_BUDGET_CONFIG_47}


@app.post("/v1/config")
async def update_config(updates: Dict[str, Any], x_api_key: Optional[str] = Header(default=None)):
    from naie.config.settings import set_value

    _validate_config_update(updates, x_api_key)
    for key, value in updates.items():
        set_value(key, value)
    return {"config": THINK_BUDGET_CONFIG_47}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Think Budget API Server")
    parser.add_argument("--port", type=int, default=8000, help="server port")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="bind address")
    parser.add_argument("--concurrency", type=int, default=4, help="max concurrent inference requests")
    parser.add_argument("--workers", type=int, default=1, help="uvicorn worker processes")
    args = parser.parse_args()

    _server_concurrency = args.concurrency
    print(
        f"[API Server] host={args.host} port={args.port} "
        f"model={DEFAULT_MODEL} concurrency={args.concurrency} workers={args.workers}"
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=args.workers)
