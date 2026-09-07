"""DTB facade for one vLLM chat-completion request."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any, TYPE_CHECKING

from dtb.budgeting.budget_resolver import DtbBudgetResolver
from dtb.config.loader import load_dtb_service_config
from dtb.config.schema import (
    DtbAlgorithmConfig,
    DtbAlgorithmPolicy,
    DtbServiceConfig,
)
from dtb.history.store import (
    AsyncHistoryRecorder,
    HistoryOutcome,
    configure_history_store,
)
from dtb.integration.vllm.request_adapter import extract_prompt_text_views
from dtb.integration.vllm.response_adapter import (
    aggregate_queue,
    native_chat_response,
    stream_queue,
    with_initial_prompt_text,
)
from dtb.observability.config import DtbObservabilityRuntime
from dtb.reasoning.parser_boundary import bind_reasoning_boundary_protocol
from dtb.reasoning.request_context import DtbRequestContext
from dtb.reasoning.request_state import DtbRequestState
from dtb.reasoning.state_machine import DtbStateMachine

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.protocol import (
        ChatCompletionRequest,
        RequestResponseMetadata,
    )
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


class DtbConfigError(ValueError):
    pass


_FIXED_REQUEST_FIELDS = frozenset({
    "fixed_budget_tokens",
    "fixed_num_thinking",
    "fixed_think_stride",
    "fixed_check_length",
    "fixed_prob_th",
    "fixed_min_think_tokens",
})
_RETIRED_REQUEST_FIELDS = frozenset({
    "tb_check_length",
    "tb_think_stride",
    "tb_num_thinking",
    "tb_check_answer_len",
    "tb_prob_th",
    "tb_min_think_tokens",
    "tb_is_code",
    "tb_enable_value_min_confidence",
    "tb_value_min_bottom_k",
    "tb_enable_clean_context",
    "tb_enable_soft_truncation",
    "tb_consecutive_same_stop",
    "budget_token_limit",
    "budget_disabled",
    "budget_confidence_threshold",
    "think_budget_config",
    "think_budget_session_id",
    "think_budget_resume",
    "think_budget_session_ttl_s",
})


class DtbRequestController:
    """Small lifecycle facade used by the versioned vLLM serving patch."""

    ROUTING_CONTEXT_ATTR = "_dtb_route_identity"

    def __init__(
        self,
        history_recorder: AsyncHistoryRecorder | None = None,
        service_config: DtbServiceConfig | None = None,
        observability: DtbObservabilityRuntime | None = None,
    ) -> None:
        self.service_config = service_config or load_dtb_service_config()
        self.policy = DtbAlgorithmPolicy()
        self.budget_resolver = DtbBudgetResolver(
            self.service_config, self.policy
        )
        self.observability = observability or DtbObservabilityRuntime(
            self.service_config.observability
        )
        self._reasoning_protocol_tokenizer: Any = None
        self.history_recorder = history_recorder
        if (
            self.history_recorder is None
            and self.service_config.algorithm.enable_history_budget
        ):
            history_store = self.service_config.history_store
            configure_history_store(
                path=history_store.path,
                max_entries=history_store.max_entries,
                max_age_days=history_store.max_age_days,
            )
            self.history_recorder = AsyncHistoryRecorder(
                max_queue_size=history_store.queue_capacity
            )

    def is_enabled(self) -> bool:
        return self.service_config.algorithm.budget_enabled

    @staticmethod
    def _validate_request_override(name: str, value: Any) -> None:
        if name not in _FIXED_REQUEST_FIELDS:
            raise DtbConfigError(f"unknown DTB config key: {name}")
        if value is None:
            raise DtbConfigError(f"DTB config {name} cannot be null")
        if name == "fixed_prob_th":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= value <= 1
            ):
                raise DtbConfigError(f"DTB config {name} must be in [0, 1]")
            return
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DtbConfigError(
                f"DTB config {name} must be a non-negative integer"
            )

    def _build_algorithm_config(
        self, request: ChatCompletionRequest
    ) -> DtbAlgorithmConfig:
        config = self.service_config.algorithm
        for name in _RETIRED_REQUEST_FIELDS:
            value = getattr(request, name, None)
            if value not in (None, False, 0, ""):
                raise DtbConfigError(f"DTB request field is not supported: {name}")
            if name == "think_budget_config" and value is not None:
                raise DtbConfigError(
                    "DTB request field is not supported: think_budget_config"
                )
        changes: dict[str, Any] = {}
        for name in _FIXED_REQUEST_FIELDS:
            value = getattr(request, name, None)
            if value is None:
                continue
            self._validate_request_override(name, value)
            if value > 0:
                changes[name] = value
        return replace(config, **changes) if changes else config

    def _record_history(self, context: DtbRequestContext) -> None:
        if self.history_recorder is None:
            return
        decision = context.request_state.budget_decision
        if decision is None:
            # Native observation did not run a budget decision. Do not fabricate one.
            return
        state = context.request_state
        self.history_recorder.record_outcome(HistoryOutcome.from_request(
            parent_request_id=context.request_id,
            question=context.question_text,
            scene=decision.scene,
            decision_branch=decision.decision_branch.value,
            budget_decision=decision.to_telemetry(),
            thinking_tokens=state.thinking_tokens,
            hidden_probe_tokens=state.hidden_probe_tokens,
            final_answer_tokens=state.final_answer_tokens,
            elapsed_ms=context.elapsed() * 1000,
            completion_status=state.completion_status,
        ))

    def _managed_generator(
        self,
        state_machine: DtbStateMachine,
        context: DtbRequestContext,
        request: ChatCompletionRequest,
    ) -> AsyncGenerator[RequestOutput, None]:
        async def generator() -> AsyncGenerator[RequestOutput, None]:
            return_prompt_text = bool(getattr(request, "return_prompt_text", False))
            producer_task = asyncio.create_task(state_machine.run())
            consumer = (
                stream_queue(context.result_queue, context)
                if request.stream
                else aggregate_queue(context.result_queue, context)
            )
            try:
                async for item in consumer:
                    if return_prompt_text:
                        item = with_initial_prompt_text(
                            item, context.initial_prompt_text
                        )
                    yield item
            except asyncio.CancelledError:
                context.request_state.completion_status = "cancelled"
                raise
            finally:
                if not producer_task.done():
                    context.request_state.completion_status = "cancelled"
                    producer_task.cancel()
                    try:
                        await producer_task
                    except asyncio.CancelledError:
                        pass
                if context.phase_recorder is not None:
                    context.phase_recorder.record_request_summary(
                        parent_request_id=context.request_id,
                        state=context.request_state,
                        error=context.orchestration_error,
                    )
                self._record_history(context)
        return generator()

    async def flush_history(self, timeout_s: float = 2.0) -> bool:
        if self.history_recorder is None:
            return True
        return await self.history_recorder.flush(timeout_s)

    def flush_telemetry(self, timeout_s: float = 2.0) -> bool:
        return self.observability.flush(timeout_s)

    async def handle_request(
        self,
        serving_chat: OpenAIServingChat,
        conversation: list,
        engine_inputs: list,
        request: ChatCompletionRequest,
        request_id: str,
        tokenizer: TokenizerLike,
        request_metadata: RequestResponseMetadata,
        model_name: str | None = None,
        lora_request: Any = None,
        data_parallel_rank: int | None = None,
    ) -> Any:
        phase_recorder = self.observability.create_phase_recorder(
            self.policy.version)
        controller_entry_started_at = (
            time.perf_counter() if phase_recorder is not None else None
        )
        if not getattr(request, "stream", False) and getattr(request, "n", None) not in (None, 1):
            return serving_chat.create_error_response("DTB currently supports only n=1")
        try:
            config = self._build_algorithm_config(request)
        except (DtbConfigError, ValueError) as exc:
            return serving_chat.create_error_response(str(exc))
        if not config.budget_enabled:
            return serving_chat.create_error_response(
                "DTB is disabled by server configuration"
            )

        policy = self.policy
        route_identity = getattr(request, self.ROUTING_CONTEXT_ATTR, None)
        trace_headers = None
        engine_prompt_text = None
        reasoning_ended = None
        reasoning_parser_kwargs = None
        reasoning_parser = None
        response_parser = None
        chat_template_kwargs = None
        completion_token_limit = None
        if isinstance(route_identity, dict):
            model_name = route_identity.get("model_name", model_name)
            lora_request = route_identity.get("lora_request", lora_request)
            data_parallel_rank = route_identity.get(
                "data_parallel_rank", data_parallel_rank
            )
            trace_headers = route_identity.get("trace_headers")
            engine_prompt_text = route_identity.get("prompt_text")
            reasoning_ended = route_identity.get("reasoning_ended")
            reasoning_parser_kwargs = route_identity.get(
                "reasoning_parser_kwargs"
            )
            reasoning_parser = route_identity.get("reasoning_parser")
            response_parser = route_identity.get("response_parser")
            chat_template_kwargs = route_identity.get("chat_template_kwargs")
            completion_token_limit = route_identity.get(
                "completion_token_limit"
            )
        elif isinstance(route_identity, tuple) and len(route_identity) == 3:
            routed_model, routed_lora, routed_rank = route_identity
            model_name = model_name or routed_model
            lora_request = lora_request or routed_lora
            if data_parallel_rank is None:
                data_parallel_rank = routed_rank
        if lora_request is None:
            lora_request = serving_chat._maybe_get_adapters(
                request, supports_default_mm_loras=True
            )
        if model_name is None:
            model_name = serving_chat.models.model_name(lora_request)
        if (
            not isinstance(completion_token_limit, int)
            or isinstance(completion_token_limit, bool)
            or completion_token_limit < 1
        ):
            return serving_chat.create_error_response(
                "DTB serving patch did not provide a positive native "
                "completion token limit"
            )

        native_params = (
            request.to_sampling_params(completion_token_limit, serving_chat.default_sampling_params)
            if request.stream else None
        )
        if request.stream:
            from dtb.reasoning.generation_states import _output_kind_name
            if (getattr(request, "n", None) not in (None, 1)
                    or _output_kind_name(native_params) != "DELTA"):
                # Outside observer's single-choice DELTA contract: keep the
                # native generator and parser without a partial DTB ledger.
                native_generator = serving_chat.engine_client.generate(
                    engine_inputs[0], native_params, request_id,
                    lora_request=lora_request, priority=getattr(request, "priority", None) or 0,
                    data_parallel_rank=data_parallel_rank, trace_headers=trace_headers,
                    prompt_text=engine_prompt_text, reasoning_ended=reasoning_ended,
                    reasoning_parser_kwargs=reasoning_parser_kwargs,
                )
                return await native_chat_response(
                    serving_chat, request, native_generator, request_id, model_name,
                    conversation, tokenizer, request_metadata, reasoning_parser,
                    response_parser, chat_template_kwargs,
                )

        reasoning_protocol = None
        if request.stream:
            try:
                validate_tokenizer_vocab = (
                    tokenizer is not self._reasoning_protocol_tokenizer
                )
                reasoning_protocol = bind_reasoning_boundary_protocol(
                    self.service_config.reasoning_protocol,
                    tokenizer,
                    reasoning_parser,
                    response_parser,
                    validate_tokenizer_vocab=validate_tokenizer_vocab,
                )
                if validate_tokenizer_vocab:
                    # The service config and tokenizer are startup-scoped.  Do
                    # not rebuild a full vocabulary-ID set for every request;
                    # parser instances and their bound IDs are still checked
                    # on every streaming DTB request.
                    self._reasoning_protocol_tokenizer = tokenizer
            except (AttributeError, RuntimeError, TypeError, ValueError):
                # No verified continuation protocol: same-child native completion.
                reasoning_protocol = None

        prompt_token_ids = engine_inputs[0].get("prompt_token_ids", [])
        if engine_prompt_text is None:
            engine_prompt_text = engine_inputs[0].get("prompt")
        if phase_recorder is None:
            text_views = extract_prompt_text_views(conversation, engine_prompt_text)
        else:
            with phase_recorder.measure("decision.select_text"):
                text_views = extract_prompt_text_views(
                    conversation, engine_prompt_text)
        classification_text = text_views.classification_text
        remaining_context = max(
            1,
            int(serving_chat.model_config.max_model_len) - len(prompt_token_ids),
        )
        hard_token_limit = min(remaining_context, completion_token_limit)

        threshold = (
            self.budget_resolver.intervention_threshold(
                classification_text, config, phase_recorder)
            if request.stream else None
        )

        def resolve_decision():
            if phase_recorder is None:
                decision = self.budget_resolver.resolve(
                    classification_text,
                    config,
                    None,
                    hard_token_limit=hard_token_limit,
                    threshold=threshold,
                )
            else:
                decision_started_at = time.perf_counter()
                decision_trace: dict[str, Any] = {
                    "classification_source": text_views.classification_source,
                    "classification_input_chars": len(classification_text),
                    "initial_text_source": text_views.initial_text_source,
                    "completion_token_limit": completion_token_limit,
                }
                with phase_recorder.measure("decision.total"):
                    decision = self.budget_resolver.resolve(
                        classification_text,
                        config,
                        phase_recorder,
                        hard_token_limit=hard_token_limit,
                        threshold=threshold,
                        decision_trace=decision_trace,
                    )
                decision_wall_ms = (
                    time.perf_counter() - decision_started_at
                ) * 1000
                phase_recorder.record_phase(
                    parent_request_id=request_id,
                    child_request_id="",
                    phase="decision",
                    round_id=0,
                    wall_ms=decision_wall_ms,
                    controller_cpu_ms=decision_wall_ms,
                    prompt_tokens=len(prompt_token_ids),
                    generated_tokens=0,
                    visible=False,
                    budget_decision=decision.to_telemetry(),
                    decision_details=decision_trace,
                )

            return decision

        decision = None if request.stream else resolve_decision()

        request_state = DtbRequestState(
            budget_decision=decision,
            completion_token_limit=completion_token_limit,
        )
        context = DtbRequestContext(
            request=request,
            tokenizer=tokenizer,
            config=config,
            policy=policy,
            serving_chat=serving_chat,
            request_id=request_id,
            engine_prompt=engine_inputs[0],
            request_state=request_state,
            lora_request=lora_request,
            data_parallel_rank=data_parallel_rank,
            trace_headers=trace_headers,
            prompt_text_views=text_views,
            controller_entry_started_at=controller_entry_started_at,
            reasoning_ended=reasoning_ended,
            reasoning_parser_kwargs=reasoning_parser_kwargs,
            reasoning_parser=reasoning_parser,
            reasoning_protocol=reasoning_protocol,
            chat_template_kwargs=chat_template_kwargs,
            result_queue=asyncio.Queue(),
            budget_start_time=time.time(),
            model_name=model_name,
            session_id=request_id,
            phase_recorder=phase_recorder,
            stream_audit=self.observability.create_stream_audit(request_id),
            question_text=classification_text,
            deferred_observation=bool(request.stream),
            intervention_threshold=threshold.tokens if threshold is not None else 0,
            intervention_threshold_source=threshold.source if threshold is not None else "none",
            activate_budget=resolve_decision if request.stream else None,
            observation_params=native_params,
        )
        response_generator = self._managed_generator(
            DtbStateMachine(context), context, request
        )
        return await native_chat_response(
            serving_chat,
            request,
            response_generator,
            request_id,
            model_name,
            conversation,
            tokenizer,
            request_metadata,
            reasoning_parser,
            response_parser,
            chat_template_kwargs,
        )
