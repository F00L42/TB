"""vLLM route context and per-request Think Budget algorithm state."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, TYPE_CHECKING

from think_budget_vllm_adapter.context import (
    AlgorithmConfig,
    BudgetPlan,
    PerformancePolicy,
)
from think_budget_vllm_adapter.telemetry import TimingCollector

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.protocol import (
        ChatCompletionRequest,
    )
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


class ReasoningState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class FinalAnswerReason(str, Enum):
    """Select the prompt suffix used to enter fresh final generation."""

    CONFIDENCE_ACCEPTED = "confidence_accepted"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass
class TrialObservation:
    """Complete hidden trial output, independent of external stream mode."""

    full_text: str = ""
    all_token_ids: list[int] = field(default_factory=list)
    sample_logprobs: list[Any | None] = field(default_factory=list)
    sampled_token_ids: list[int] = field(default_factory=list)
    generated_tokens: int = 0
    finish_reason: Any = None
    stop_reason: Any = None
    last_output: Any = None
    output_kind: str = "DELTA"


@dataclass
class RequestAlgorithmState:
    reasoning_state: ReasoningState = ReasoningState.OPEN
    attempted_early_stop: bool = False
    thinking_tokens: int = 0
    probe_round: int = 0
    current_trial: TrialObservation | None = None
    last_confidence: float | None = None
    api_call_count: int = 0
    budget_plan: BudgetPlan | None = None
    completion_status: str = "running"
    hidden_probe_tokens: int = 0
    final_answer_tokens: int = 0
    thinking_segments: int = 0
    final_answer_reason: FinalAnswerReason | None = None


@dataclass
class RequestStateContext:
    """One parent request plus the native vLLM identity of all child calls."""

    request: ChatCompletionRequest
    tokenizer: TokenizerLike
    config: AlgorithmConfig
    policy: PerformancePolicy
    serving_chat: OpenAIServingChat
    request_id: str
    engine_prompt: dict[str, Any]
    algorithm_state: RequestAlgorithmState

    lora_request: Any = None
    data_parallel_rank: Optional[int] = None
    trace_headers: Optional[Mapping[str, str]] = None
    prompt_text: Optional[str] = None
    reasoning_ended: Optional[bool] = None
    reasoning_parser_kwargs: Optional[dict[str, Any]] = None
    reasoning_parser: Any = None
    chat_template_kwargs: Optional[dict[str, Any]] = None

    result_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    last_chunk: Optional[RequestOutput] = None
    think_end_emitted: bool = False
    prompt_suffix_token_cache: dict[str, tuple[int, ...]] = field(
        default_factory=dict)
    parser_structural_token_ids: dict[int, str] | None = None
    stream_debug_sequence: int = 0
    stream_debug_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "TB_TOOL_STREAM_DEBUG", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
    )

    model_name: Optional[str] = None
    session_id: Optional[str] = None
    timing: TimingCollector = field(default_factory=TimingCollector)
    orchestration_error: Optional[str] = None
    budget_start_time: float = field(default_factory=time.time)
    question_text: str = ""

    @property
    def engine_client(self):
        return self.serving_chat.engine_client

    @property
    def default_sampling_params(self):
        return self.serving_chat.default_sampling_params

    @property
    def budget_plan(self) -> BudgetPlan:
        plan = self.algorithm_state.budget_plan
        if plan is None:
            raise RuntimeError("BudgetPlan was not attached to request state")
        return plan

    @property
    def trial_count(self) -> int:
        """Compatibility name used only by low-cardinality stage logging."""
        return self.algorithm_state.probe_round

    @property
    def api_call_count(self) -> int:
        return self.algorithm_state.api_call_count

    @property
    def thinking_rounds(self) -> int:
        return self.algorithm_state.thinking_segments

    def engine_request_kwargs(
        self,
        *,
        prompt_text: Optional[str] = None,
        reasoning_ended: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Return native route identity for every child ``generate`` call."""
        return {
            "lora_request": self.lora_request,
            "priority": getattr(self.request, "priority", None) or 0,
            "data_parallel_rank": self.data_parallel_rank,
            "trace_headers": self.trace_headers,
            "prompt_text": (
                self.prompt_text if prompt_text is None else prompt_text
            ),
            "reasoning_ended": (
                self.reasoning_ended
                if reasoning_ended is None else reasoning_ended
            ),
            "reasoning_parser_kwargs": self.reasoning_parser_kwargs,
        }

    def _get_parser_structural_token_ids(self) -> dict[int, str]:
        cached = self.parser_structural_token_ids
        if cached is not None:
            return cached
        try:
            vocab = self.tokenizer.get_vocab()
        except (AttributeError, TypeError):
            vocab = {}
        cached = {
            token_id: tag
            for tag in ("<tool_call>", "</tool_call>")
            if isinstance((token_id := vocab.get(tag)), int)
        }
        self.parser_structural_token_ids = cached
        return cached

    def _restore_parser_visible_tool_text(
        self,
        text: str,
        token_ids: list[int],
    ) -> tuple[str, list[str], str | None]:
        """Restore a structural tag omitted from parser-facing delta text.

        Qwen tool parsers use token IDs to detect the start marker and the
        accumulated text to parse the following XML.  A speculative batch may
        contain a structural token while incremental detokenization exposes
        only a suffix such as ``"f"``.  In that case the parser can enter its
        tool state and immediately lose it again on the next empty-text reset.

        The repair is deliberately conservative: it is attempted only when a
        known structural token ID occurs, its literal tag is absent from the
        supplied text, and decoding the same token batch with special tokens
        retained produces a string ending in the original text.
        """
        structural_ids = self._get_parser_structural_token_ids()
        missing_tags = [
            structural_ids[token_id]
            for token_id in token_ids
            if token_id in structural_ids
            and structural_ids[token_id] not in text
        ]
        if not missing_tags:
            return text, [], None
        try:
            decoded = self.tokenizer.decode(
                token_ids, skip_special_tokens=False)
        except (AttributeError, TypeError, ValueError):
            return text, [], None
        if not isinstance(decoded, str):
            return text, [], None
        if not all(tag in decoded for tag in missing_tags):
            return text, [], decoded
        if text and not decoded.endswith(text):
            return text, [], decoded
        return decoded, list(dict.fromkeys(missing_tags)), decoded

    @staticmethod
    def _stream_text_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _log_stream_delta(
        self,
        *,
        result: Any,
        phase: str,
        child_request_id: str,
        raw_text: str,
        parser_text: str,
        token_ids: list[int],
        decoded_text: str | None,
        repaired_tags: list[str],
    ) -> None:
        if not self.stream_debug_enabled:
            return
        self.stream_debug_sequence += 1
        try:
            output = result.outputs[0]
        except (AttributeError, IndexError, TypeError):
            output = None
        record = {
            "schema": 1,
            "source": "controller_pre_parser",
            "parent_request_id": self.request_id,
            "child_request_id": child_request_id,
            "phase": phase,
            "sequence": self.stream_debug_sequence,
            "raw_text": raw_text,
            "parser_text": parser_text,
            "decoded_token_text": decoded_text,
            "raw_text_sha256": self._stream_text_digest(raw_text),
            "parser_text_sha256": self._stream_text_digest(parser_text),
            "token_ids": token_ids,
            "repaired_tags": repaired_tags,
            "finish_reason": getattr(output, "finish_reason", None),
            "stop_reason": getattr(output, "stop_reason", None),
        }
        print(
            "TB_TOOL_STREAM "
            + json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )

    async def stream_output(
        self,
        result: Any,
        *,
        phase: str = "controller",
        child_request_id: str = "",
    ) -> None:
        if result is None:
            if self.stream_debug_enabled:
                self._log_stream_delta(
                    result=result,
                    phase=phase,
                    child_request_id=child_request_id,
                    raw_text="",
                    parser_text="",
                    token_ids=[],
                    decoded_text=None,
                    repaired_tags=[],
                )
            await self.result_queue.put(None)
            return

        try:
            output = result.outputs[0]
            raw_text = output.text or ""
            token_ids = list(output.token_ids or [])
        except (AttributeError, IndexError, TypeError):
            raw_text = ""
            token_ids = []

        parser_text = raw_text
        repaired_tags: list[str] = []
        decoded_text: str | None = None
        if phase == "final_answer" and getattr(self.request, "tools", None):
            parser_text, repaired_tags, decoded_text = (
                self._restore_parser_visible_tool_text(raw_text, token_ids)
            )
            if repaired_tags:
                prepared_result = copy.copy(result)
                prepared_output = copy.copy(output)
                prepared_result.outputs = [
                    prepared_output,
                    *result.outputs[1:],
                ]
                prepared_output.text = parser_text
                result = prepared_result

        self._log_stream_delta(
            result=result,
            phase=phase,
            child_request_id=child_request_id,
            raw_text=raw_text,
            parser_text=parser_text,
            token_ids=token_ids,
            decoded_text=decoded_text,
            repaired_tags=repaired_tags,
        )
        await self.result_queue.put(result)

    def encode_prompt_suffix(self, text: str) -> tuple[int, ...]:
        cached = self.prompt_suffix_token_cache.get(text)
        if cached is None:
            cached = tuple(
                self.tokenizer.encode(text, add_special_tokens=False))
            self.prompt_suffix_token_cache[text] = cached
        return cached

    def child_prompt(self, suffix_text: str) -> dict[str, Any]:
        """Shallow-copy the main prompt; hidden trials never mutate it."""
        child = self.engine_prompt.copy()
        child["prompt_token_ids"] = [
            *self.engine_prompt.get("prompt_token_ids", ()),
            *self.encode_prompt_suffix(suffix_text),
        ]
        if self.prompt_text is not None:
            child["prompt"] = self.prompt_text + suffix_text
        return child

    def append_prompt_suffix(self, suffix_text: str) -> None:
        if not suffix_text:
            return
        self.engine_prompt["prompt_token_ids"].extend(
            self.encode_prompt_suffix(suffix_text))
        if self.prompt_text is not None:
            self.prompt_text += suffix_text
            self.engine_prompt["prompt"] = self.prompt_text

    def append_thinking(
        self,
        token_ids: list[int],
        text: str,
        last_output: RequestOutput,
    ) -> None:
        if self.algorithm_state.reasoning_state is ReasoningState.CLOSED:
            raise RuntimeError("cannot append thinking after reasoning is closed")
        self.engine_prompt["prompt_token_ids"].extend(token_ids)
        if self.prompt_text is not None and text:
            self.prompt_text += text
            self.engine_prompt["prompt"] = self.prompt_text
        self.algorithm_state.thinking_tokens += len(token_ids)
        self.algorithm_state.thinking_segments += 1

    def close_reasoning(self, prompt_suffix: str = "</think>") -> bool:
        """Close once using the FinalAnswer-selected model prompt suffix."""
        if self.algorithm_state.reasoning_state is ReasoningState.CLOSED:
            return False
        if prompt_suffix.count("</think>") != 1:
            raise ValueError(
                "reasoning close suffix must contain exactly one </think>")
        self.append_prompt_suffix(prompt_suffix)
        self.algorithm_state.reasoning_state = ReasoningState.CLOSED
        return True

    def elapsed(self) -> float:
        return time.time() - self.budget_start_time


# Compatibility aliases for imports from the pre-refactor adapter.  Both point
# at the new independent algorithm config / route context and contain no DEER
# budget fields.
ThinkBudgetConfig = AlgorithmConfig
BudgetRequestContext = RequestStateContext
