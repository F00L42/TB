"""vLLM route context and per-request Think Budget algorithm state."""
from __future__ import annotations

import asyncio
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


class ReasoningBoundary(str, Enum):
    """Model protocol markers that end the reasoning phase."""

    THINK_END = "think_end"
    TOOL_CALL = "tool_call"

    @property
    def marker(self) -> str:
        return {
            ReasoningBoundary.THINK_END: "</think>",
            ReasoningBoundary.TOOL_CALL: "<tool_call>",
        }[self]


class FinalAnswerReason(str, Enum):
    """Explain whether final generation is synthesized or model-selected."""

    CONFIDENCE_ACCEPTED = "confidence_accepted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    MODEL_THINK_END = "model_think_end"
    MODEL_TOOL_CALL = "model_tool_call"


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
    reasoning_boundary: ReasoningBoundary | None = None
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
    assistant_completion_epoch: int = 1
    final_terminal_count: int = 0


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
    reasoning_boundary_emitted: bool = False
    prompt_suffix_token_cache: dict[str, tuple[int, ...]] = field(
        default_factory=dict)
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

    @staticmethod
    def _stream_text_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _stream_token_digest(token_ids: list[int]) -> str:
        payload = json.dumps(token_ids, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _first_output(result: Any) -> Any:
        try:
            return result.outputs[0]
        except (AttributeError, IndexError, TypeError):
            return None

    def _log_stream_delta(
        self,
        *,
        result: Any,
        source_result: Any,
        phase: str,
        child_request_id: str,
        event: str,
        controller_state: str,
        next_state: str,
        reasoning_boundary: ReasoningBoundary | None,
        terminal_flush: bool,
        stop_boundary_aligned: bool,
        terminal_origin: str,
        final_terminal_passthrough: bool,
    ) -> None:
        if not self.stream_debug_enabled:
            return
        self.stream_debug_sequence += 1
        source_output = self._first_output(source_result)
        forwarded_output = self._first_output(result)
        raw_text = getattr(source_output, "text", "") or ""
        raw_token_ids = list(getattr(source_output, "token_ids", None) or [])
        forwarded_text = getattr(forwarded_output, "text", "") or ""
        forwarded_token_ids = list(
            getattr(forwarded_output, "token_ids", None) or [])
        alignment_prefix_valid = (
            len(forwarded_token_ids) < len(raw_token_ids)
            and raw_token_ids[:len(forwarded_token_ids)]
            == forwarded_token_ids
        )
        dropped_token_ids = (
            raw_token_ids[len(forwarded_token_ids):]
            if stop_boundary_aligned and alignment_prefix_valid
            else []
        )
        record = {
            "schema": 4,
            "source": "controller_pre_parser",
            "event": event,
            "parent_request_id": self.request_id,
            "child_request_id": child_request_id,
            "phase": phase,
            "sequence": self.stream_debug_sequence,
            "completion_epoch": (
                self.algorithm_state.assistant_completion_epoch),
            "reasoning_boundary": (
                reasoning_boundary.value if reasoning_boundary else "none"),
            "controller_state": controller_state,
            "next_state": next_state,
            "raw_text": raw_text,
            "raw_text_sha256": self._stream_text_digest(raw_text),
            "token_ids": raw_token_ids,
            "source_token_ids_sha256": self._stream_token_digest(
                raw_token_ids),
            "source_finish_reason": getattr(
                source_output, "finish_reason", None),
            "source_stop_reason": getattr(
                source_output, "stop_reason", None),
            "raw_finished": getattr(source_result, "finished", None),
            "forwarded_text_sha256": self._stream_text_digest(
                forwarded_text),
            "forwarded_text_length": len(forwarded_text),
            "forwarded_token_ids_sha256": self._stream_token_digest(
                forwarded_token_ids),
            "forwarded_token_ids": forwarded_token_ids,
            "forwarded_token_count": len(forwarded_token_ids),
            "forwarded_finish_reason": getattr(
                forwarded_output, "finish_reason", None),
            "forwarded_stop_reason": getattr(
                forwarded_output, "stop_reason", None),
            "forwarded_finished": getattr(result, "finished", None),
            "terminal_flush": terminal_flush,
            "terminal_origin": terminal_origin,
            "final_terminal_passthrough": final_terminal_passthrough,
            "synthetic_terminal": terminal_origin == "synthetic",
            "stop_boundary_aligned": stop_boundary_aligned,
            "alignment_dropped_token_ids": dropped_token_ids,
            "alignment_dropped_token_count": len(dropped_token_ids),
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
        source_result: Any = None,
        event: str = "delta",
        controller_state: str = "",
        next_state: str = "",
        reasoning_boundary: ReasoningBoundary | None = None,
        terminal_flush: bool = False,
        stop_boundary_aligned: bool = False,
        terminal_origin: str = "none",
        final_terminal_passthrough: bool = False,
    ) -> None:
        # Keep the disabled path out of the per-delta telemetry builder.  In
        # particular, do not copy token IDs, hash payloads, serialize JSON, or
        # write/flush stdout unless the diagnostic flag is explicitly enabled.
        if self.stream_debug_enabled:
            self._log_stream_delta(
                result=result,
                source_result=(
                    result if source_result is None else source_result),
                phase=phase,
                child_request_id=child_request_id,
                event=event,
                controller_state=controller_state,
                next_state=next_state,
                reasoning_boundary=reasoning_boundary,
                terminal_flush=terminal_flush,
                stop_boundary_aligned=stop_boundary_aligned,
                terminal_origin=terminal_origin,
                final_terminal_passthrough=final_terminal_passthrough,
            )
        await self.result_queue.put(result)

    def log_stream_event(
        self,
        *,
        event: str,
        phase: str,
        child_request_id: str = "",
        controller_state: str = "",
        next_state: str = "",
        reasoning_boundary: ReasoningBoundary | None = None,
    ) -> None:
        if not self.stream_debug_enabled:
            return
        self._log_stream_delta(
            result=None,
            source_result=None,
            phase=phase,
            child_request_id=child_request_id,
            event=event,
            controller_state=controller_state,
            next_state=next_state,
            reasoning_boundary=reasoning_boundary,
            terminal_flush=False,
            stop_boundary_aligned=False,
            terminal_origin="none",
            final_terminal_passthrough=False,
        )

    async def stream_final_terminal(
        self,
        source_result: Any,
        *,
        phase: str,
        child_request_id: str,
    ) -> None:
        """Forward the final child's source terminal exactly once."""
        state = self.algorithm_state
        if state.final_terminal_count:
            raise RuntimeError("assistant completion already emitted terminal")
        source_output = self._first_output(source_result)
        if source_output is None or getattr(
            source_output, "finish_reason", None
        ) is None:
            raise RuntimeError(
                "final terminal passthrough requires a finished child output")
        if getattr(source_result, "finished", None) is not True:
            raise RuntimeError(
                "final terminal passthrough requires source finished=True")
        state.final_terminal_count += 1
        await self.stream_output(
            source_result,
            source_result=source_result,
            phase=phase,
            child_request_id=child_request_id,
            event="final_terminal_passthrough",
            controller_state="free_final_answer",
            next_state="complete",
            reasoning_boundary=state.reasoning_boundary,
            terminal_flush=False,
            stop_boundary_aligned=False,
            terminal_origin="source",
            final_terminal_passthrough=True,
        )

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
        reasoning_boundary: ReasoningBoundary | None = None,
    ) -> tuple[int, int]:
        if self.algorithm_state.reasoning_state is ReasoningState.CLOSED:
            raise RuntimeError("cannot append thinking after reasoning is closed")
        self.engine_prompt["prompt_token_ids"].extend(token_ids)
        if self.prompt_text is not None and text:
            self.prompt_text += text
            self.engine_prompt["prompt"] = self.prompt_text
        thinking_token_count = len(token_ids)
        final_token_count = 0
        if reasoning_boundary is not None:
            marker_ids = list(self.encode_prompt_suffix(
                reasoning_boundary.marker))
            marker_index = self._find_token_subsequence(token_ids, marker_ids)
            if marker_index is None:
                text_index = text.find(reasoning_boundary.marker)
                if text_index < 0:
                    raise RuntimeError(
                        "reasoning boundary was reported without parser-facing "
                        f"text/token ids: {reasoning_boundary.marker}")
                marker_index = min(
                    len(token_ids),
                    len(self.tokenizer.encode(
                        text[:text_index], add_special_tokens=False)),
                )
            thinking_token_count = marker_index
            final_token_count = len(token_ids) - marker_index
            self.algorithm_state.reasoning_boundary = reasoning_boundary
            self.algorithm_state.reasoning_state = ReasoningState.CLOSED
            self.reasoning_boundary_emitted = True
        self.algorithm_state.thinking_tokens += thinking_token_count
        self.algorithm_state.final_answer_tokens += final_token_count
        self.algorithm_state.thinking_segments += 1
        return thinking_token_count, final_token_count

    @staticmethod
    def _find_token_subsequence(
        token_ids: list[int],
        marker_ids: list[int],
    ) -> int | None:
        if not marker_ids or len(marker_ids) > len(token_ids):
            return None
        width = len(marker_ids)
        for index in range(len(token_ids) - width + 1):
            if token_ids[index:index + width] == marker_ids:
                return index
        return None

    def close_reasoning(self, prompt_suffix: str = "</think>") -> bool:
        """Close once using the FinalAnswer-selected model prompt suffix."""
        if self.algorithm_state.reasoning_state is ReasoningState.CLOSED:
            return False
        if prompt_suffix.count("</think>") != 1:
            raise ValueError(
                "reasoning close suffix must contain exactly one </think>")
        self.append_prompt_suffix(prompt_suffix)
        self.algorithm_state.reasoning_state = ReasoningState.CLOSED
        self.algorithm_state.reasoning_boundary = ReasoningBoundary.THINK_END
        return True

    def elapsed(self) -> float:
        return time.time() - self.budget_start_time


# Compatibility aliases for imports from the pre-refactor adapter.  Both point
# at the new independent algorithm config / route context and contain no DEER
# budget fields.
ThinkBudgetConfig = AlgorithmConfig
BudgetRequestContext = RequestStateContext
