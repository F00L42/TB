"""Think Budget decision, vLLM routing, and parent-request lifecycle."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import replace
from typing import Any, Optional, TYPE_CHECKING

from think_budget_vllm_adapter.config.unified_config import (
    get_performance_policy_config,
    get_runtime_config,
)
from think_budget_vllm_adapter.context import (
    AlgorithmConfig,
    BudgetPlan,
    BudgetVector,
    DecisionBranch,
    PerformancePolicy,
    log_performance_policy_once,
)
from think_budget_vllm_adapter.deer_think_budget.context import (
    RequestAlgorithmState,
    RequestStateContext,
)
from think_budget_vllm_adapter.deer_think_budget.state import (
    AsyncReasoningOrchestrator,
)
from think_budget_vllm_adapter.history.history_module import (
    AsyncHistoryRecorder,
    HistoryOutcome,
)
from think_budget_vllm_adapter.optimizations.difficulty_estimator import (
    compute_budget_from_difficulty,
    estimate_difficulty,
)
from think_budget_vllm_adapter.optimizations.scene_classifier import (
    classify_scene,
)
from think_budget_vllm_adapter.optimizations.task_type_budget import (
    classify_task_type,
    get_task_type_budget,
    is_known_task_type,
)
from think_budget_vllm_adapter.telemetry import TimingCollector

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.protocol import (
        ChatCompletionRequest,
        RequestResponseMetadata,
    )
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


class ThinkBudgetConfigError(ValueError):
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
    "budget_task_type_override",
    "think_budget_config",
    "think_budget_session_id",
    "think_budget_resume",
    "think_budget_session_ttl_s",
})
_VECTOR_FIELDS = (
    "check_length",
    "min_think_budget",
    "num_thinking",
    "think_stride",
)
_DEFAULT_CONFIDENCE_THRESHOLD = 0.95
_DEFAULT_PERCENTILE_STEP = 0.75


def _round_half_up_average(left: int, right: int) -> int:
    return (int(left) + int(right) + 1) // 2


def _optional_vector_value(
    values: dict[str, Any],
    field_name: str,
) -> int | None:
    value = values.get(field_name)
    if value is None and field_name == "min_think_budget":
        value = values.get("min_think_tokens")
    if value is None:
        return None
    return max(1, int(value))


class BudgetController:
    """Entry point used by the vLLM chat-completion serving patch."""

    ROUTING_CONTEXT_ATTR = "_think_budget_route_identity"

    def __init__(
        self,
        history_recorder: AsyncHistoryRecorder | None = None,
    ) -> None:
        self.history_recorder = history_recorder or AsyncHistoryRecorder()

    @staticmethod
    def _default_config() -> AlgorithmConfig:
        defaults = get_runtime_config()
        get_performance_policy_config()
        return AlgorithmConfig(
            budget_enabled=bool(defaults["BUDGET_ENABLED"]),
            check_length=int(defaults["check_length"]),
            min_think_budget=int(defaults["min_think_budget"]),
            num_thinking=int(defaults["num_thinking"]),
            think_stride=int(defaults["think_stride"]),
            confidence_threshold=float(defaults["confidence_threshold"]),
            percentile_step=float(defaults["percentile_step"]),
            enable_value_min_confidence=bool(
                defaults["enable_value_min_confidence"]),
            value_min_bottom_k=int(defaults["value_min_bottom_k"]),
            enable_safety_gate=bool(defaults["enable_safety_gate"]),
            baseline_time_limit=float(defaults["baseline_time_limit"]),
            enable_task_type_budget=bool(
                defaults["enable_task_type_budget"]),
            enable_history_budget=bool(defaults["enable_history_budget"]),
            enable_difficulty_estimator=bool(
                defaults["enable_difficulty_estimator"]),
            difficulty_check_length_base=int(
                defaults["difficulty_check_length_base"]),
            difficulty_check_length_max=int(
                defaults["difficulty_check_length_max"]),
            difficulty_min_think_base=int(
                defaults["difficulty_min_think_base"]),
            difficulty_min_think_max=int(
                defaults["difficulty_min_think_max"]),
            difficulty_stride_base=int(
                defaults["difficulty_stride_base"]),
            difficulty_stride_max=int(
                defaults["difficulty_stride_max"]),
            difficulty_num_thinking_base=int(
                defaults["difficulty_num_thinking_base"]),
            difficulty_num_thinking_max=int(
                defaults["difficulty_num_thinking_max"]),
            fixed_budget_tokens=int(defaults["fixed_budget_tokens"]),
            fixed_num_thinking=int(defaults["fixed_num_thinking"]),
            fixed_think_stride=int(defaults["fixed_think_stride"]),
            fixed_check_length=int(defaults["fixed_check_length"]),
            fixed_prob_th=float(defaults["fixed_prob_th"]),
            fixed_min_think_tokens=int(
                defaults["fixed_min_think_tokens"]),
            history_budget_multiplier=float(
                defaults["history_budget_multiplier"]),
            history_low_success_boost=float(
                defaults["history_low_success_boost"]),
            history_stride_percentile=float(
                defaults["history_stride_percentile"]),
            history_warmup_count=int(defaults["history_warmup_count"]),
            history_warmup_strategy=str(
                defaults["history_warmup_strategy"]),
            new_task_unlimited=bool(defaults["new_task_unlimited"]),
            auto_cluster_threshold=int(
                defaults["auto_cluster_threshold"]),
            custom_task_types_path=defaults["custom_task_types_path"],
            default_task_type=str(defaults["default_task_type"]),
            task_type_override=str(defaults["task_type_override"]),
            max_internal_calls=int(defaults["max_internal_calls"]),
            max_thinking_tokens=int(defaults["max_thinking_tokens"]),
            final_answer_max_tokens=int(
                defaults["final_answer_max_tokens"]),
            emit_phase_metrics=bool(defaults["emit_phase_metrics"]),
            verbose_stage_logging=bool(defaults["verbose_stage_logging"]),
        )

    @staticmethod
    def is_enabled() -> bool:
        return bool(get_runtime_config()["BUDGET_ENABLED"])

    @staticmethod
    def _validate_value(name: str, value: Any) -> None:
        if name not in _FIXED_REQUEST_FIELDS:
            raise ThinkBudgetConfigError(
                f"unknown Think Budget config key: {name}")
        if value is None:
            raise ThinkBudgetConfigError(
                f"Think Budget config {name} cannot be null")
        if name == "fixed_prob_th":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= value <= 1
            ):
                raise ThinkBudgetConfigError(
                    f"Think Budget config {name} must be in [0, 1]")
            return
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ThinkBudgetConfigError(
                f"Think Budget config {name} must be a non-negative integer")

    @classmethod
    def _build_algorithm_config(
        cls,
        request: ChatCompletionRequest,
    ) -> AlgorithmConfig:
        config = cls._default_config()
        for name in _RETIRED_REQUEST_FIELDS:
            value = getattr(request, name, None)
            if value not in (None, False, 0, ""):
                raise ThinkBudgetConfigError(
                    f"Think Budget request field is not supported: {name}")
            if name == "think_budget_config" and value is not None:
                raise ThinkBudgetConfigError(
                    "Think Budget request field is not supported: "
                    "think_budget_config")
        changes: dict[str, Any] = {}
        for name in _FIXED_REQUEST_FIELDS:
            value = getattr(request, name, None)
            if value is None:
                continue
            cls._validate_value(name, value)
            if value > 0:
                changes[name] = value
        return replace(config, **changes) if changes else config

    # Compatibility with callers that used the previous private name.
    _build_budget_config = _build_algorithm_config

    @staticmethod
    def _classify_task(
        prompt_text: str,
        config: AlgorithmConfig,
        timing: TimingCollector,
    ) -> tuple[dict[str, Any], dict[str, Any], str, bool]:
        with timing.measure("decision.scene_classifier"):
            scene_info = classify_scene(prompt_text)
        with timing.measure("decision.task_type"):
            task_match = classify_task_type(
                prompt_text, scene_info, include_budget=False)
        task_type = (
            config.task_type_override
            or task_match.get("task_type")
            or config.default_task_type
        )
        task_matched = bool(
            config.enable_task_type_budget
            and task_type != "general"
            and is_known_task_type(task_type)
            and (
                config.task_type_override
                or float(task_match.get("task_confidence", 0.0)) > 0
            )
        )
        return scene_info, task_match, task_type, task_matched

    @staticmethod
    def _clamp_vector(
        vector: BudgetVector,
        hard_limit: int,
        source_by_field: dict[str, Any],
    ) -> BudgetVector:
        clamped: dict[str, int] = {}
        for field_name, value in vector.to_dict().items():
            final_value = max(1, min(int(value), hard_limit))
            clamped[field_name] = final_value
            if final_value != value:
                previous = source_by_field.get(field_name)
                source_by_field[field_name] = {
                    "source": previous,
                    "clamped_from": value,
                    "hard_limit": hard_limit,
                    "final": final_value,
                }
        return BudgetVector(**clamped)

    @classmethod
    def _decide(
        cls,
        prompt_text: str,
        config: AlgorithmConfig,
        timing: TimingCollector,
        *,
        hard_token_limit: int | None = None,
        reference: str = "",
    ) -> BudgetPlan:
        """Apply the reviewed FIXED / TASK_DIFFICULTY / HISTORY tree."""
        policy = PerformancePolicy()
        scene_info, task_match, task_type, task_matched = cls._classify_task(
            prompt_text, config, timing)
        default_vector = config.default_budget
        source_by_field: dict[str, Any] = {}
        difficulty_info: dict[str, Any] | None = None
        history_info: dict[str, Any] | None = None
        history_snapshot_id: str | None = None
        branch_confidence_threshold = _DEFAULT_CONFIDENCE_THRESHOLD
        branch_confidence_source = "runtime_default"
        if config.percentile_step != _DEFAULT_PERCENTILE_STEP:
            effective_percentile_step = config.percentile_step
            percentile_source = "percentile_step_override"
        else:
            effective_percentile_step = config.history_stride_percentile
            percentile_source = "history_stride_percentile"

        if config.fixed_budget_tokens > 0:
            branch = DecisionBranch.FIXED
            fixed_check_length = (
                config.fixed_check_length
                if config.fixed_check_length > 0
                else default_vector.check_length
            )
            fixed_stride = (
                config.fixed_think_stride
                if config.fixed_think_stride > 0
                else default_vector.think_stride
            )
            check_length = min(
                fixed_check_length, config.fixed_budget_tokens)
            think_stride = min(
                fixed_stride, config.fixed_budget_tokens)
            if config.fixed_num_thinking > 0:
                num_thinking = config.fixed_num_thinking
            else:
                remaining = max(
                    config.fixed_budget_tokens - check_length, 0)
                num_thinking = max(
                    1,
                    (remaining + max(think_stride, 1) - 1)
                    // max(think_stride, 1),
                )
            vector = BudgetVector(
                check_length=check_length,
                min_think_budget=(
                    config.fixed_min_think_tokens
                    if config.fixed_min_think_tokens > 0
                    else config.fixed_budget_tokens
                ),
                num_thinking=num_thinking,
                think_stride=think_stride,
            )
            source_by_field.update({
                field_name: {
                    "source": "fixed_budget",
                    "fixed_budget_tokens": config.fixed_budget_tokens,
                    "final": getattr(vector, field_name),
                }
                for field_name in _VECTOR_FIELDS
            })
        elif task_matched:
            branch = DecisionBranch.TASK_DIFFICULTY
            with timing.measure("decision.task_budget"):
                task_values = get_task_type_budget(
                    task_type, config.custom_task_types_path)
            task_vector = BudgetVector.from_mapping(
                task_values, default_vector)
            task_confidence = task_values.get(
                "confidence_threshold", task_values.get("prob_th"))
            if isinstance(task_confidence, (int, float)):
                branch_confidence_threshold = max(
                    0.0, min(1.0, float(task_confidence)))
                branch_confidence_source = "task"
            if config.enable_difficulty_estimator:
                with timing.measure("decision.difficulty"):
                    difficulty_info = estimate_difficulty(
                        prompt_text,
                        reference,
                        task_type=task_type,
                        scene_type=scene_info.get(
                            "scene_type", "general"),
                        scene_typical_tokens=int(
                            scene_info.get("typical_tokens", 0)),
                        scene_confidence=float(
                            scene_info.get("scene_confidence", 0.0)),
                    )
                    difficulty_values = compute_budget_from_difficulty(
                        float(difficulty_info.get("difficulty", 0.5)),
                        base_check_length=(
                            config.difficulty_check_length_base),
                        max_check_length=(
                            config.difficulty_check_length_max),
                        base_min_think=(
                            config.difficulty_min_think_base),
                        max_min_think=(
                            config.difficulty_min_think_max),
                        base_stride=config.difficulty_stride_base,
                        max_stride=config.difficulty_stride_max,
                        base_num_thinking=(
                            config.difficulty_num_thinking_base),
                        max_num_thinking=(
                            config.difficulty_num_thinking_max),
                    )
                merged: dict[str, int] = {}
                raw_sources: dict[str, tuple[int | None, int | None]] = {}
                for field_name in _VECTOR_FIELDS:
                    task_value = _optional_vector_value(
                        task_values, field_name)
                    difficulty_value = _optional_vector_value(
                        difficulty_values, field_name)
                    raw_sources[field_name] = (
                        task_value, difficulty_value)
                    if task_value is not None and difficulty_value is not None:
                        merged[field_name] = _round_half_up_average(
                            task_value, difficulty_value)
                    elif task_value is not None:
                        merged[field_name] = task_value
                    elif difficulty_value is not None:
                        merged[field_name] = difficulty_value
                    else:
                        merged[field_name] = getattr(
                            default_vector, field_name)
                vector = BudgetVector(**merged)
                for field_name in _VECTOR_FIELDS:
                    task_value, difficulty_value = raw_sources[field_name]
                    if task_value is not None and difficulty_value is not None:
                        source = "task+difficulty"
                    elif task_value is not None:
                        source = "task"
                    elif difficulty_value is not None:
                        source = "difficulty"
                    else:
                        source = "default"
                    source_by_field[field_name] = {
                        "source": source,
                        "task": task_value,
                        "difficulty": difficulty_value,
                        "rounding": "half_up",
                        "final": getattr(vector, field_name),
                    }
            else:
                vector = task_vector
                source_by_field.update({
                    field_name: {
                        "source": "task",
                        "final": getattr(vector, field_name),
                    }
                    for field_name in _VECTOR_FIELDS
                })
        else:
            branch = DecisionBranch.HISTORY
            if config.enable_history_budget:
                from think_budget_vllm_adapter.history.history_budget import (
                    compute_history_budget,
                )

                with timing.measure("decision.history"):
                    history_info = compute_history_budget(
                        prompt_text,
                        task_type=task_type,
                        scene_type=scene_info.get(
                            "scene_type", "general"),
                        config={
                            "history_budget_multiplier":
                                config.history_budget_multiplier,
                            "history_low_success_boost":
                                config.history_low_success_boost,
                            "history_stride_percentile":
                                effective_percentile_step,
                            "history_warmup_count":
                                config.history_warmup_count,
                            "history_warmup_strategy":
                                config.history_warmup_strategy,
                            "new_task_unlimited":
                                config.new_task_unlimited,
                            "auto_cluster_threshold":
                                config.auto_cluster_threshold,
                            "custom_task_types_path":
                                config.custom_task_types_path,
                        },
                    )
                vector = BudgetVector.from_mapping(
                    history_info, default_vector)
                history_snapshot_id = history_info.get(
                    "history_snapshot_id")
                history_source = history_info.get(
                    "budget_mode", "history")
                history_confidence = history_info.get(
                    "confidence_threshold", history_info.get("prob_th"))
                if isinstance(history_confidence, (int, float)):
                    branch_confidence_threshold = max(
                        0.0, min(1.0, float(history_confidence)))
                    branch_confidence_source = "history"
            else:
                vector = default_vector
                history_info = {"budget_mode": "history_disabled"}
                history_source = "default"
            source_by_field.update({
                field_name: {
                    "source": history_source,
                    "final": getattr(vector, field_name),
                }
                for field_name in _VECTOR_FIELDS
            })

        fixed_overrides = {
            "check_length": config.fixed_check_length,
            "min_think_budget": config.fixed_min_think_tokens,
            "num_thinking": config.fixed_num_thinking,
            "think_stride": config.fixed_think_stride,
        }
        fixed_source_names = {
            "check_length": "fixed_check_length",
            "min_think_budget": "fixed_min_think_tokens",
            "num_thinking": "fixed_num_thinking",
            "think_stride": "fixed_think_stride",
        }
        vector_values = vector.to_dict()
        for field_name, fixed_value in fixed_overrides.items():
            if fixed_value > 0:
                vector_values[field_name] = fixed_value
                source_by_field[field_name] = {
                    "source": fixed_source_names[field_name],
                    "final": fixed_value,
                }
        vector = BudgetVector(**vector_values)

        hard_limit = max(1, min(
            config.max_thinking_tokens,
            hard_token_limit or config.max_thinking_tokens,
            (
                config.fixed_budget_tokens
                if config.fixed_budget_tokens > 0
                else config.max_thinking_tokens
            ),
        ))
        vector = cls._clamp_vector(
            vector, hard_limit, source_by_field)
        if config.fixed_prob_th > 0:
            confidence_threshold = config.fixed_prob_th
            confidence_source = "fixed_prob_th"
        elif (
            config.confidence_threshold
            != _DEFAULT_CONFIDENCE_THRESHOLD
        ):
            confidence_threshold = config.confidence_threshold
            confidence_source = "confidence_threshold_override"
        else:
            confidence_threshold = branch_confidence_threshold
            confidence_source = branch_confidence_source
        source_by_field["confidence_threshold"] = {
            "source": confidence_source,
            "final": confidence_threshold,
        }
        source_by_field["percentile_step"] = {
            "source": percentile_source,
            "final": effective_percentile_step,
        }
        source_by_field["hard_think_token_limit"] = {
            "source": "context/request/config",
            "final": hard_limit,
        }

        return BudgetPlan(
            task_type=task_type,
            task_matched=task_matched,
            decision_branch=branch,
            budget=vector,
            confidence_threshold=confidence_threshold,
            percentile_step=effective_percentile_step,
            source_by_field=source_by_field,
            history_snapshot_id=history_snapshot_id,
            policy_version=policy.version,
            hard_think_token_limit=hard_limit,
            scene_type=scene_info.get("scene_type", "general"),
            difficulty_info=difficulty_info,
            history_info=history_info,
        )

    @staticmethod
    async def _queue_as_async_gen_stream(
        queue: asyncio.Queue,
        context: RequestStateContext,
    ) -> AsyncIterator[RequestOutput]:
        while True:
            item = await queue.get()
            if item is None:
                if context.orchestration_error:
                    raise RuntimeError(
                        "Think Budget orchestration failed; see server logs")
                break
            yield item

    @staticmethod
    async def _queue_as_async_gen(
        queue: asyncio.Queue,
        context: RequestStateContext,
    ) -> AsyncIterator[RequestOutput]:
        item = await queue.get()
        if item is None:
            if context.orchestration_error:
                raise RuntimeError(
                    "Think Budget orchestration failed; see server logs")
            yield None
            return
        while True:
            following = await queue.get()
            if following is None:
                if context.orchestration_error:
                    raise RuntimeError(
                        "Think Budget orchestration failed; see server logs")
                break
            item.outputs[0].text += following.outputs[0].text
            item.outputs[0].token_ids.extend(
                following.outputs[0].token_ids)
            if following.outputs[0].finish_reason is not None:
                item.outputs[0].finish_reason = (
                    following.outputs[0].finish_reason)
                item.outputs[0].stop_reason = (
                    following.outputs[0].stop_reason)
        if item.outputs[0].finish_reason is None:
            item.outputs[0].finish_reason = "stop"
        item.finished = True
        yield item

    def _record_history(self, context: RequestStateContext) -> None:
        plan = context.budget_plan
        difficulty = None
        if plan.difficulty_info is not None:
            difficulty = float(
                plan.difficulty_info.get("difficulty", 0.0))
        ledger = context.timing.ledger.snapshot()
        outcome = HistoryOutcome.from_request(
            parent_request_id=context.request_id,
            question=context.question_text,
            task_type=plan.task_type,
            scene_type=plan.scene_type,
            decision_branch=plan.decision_branch.value,
            difficulty=difficulty,
            budget_plan=plan.telemetry(),
            thinking_tokens=int(ledger["visible_think_tokens"]),
            hidden_probe_tokens=int(ledger["hidden_probe_tokens"]),
            final_answer_tokens=int(ledger["final_answer_tokens"]),
            elapsed_ms=context.elapsed() * 1000,
            completion_status=context.algorithm_state.completion_status,
        )
        self.history_recorder.record_outcome(outcome)

    def _build_managed_generator(
        self,
        orchestrator: AsyncReasoningOrchestrator,
        context: RequestStateContext,
        request: ChatCompletionRequest,
    ) -> AsyncGenerator[RequestOutput, None]:
        async def generator() -> AsyncGenerator[RequestOutput, None]:
            producer_task = asyncio.create_task(orchestrator.run())
            consumer = (
                self._queue_as_async_gen_stream(
                    context.result_queue, context)
                if request.stream
                else self._queue_as_async_gen(
                    context.result_queue, context)
            )
            try:
                async for item in consumer:
                    yield item
            except asyncio.CancelledError:
                context.algorithm_state.completion_status = "cancelled"
                raise
            finally:
                if not producer_task.done():
                    context.algorithm_state.completion_status = "cancelled"
                    producer_task.cancel()
                    try:
                        await producer_task
                    except asyncio.CancelledError:
                        pass
                self._record_history(context)

        return generator()

    async def flush_history(self, timeout_s: float = 2.0) -> bool:
        return await self.history_recorder.flush(timeout_s)

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
        if getattr(request, "n", None) not in (None, 1):
            return serving_chat.create_error_response(
                "Think Budget currently supports only n=1")
        try:
            config = self._build_algorithm_config(request)
        except (ThinkBudgetConfigError, ValueError) as exc:
            return serving_chat.create_error_response(str(exc))
        if not config.budget_enabled:
            return serving_chat.create_error_response(
                "Think Budget is disabled by server configuration")

        policy = PerformancePolicy()
        log_performance_policy_once(policy)

        route_identity = getattr(
            request, self.ROUTING_CONTEXT_ATTR, None)
        trace_headers = None
        engine_prompt_text = None
        reasoning_ended = None
        reasoning_parser_kwargs = None
        reasoning_parser = None
        response_parser = None
        chat_template_kwargs = None
        if isinstance(route_identity, dict):
            model_name = route_identity.get("model_name", model_name)
            lora_request = route_identity.get(
                "lora_request", lora_request)
            data_parallel_rank = route_identity.get(
                "data_parallel_rank", data_parallel_rank)
            trace_headers = route_identity.get("trace_headers")
            engine_prompt_text = route_identity.get("prompt_text")
            reasoning_ended = route_identity.get("reasoning_ended")
            reasoning_parser_kwargs = route_identity.get(
                "reasoning_parser_kwargs")
            reasoning_parser = route_identity.get("reasoning_parser")
            response_parser = route_identity.get("response_parser")
            chat_template_kwargs = route_identity.get(
                "chat_template_kwargs")
        elif isinstance(route_identity, tuple) and len(route_identity) == 3:
            routed_model, routed_lora, routed_rank = route_identity
            model_name = model_name or routed_model
            lora_request = lora_request or routed_lora
            if data_parallel_rank is None:
                data_parallel_rank = routed_rank
        if lora_request is None:
            lora_request = serving_chat._maybe_get_adapters(
                request, supports_default_mm_loras=True)
        if model_name is None:
            model_name = serving_chat.models.model_name(lora_request)

        timing = TimingCollector(
            emit_phase_metrics=config.emit_phase_metrics,
            policy_version=policy.version,
        )
        prompt_token_ids = engine_inputs[0].get(
            "prompt_token_ids", [])
        try:
            with timing.measure("decision.decode_prompt"):
                prompt_text = tokenizer.decode(
                    prompt_token_ids, skip_special_tokens=True)
        except Exception:
            prompt_text = ""

        remaining_context = max(
            1,
            int(serving_chat.model_config.max_model_len)
            - len(prompt_token_ids),
        )
        requested_limit = (
            getattr(request, "max_completion_tokens", None)
            or getattr(request, "max_tokens", None)
        )
        hard_token_limit = remaining_context
        if isinstance(requested_limit, int) and requested_limit > 0:
            hard_token_limit = min(hard_token_limit, requested_limit)

        decision_started_at = time.perf_counter()
        with timing.measure("decision.total"):
            plan = self._decide(
                prompt_text,
                config,
                timing,
                hard_token_limit=hard_token_limit,
            )
        decision_wall_ms = (
            time.perf_counter() - decision_started_at) * 1000
        plan_telemetry = plan.telemetry()
        timing.record_phase(
            parent_request_id=request_id,
            child_request_id="",
            phase="decision",
            round_id=0,
            wall_ms=decision_wall_ms,
            controller_cpu_ms=decision_wall_ms,
            prompt_tokens=len(prompt_token_ids),
            generated_tokens=0,
            visible=False,
            budget_plan=plan_telemetry,
        )

        state = RequestAlgorithmState(budget_plan=plan)
        context = RequestStateContext(
            request=request,
            tokenizer=tokenizer,
            config=config,
            policy=policy,
            serving_chat=serving_chat,
            request_id=request_id,
            engine_prompt=engine_inputs[0],
            algorithm_state=state,
            lora_request=lora_request,
            data_parallel_rank=data_parallel_rank,
            trace_headers=trace_headers,
            prompt_text=(
                engine_prompt_text
                or engine_inputs[0].get("prompt")
                or prompt_text
            ),
            reasoning_ended=reasoning_ended,
            reasoning_parser_kwargs=reasoning_parser_kwargs,
            reasoning_parser=reasoning_parser,
            chat_template_kwargs=chat_template_kwargs,
            result_queue=asyncio.Queue(),
            budget_start_time=time.time(),
            model_name=model_name,
            session_id=request_id,
            timing=timing,
            question_text=prompt_text,
        )
        orchestrator = AsyncReasoningOrchestrator(context)
        response_generator = self._build_managed_generator(
            orchestrator, context, request)
        return await self._format_response(
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

    @staticmethod
    async def _format_response(
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
