"""Small vLLM v0.23.0 adapter; core imports stay dependency-free."""
from __future__ import annotations

import copy
import importlib
import logging
import os
from contextlib import aclosing

from .api import BoundaryProtocol, ObserverContext
from .config import ObserverConfig
from .controller import ThinkingObserver

LOG = logging.getLogger(__name__)


def capability_rejection(request, prompt, params, engine, reasoning_ended):
    if reasoning_ended is not False:
        return "reasoning_not_open"
    if getattr(request, "use_beam_search", False) or getattr(params, "n", 1) != 1:
        return "multiple_choices_or_beam"
    if getattr(request, "continue_final_message", False):
        return "continue_final_message"
    if getattr(request, "truncate_prompt_tokens", None) is not None:
        return "prompt_truncation"
    if getattr(request, "kv_transfer_params", None):
        return "kv_transfer"
    if getattr(getattr(engine, "vllm_config", None), "kv_transfer_config", None) is not None:
        return "kv_transfer_connector"
    if not callable(getattr(engine, "abort", None)):
        return "missing_abort"
    # Continuation support deliberately excludes non-text and hidden engine state.
    if set(prompt) - {"type", "prompt", "prompt_token_ids", "cache_salt"}:
        return "unsupported_prompt_fields"
    if prompt.get("type", "token") not in ("token", "tokens"):
        return "non_text_prompt"
    if not isinstance(prompt.get("prompt_token_ids"), (list, tuple)):
        return "missing_prompt_ids"
    if getattr(params, "stop", None):
        return "user_string_stop"
    if getattr(params, "min_tokens", 0):
        return "min_tokens_state"
    for field, default in (("presence_penalty", 0), ("frequency_penalty", 0),
                           ("repetition_penalty", 1)):
        if getattr(params, field, default) != default:
            return "penalty_state"
    for field in ("structured_outputs", "logits_processors", "extra_args",
                  "allowed_token_ids", "bad_words", "logit_bias", "thinking_token_budget"):
        if getattr(params, field, None):
            return "sampling_state_" + field
    # Synthetic boundaries have no model logprob; never fabricate one.
    if (getattr(params, "logprobs", None) is not None
            or getattr(params, "prompt_logprobs", None) is not None):
        return "logprobs"
    if not getattr(params, "detokenize", True):
        return "detokenization_disabled"
    return None


def bind_qwen3(tokenizer, reasoning_parser, response_parser):
    """Bind IDs from the active vocabulary, never from model-specific constants."""
    if type(reasoning_parser).__name__ != "Qwen3ReasoningParser":
        raise ValueError("unsupported_reasoning_parser")
    reasoner = getattr(response_parser, "reasoning_parser", None)
    tool = getattr(response_parser, "tool_parser", None)
    if type(reasoner).__name__ != "Qwen3ReasoningParser":
        raise ValueError("unsupported_response_parser")
    if tool is not None and type(tool).__name__ != "Qwen3CoderToolParser":
        raise ValueError("unsupported_tool_parser")
    vocab = tokenizer.get_vocab()
    end, start = vocab.get("</think>"), vocab.get("<tool_call>")
    if type(end) is not int or type(start) is not int or end == start:
        raise ValueError("missing_boundary_ids")
    for marker, expected in (("</think>", end), ("<tool_call>", start)):
        if list(tokenizer.encode(marker, add_special_tokens=False)) != [expected]:
            raise ValueError("noncanonical_boundary_encoding")
    for parser in (reasoning_parser, reasoner):
        if getattr(parser, "end_token_id", None) != end:
            raise ValueError("reasoning_end_id_mismatch")
        if not getattr(parser, "thinking_enabled", True):
            raise ValueError("thinking_disabled")
        for token in (end, start):
            if not parser.is_reasoning_end_streaming([], [token]):
                raise ValueError("parser_boundary_mismatch")
    if tool is not None and getattr(tool, "tool_call_start_token_id", None) != start:
        raise ValueError("tool_start_id_mismatch")
    return BoundaryProtocol(end, start)


