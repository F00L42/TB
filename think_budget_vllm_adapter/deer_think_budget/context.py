"""vLLM route context and per-request Think Budget algorithm state."""
from __future__ import annotations

import asyncio
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

    async def stream_output(self, result: Any) -> None:
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

    def close_reasoning(self) -> bool:
        """Perform the only allowed OPEN -> CLOSED transition."""
        if self.algorithm_state.reasoning_state is ReasoningState.CLOSED:
            return False
        self.append_prompt_suffix("</think>")
        self.algorithm_state.reasoning_state = ReasoningState.CLOSED
        return True

    def elapsed(self) -> float:
        return time.time() - self.budget_start_time


# Compatibility aliases for imports from the pre-refactor adapter.  Both point
# at the new independent algorithm config / route context and contain no DEER
# budget fields.
ThinkBudgetConfig = AlgorithmConfig
BudgetRequestContext = RequestStateContext
