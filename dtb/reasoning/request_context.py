"""Native vLLM route identity and request-local DTB collaborators."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, TYPE_CHECKING

from dtb.budgeting.budget_decision import DtbBudgetDecision
from dtb.config.schema import DtbAlgorithmConfig, DtbAlgorithmPolicy
from dtb.reasoning.request_state import (
    DtbRequestState,
    ReasoningBoundary,
    ReasoningState,
)

if TYPE_CHECKING:
    from dtb.observability.phase_recorder import DtbPhaseRecorder
    from dtb.observability.stream_audit import DtbStreamAudit
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


@dataclass
class DtbRequestContext:
    """One external request plus identity propagated to every child generate."""

    request: ChatCompletionRequest
    tokenizer: TokenizerLike
    config: DtbAlgorithmConfig
    policy: DtbAlgorithmPolicy
    serving_chat: OpenAIServingChat
    request_id: str
    engine_prompt: dict[str, Any]
    request_state: DtbRequestState
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
        default_factory=dict
    )
    model_name: Optional[str] = None
    session_id: Optional[str] = None
    phase_recorder: DtbPhaseRecorder | None = None
    stream_audit: DtbStreamAudit | None = None
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
    def budget_decision(self) -> DtbBudgetDecision:
        decision = self.request_state.budget_decision
        if decision is None:
            raise RuntimeError("DTB budget decision was not attached to request state")
        return decision

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
            "prompt_text": self.prompt_text if prompt_text is None else prompt_text,
            "reasoning_ended": (
                self.reasoning_ended
                if reasoning_ended is None
                else reasoning_ended
            ),
            "reasoning_parser_kwargs": self.reasoning_parser_kwargs,
        }

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
        output = self._first_output(result)
        token_ids = (
            getattr(output, "token_ids", None)
            if output is not None else None
        )
        emitted_tokens = len(token_ids or ())
        if emitted_tokens:
            state = self.request_state
            updated = state.emitted_completion_tokens + emitted_tokens
            if updated > state.completion_token_limit:
                raise RuntimeError(
                    "DTB parser-facing completion token budget exceeded: "
                    f"{updated}>{state.completion_token_limit}"
                )
            state.emitted_completion_tokens = updated
        audit = self.stream_audit
        if audit is not None:
            audit.record(
                context=self,
                result=result,
                source_result=result if source_result is None else source_result,
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
        audit = self.stream_audit
        if audit is None:
            return
        audit.record(
            context=self,
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

    @staticmethod
    def _first_output(result: Any) -> Any:
        try:
            return result.outputs[0]
        except (AttributeError, IndexError, TypeError):
            return None

    async def stream_final_terminal(
        self,
        source_result: Any,
        *,
        phase: str,
        child_request_id: str,
    ) -> None:
        """Forward the final child's source terminal exactly once."""
        state = self.request_state
        if state.final_terminal_count:
            raise RuntimeError("assistant completion already emitted terminal")
        source_output = self._first_output(source_result)
        if source_output is None or getattr(
            source_output, "finish_reason", None
        ) is None:
            raise RuntimeError(
                "final terminal passthrough requires a finished child output"
            )
        if getattr(source_result, "finished", None) is not True:
            raise RuntimeError(
                "final terminal passthrough requires source finished=True"
            )
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
            cached = tuple(self.tokenizer.encode(text, add_special_tokens=False))
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
            self.encode_prompt_suffix(suffix_text)
        )
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
        state = self.request_state
        if state.reasoning_state is ReasoningState.CLOSED:
            raise RuntimeError("cannot append thinking after reasoning is closed")
        self.engine_prompt["prompt_token_ids"].extend(token_ids)
        if self.prompt_text is not None and text:
            self.prompt_text += text
            self.engine_prompt["prompt"] = self.prompt_text
        thinking_token_count = len(token_ids)
        final_token_count = 0
        if reasoning_boundary is not None:
            marker_ids = list(self.encode_prompt_suffix(reasoning_boundary.marker))
            marker_index = self._find_token_subsequence(token_ids, marker_ids)
            if marker_index is None:
                text_index = text.find(reasoning_boundary.marker)
                if text_index < 0:
                    raise RuntimeError(
                        "reasoning boundary was reported without parser-facing "
                        f"text/token ids: {reasoning_boundary.marker}"
                    )
                marker_index = min(
                    len(token_ids),
                    len(self.tokenizer.encode(
                        text[:text_index], add_special_tokens=False
                    )),
                )
            thinking_token_count = marker_index
            final_token_count = len(token_ids) - marker_index
            state.reasoning_boundary = reasoning_boundary
            state.reasoning_state = ReasoningState.CLOSED
            self.reasoning_boundary_emitted = True
        state.thinking_tokens += thinking_token_count
        state.final_answer_tokens += final_token_count
        state.thinking_segments += 1
        return thinking_token_count, final_token_count

    @staticmethod
    def _find_token_subsequence(
        token_ids: list[int], marker_ids: list[int]
    ) -> int | None:
        if not marker_ids or len(marker_ids) > len(token_ids):
            return None
        width = len(marker_ids)
        for index in range(len(token_ids) - width + 1):
            if token_ids[index:index + width] == marker_ids:
                return index
        return None

    def close_reasoning(self, prompt_suffix: str = "</think>") -> bool:
        """Close once using the final-answer-selected model prompt suffix."""
        state = self.request_state
        if state.reasoning_state is ReasoningState.CLOSED:
            return False
        if prompt_suffix.count("</think>") != 1:
            raise ValueError(
                "reasoning close suffix must contain exactly one </think>"
            )
        self.append_prompt_suffix(prompt_suffix)
        state.reasoning_state = ReasoningState.CLOSED
        state.reasoning_boundary = ReasoningBoundary.THINK_END
        return True

    def elapsed(self) -> float:
        return time.time() - self.budget_start_time