async def aggregate_deltas(source):
    """Produce ONE cumulative result for native full-response parsing."""
    result = None
    texts, ids = [], []
    async with aclosing(source):
        async for item in source:
            if result is not None and result.finished:
                raise RuntimeError("output after aggregate terminal")
            result = copy.copy(item)
            result.outputs = [copy.copy(item.outputs[0])]
            texts.append(item.outputs[0].text)
            ids.extend(item.outputs[0].token_ids)
    if result is None or not result.finished:
        raise RuntimeError("aggregation ended without terminal")
    result.outputs[0].text = "".join(texts)
    result.outputs[0].token_ids = ids
    yield result


async def close_with_response(response, source):
    """Tie native SSE consumer cancellation/close to the engine lifecycle."""
    async with aclosing(source), aclosing(response):
        async for item in response:
            yield item


class VllmObserver:
    def __init__(self, observer: ThinkingObserver | None = None):
        self.observer = observer

    @property
    def enabled(self):
        return self.observer is not None and self.observer.config.enabled

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("THINKING_OBSERVER_ENABLED", "0")
        if enabled not in ("0", "1"):
            raise ValueError("THINKING_OBSERVER_ENABLED must be 0 or 1")
        if enabled == "0":
            return cls()  # No factory import/creation while disabled.
        spec = os.environ.get("THINKING_OBSERVER_FACTORY",
                              "thinking_observer.detectors:NoopDetector")
        module, separator, name = spec.partition(":")
        if not separator or not module or not name:
            raise ValueError("THINKING_OBSERVER_FACTORY must be module:factory")
        factory = getattr(importlib.import_module(module), name)
        detector = factory()
        if not callable(getattr(detector, "start", None)):
            raise TypeError("factory must return a Detector")
        config = ObserverConfig(
            enabled=True,
            answer_cue=os.environ.get("THINKING_OBSERVER_CUE", ObserverConfig.answer_cue),
            callback_limit_ms=float(os.environ.get("THINKING_OBSERVER_CALLBACK_MS", "10")),
            abort_timeout_s=float(os.environ.get("THINKING_OBSERVER_ABORT_TIMEOUT_S", "10")),
            telemetry=os.environ.get("THINKING_OBSERVER_TELEMETRY", "off"),
        )
        return cls(ThinkingObserver(detector, config))

    def prepare(self, *, request, prompt, params, engine, reasoning_ended,
                tokenizer, reasoning_parser, response_parser, request_id,
                model, max_model_len, data_parallel_rank=None):
        """Return context or None BEFORE engine submission/detector.start."""
        if not self.enabled or not getattr(request, "enable_thinking_observer", False):
            return None
        rejection = capability_rejection(request, prompt, params, engine, reasoning_ended)
        parallel = getattr(getattr(engine, "vllm_config", None), "parallel_config", None)
        if (getattr(parallel, "data_parallel_size", 1) > 1
                and data_parallel_rank is None):
            rejection = "unpinned_data_parallel_rank"
        protocol = None
        if rejection is None:
            try:
                protocol = bind_qwen3(tokenizer, reasoning_parser, response_parser)
            except ValueError as exc:
                rejection = str(exc)
        if rejection:
            # Requested-but-unsupported must be visible even when telemetry=off.
            LOG.warning("THINKING_OBSERVER_FALLBACK request_id=%s reason=%s",
                        request_id, rejection)
            return None
        return ObserverContext(request_id, model, protocol, params.max_tokens,
                               max_model_len, len(prompt["prompt_token_ids"]), tokenizer)

    def generate(self, engine, prompt, params, context, engine_kwargs, *, stream):
        from vllm.sampling_params import RequestOutputKind

        delta_params = copy.copy(params)
        delta_params.output_kind = RequestOutputKind.DELTA
        source = self.observer.stream(engine, prompt, delta_params, context, engine_kwargs)
        return source if stream else aggregate_deltas(source)
