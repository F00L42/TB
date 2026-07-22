"""Production controller for Think Budget on top of vLLM's async engine.

The controller deliberately separates three concerns:
* request configuration is validated once and always wins field-by-field;
* budget modules calculate only fields not supplied by the caller;
* the state machine follows DEER's request-bound stream lifecycle, leaving
  parent-request concurrency and cancellation to vLLM/OpenAI serving.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Dict, Optional, TYPE_CHECKING

from think_budget_vllm_adapter.config.unified_config import get_runtime_config
from think_budget_vllm_adapter.context import (
    BudgetConfig, BudgetRequestContext, TRIAL_ANSWER_LENGTH,
)
from think_budget_vllm_adapter.deer_think_budget.state import AsyncReasoningOrchestrator
from think_budget_vllm_adapter.optimizations.complexity_analyzer import estimate_complexity
from think_budget_vllm_adapter.optimizations.difficulty_estimator import (
    compute_budget_from_difficulty,
    estimate_difficulty,
)
from think_budget_vllm_adapter.optimizations.scene_classifier import classify_scene
from think_budget_vllm_adapter.optimizations.task_type_budget import (
    classify_task_type,
    get_task_type_budget,
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
    """A client supplied an unknown or invalid Think Budget configuration."""


# Think Budget 的公网请求面严格收敛为总开关和这六个固定输入。动态
# 策略、会话控制与兼容别名均只允许出现在服务端配置中，不能由 HTTP 请求
# 改写。下面的拒绝列表用于防御「服务端协议补丁未完全更新」的混合部署。
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

_DECISION_TO_CONFIG = {
    "check_length": "initial_thought_limit",
    "think_stride": "think_stride",
    "num_thinking": "trial_answers",
    "check_answer_len": "trial_answer_length",
    "prob_th": "prob_th",
    "min_think_tokens": "min_think_tokens",
    "think_budget": "token_budget",
    "token_budget": "token_budget",
}
_CORE_DECISION_FIELDS = frozenset(_DECISION_TO_CONFIG.values())
class BudgetController:
    """Request entrypoint used by the vLLM chat-completion serving patch."""

    # ``serving.py`` 与适配器曾分别演进，直接扩展 handle_request 的位置
    # 参数会让部分服务器加载到的旧控制器在路由后立即 TypeError。公共调用
    # ABI 固定为 7 个业务参数；新版所需路由与解析上下文由 serving.py 写入
    # 私有属性，避免把 HTTP 请求面扩展成内部对象传输协议。
    ROUTING_CONTEXT_ATTR = "_think_budget_route_identity"

    @staticmethod
    def _default_config() -> BudgetConfig:
        """Build runtime config from the single YAML-backed configuration."""
        defaults = get_runtime_config()
        return BudgetConfig(
            BUDGET_ENABLED=bool(defaults["BUDGET_ENABLED"]),
            initial_thought_limit=int(defaults["check_length"]),
            think_stride=int(defaults["think_stride"]),
            trial_answers=int(defaults["num_thinking"]),
            trial_answer_length=int(defaults["check_answer_len"]),
            token_budget=int(defaults["max_tokens"]),
            max_api_calls=int(defaults["max_api_calls"]),
            prob_th=float(defaults["prob_th"]),
            min_think_tokens=int(defaults["min_think_tokens"]),
            enable_value_min_confidence=bool(defaults["enable_value_min_confidence"]),
            value_min_bottom_k=int(defaults["value_min_bottom_k"]),
            enable_full_chain_confidence=bool(defaults["enable_full_chain_confidence"]),
            full_chain_min_percentile=float(defaults["full_chain_min_percentile"]),
            enable_safety_gate=bool(defaults["enable_safety_gate"]),
            baseline_time_limit=float(defaults["baseline_time_limit"]),
            enable_task_type_budget=bool(defaults["enable_task_type_budget"]),
            enable_history_budget=bool(defaults["enable_history_budget"]),
            enable_difficulty_estimator=bool(defaults["enable_difficulty_estimator"]),
            enable_deer_trial=bool(defaults["enable_deer_trial"]),
            enable_dual_channel=bool(defaults["enable_dual_channel"]),
            consecutive_same_stop=int(defaults["consecutive_same_stop"]),
            enable_consecutive_same_stop=bool(defaults["enable_consecutive_same_stop"]),
            fixed_budget_tokens=int(defaults["fixed_budget_tokens"]),
            fixed_num_thinking=int(defaults["fixed_num_thinking"]),
            fixed_think_stride=int(defaults["fixed_think_stride"]),
            fixed_check_length=int(defaults["fixed_check_length"]),
            fixed_prob_th=float(defaults["fixed_prob_th"]),
            fixed_min_think_tokens=int(defaults["fixed_min_think_tokens"]),
            history_budget_multiplier=float(defaults["history_budget_multiplier"]),
            history_low_success_boost=float(defaults["history_low_success_boost"]),
            history_stride_percentile=float(defaults["history_stride_percentile"]),
            history_warmup_count=int(defaults["history_warmup_count"]),
            history_warmup_strategy=str(defaults["history_warmup_strategy"]),
            default_task_type=str(defaults["default_task_type"]),
            final_answer_max_tokens=int(defaults["final_answer_max_tokens"]),
            emit_phase_metrics=bool(defaults["emit_phase_metrics"]),
            verbose_stage_logging=bool(defaults["verbose_stage_logging"]),
        )

    @staticmethod
    def is_enabled() -> bool:
        """Whether the server allows a request to enter the Think Budget route."""
        return bool(get_runtime_config()["BUDGET_ENABLED"])

    @staticmethod
    def _validate_value(name: str, value: Any) -> None:
        if name not in _FIXED_REQUEST_FIELDS:
            raise ThinkBudgetConfigError(f"unknown Think Budget config key: {name}")
        if value is None:
            raise ThinkBudgetConfigError(f"Think Budget config {name} cannot be null")
        if name != "fixed_prob_th" and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ThinkBudgetConfigError(f"Think Budget config {name} must be a non-negative integer")
        if name == "fixed_prob_th" and (
            not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1
        ):
            raise ThinkBudgetConfigError(f"Think Budget config {name} must be in [0, 1]")

    @classmethod
    def _build_budget_config(cls, request: ChatCompletionRequest) -> tuple[BudgetConfig, set[str]]:
        """Build config from the six supported fixed request inputs only.

        A positive fixed value takes unconditional precedence over the matching
        dynamic calculation.  A value of zero means "do not fix this field" and
        keeps the original server-side calculation chain intact.
        """
        cfg = cls._default_config()
        external: dict[str, Any] = {}
        for name in _RETIRED_REQUEST_FIELDS:
            value = getattr(request, name, None)
            # Some old protocol versions materialize False/0 defaults.  They
            # are indistinguishable from an omitted field and have no effect;
            # every meaningful old input is rejected explicitly.
            if value not in (None, False, 0, ""):
                raise ThinkBudgetConfigError(
                    f"Think Budget request field is not supported: {name}")
            if name == "think_budget_config" and value is not None:
                raise ThinkBudgetConfigError(
                    "Think Budget request field is not supported: think_budget_config")

        for name in _FIXED_REQUEST_FIELDS:
            value = getattr(request, name, None)
            if value is not None:
                cls._validate_value(name, value)
                # 与参考 API server 一致，0/0.0 是统一的“未固定”值：
                # 不覆盖服务端静态配置，也不阻断动态决策。
                if value > 0:
                    external[name] = value

        # Fixed sub-parameters are external inputs as well. Project them onto
        # their canonical runtime fields before validation/merge.  Fixed inputs
        # are the only caller-controlled values, therefore there is no second
        # request representation with which they can conflict.
        fixed_aliases = {
            "fixed_check_length": "initial_thought_limit",
            "fixed_think_stride": "think_stride",
            "fixed_num_thinking": "trial_answers",
            "fixed_prob_th": "prob_th",
            "fixed_min_think_tokens": "min_think_tokens",
        }
        for fixed_name, runtime_name in fixed_aliases.items():
            fixed_value = external.get(fixed_name, getattr(cfg, fixed_name))
            if fixed_value:
                external.setdefault(runtime_name, fixed_value)
        fixed_budget = external.get("fixed_budget_tokens", cfg.fixed_budget_tokens)
        if fixed_budget:
            external.setdefault("token_budget", fixed_budget)

        for name, value in external.items():
            setattr(cfg, name, value)
        if 0 < cfg.max_api_calls < 2:
            raise ThinkBudgetConfigError(
                "max_api_calls must be 0 (unlimited) or at least 2 so a final answer can be generated")
        return cfg, set(external)

    @staticmethod
    def _set_dynamic(
        params: dict[str, Any], external_overrides: set[str], key: str, value: Any
    ) -> None:
        config_key = _DECISION_TO_CONFIG.get(key, key)
        if config_key not in external_overrides:
            params[key] = value

    @classmethod
    def _run_budget_decision_chain(
        cls,
        prompt_text: str,
        config: BudgetConfig,
        external_overrides: set[str],
        timing: TimingCollector,
        reference: str = "",
    ) -> Dict[str, Any]:
        """Run the original priority chain, but only fill non-external fields."""
        params: dict[str, Any] = {}
        if not config.BUDGET_ENABLED:
            return params

        def needs_core() -> bool:
            return bool(_CORE_DECISION_FIELDS - external_overrides)

        # Step 0.5/1: fixed budget remains the highest *computed* source. A
        # caller-provided individual field is never overwritten by this result.
        if config.fixed_budget_tokens > 0 and needs_core():
            with timing.measure("decision.fixed_budget"):
                fixed = config.fixed_budget_tokens
                check_length = min(fixed, config.initial_thought_limit)
                num_thinking = max(3, fixed // max(check_length, 1))
                values = {
                    "check_length": check_length,
                    "num_thinking": num_thinking,
                    "think_stride": max(50, fixed // num_thinking),
                    "prob_th": config.prob_th,
                    "token_budget": fixed,
                }
            for key, value in values.items():
                cls._set_dynamic(params, external_overrides, key, value)
            params["check_answer_len"] = TRIAL_ANSWER_LENGTH
            return params

        scene_info: dict[str, Any] = {
            "scene_type": config.scene_type,
            "typical_tokens": 0,
            "scene_confidence": 0.0,
        }
        task_type = config.default_task_type
        task_matched = False
        if needs_core() and config.enable_task_type_budget:
            with timing.measure("decision.scene_classifier"):
                scene_info = classify_scene(prompt_text)
            with timing.measure("decision.task_type"):
                task_match = classify_task_type(prompt_text, scene_info)
                task_type = task_match.get("task_type", config.default_task_type)
                task_matched = (
                    task_type != "general"
                    and float(task_match.get("task_confidence", 0.0)) > 0.0
                )
                task_budget = get_task_type_budget(task_type)
            for key in ("check_length", "think_stride", "num_thinking", "min_think_tokens", "check_answer_len", "prob_th"):
                cls._set_dynamic(params, external_overrides, key, task_budget.get(key))
            cls._set_dynamic(params, external_overrides, "token_budget", task_budget.get("typical_tokens", config.token_budget))

        # A matched task has a meaningful feature space for difficulty
        # estimation.  Established history must not overwrite that budget;
        # zero-history tasks are handled below as unlimited cold starts.
        if needs_core() and config.enable_difficulty_estimator and task_matched:
            with timing.measure("decision.difficulty"):
                diff_info = estimate_difficulty(
                    prompt_text,
                    reference,
                    scene_type=scene_info.get("scene_type", "general"),
                    scene_typical_tokens=scene_info.get("typical_tokens", 0),
                    scene_confidence=scene_info.get("scene_confidence", 0.0),
                )
                diff_budget = compute_budget_from_difficulty(diff_info.get("difficulty", 0.5))
            for key, value in diff_budget.items():
                cls._set_dynamic(params, external_overrides, key, value)
            params["difficulty_info"] = diff_info

        if needs_core() and config.enable_history_budget:
            from think_budget_vllm_adapter.history.history_budget import compute_history_budget

            with timing.measure("decision.history"):
                history = compute_history_budget(
                    prompt_text,
                    task_type=task_type,
                    scene_type=scene_info.get("scene_type", "general"),
                    config={
                        "history_budget_multiplier": config.history_budget_multiplier,
                        "history_low_success_boost": config.history_low_success_boost,
                        "history_stride_percentile": config.history_stride_percentile,
                        "history_warmup_count": config.history_warmup_count,
                        "history_warmup_strategy": config.history_warmup_strategy,
                        "custom_task_types_path": config.custom_task_types_path,
                    },
                )
            # Keep the decision record consistent with the hidden probe
            # invariant; task/history modules may still return legacy values.
            history["check_answer_len"] = TRIAL_ANSWER_LENGTH
            # A matched task with established history keeps its difficulty
            # budget.  A zero-history task still needs the unlimited cold start
            # even when the classifier gave it a known coarse label.
            apply_history_budget = (
                not task_matched or int(history.get("history_count", 0)) == 0
            )
            if apply_history_budget:
                for key, value in history.items():
                    if key in _DECISION_TO_CONFIG:
                        # History modules call the aggregate amount think_budget;
                        # DEER consumes token_budget.  Normalize at the adapter
                        # boundary so the value reaches the retained state machine.
                        output_key = "token_budget" if key == "think_budget" else key
                        cls._set_dynamic(params, external_overrides, output_key, value)
            params["history_info"] = history

        if needs_core():
            with timing.measure("decision.complexity"):
                params["complexity_info"] = estimate_complexity(prompt_text, reference)
        cls._set_dynamic(params, external_overrides, "task_type", task_type)
        cls._set_dynamic(params, external_overrides, "scene_type", scene_info.get("scene_type", "general"))
        # ``check_answer_len`` is a probe length, not the requested answer
        # length.  Dynamic task/history policies may report a legacy value,
        # but the state machine must always issue a 50-token hidden trial.
        params["check_answer_len"] = TRIAL_ANSWER_LENGTH
        return params

    @staticmethod
    async def _queue_as_async_gen_stream(
        queue: asyncio.Queue, context: BudgetRequestContext
    ) -> AsyncIterator[RequestOutput]:
        while True:
            item = await queue.get()
            if item is None:
                if context.orchestration_error:
                    raise RuntimeError("Think Budget orchestration failed; see server logs")
                break
            yield item

    @staticmethod
    async def _queue_as_async_gen(
        queue: asyncio.Queue, context: BudgetRequestContext
    ) -> AsyncIterator[RequestOutput]:
        item = await queue.get()
        if item is None:
            if context.orchestration_error:
                raise RuntimeError("Think Budget orchestration failed; see server logs")
            yield None
            return
        if item is not None:
            while True:
                item_inc = await queue.get()
                if item_inc is None:
                    if context.orchestration_error:
                        raise RuntimeError("Think Budget orchestration failed; see server logs")
                    break
                item.outputs[0].text += item_inc.outputs[0].text
                item.outputs[0].token_ids.extend(item_inc.outputs[0].token_ids)
            item.outputs[0].finish_reason = "stop"
            item.finished = True
        yield item

    @staticmethod
    def _build_managed_generator(
        orchestrator: AsyncReasoningOrchestrator,
        context: BudgetRequestContext,
        request: ChatCompletionRequest,
    ) -> AsyncGenerator[RequestOutput, None]:
        """与 DEER 保持相同的请求绑定式 producer/consumer 生命周期。"""
        async def generator() -> AsyncGenerator[RequestOutput, None]:
            producer_task = asyncio.create_task(orchestrator.run())
            consumer = (
                BudgetController._queue_as_async_gen_stream(context.result_queue, context)
                if request.stream else BudgetController._queue_as_async_gen(
                    context.result_queue, context)
            )
            try:
                async for item in consumer:
                    yield item
            finally:
                # 与 DEER 一致：客户端断开后立即取消当前父请求的子请求链。
                if not producer_task.done():
                    producer_task.cancel()
                    try:
                        await producer_task
                    except asyncio.CancelledError:
                        pass

        return generator()

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
        try:
            config, external_overrides = self._build_budget_config(request)
        except ThinkBudgetConfigError as exc:
            return serving_chat.create_error_response(str(exc))
        if not config.BUDGET_ENABLED:
            # The serving patch normally bypasses this method entirely.  Keep
            # this guard for mixed/stale deployments so the state machine can
            # never be created after the server master switch was disabled.
            return serving_chat.create_error_response(
                "Think Budget is disabled by server configuration")

        base_config = {
            "initial_thought_limit": config.initial_thought_limit,
            "think_stride": config.think_stride,
            "trial_answers": config.trial_answers,
            "trial_answer_length": config.trial_answer_length,
            "token_budget": config.token_budget,
            "prob_th": config.prob_th,
            "min_think_tokens": config.min_think_tokens,
        }
        print(
            "TB_REQUEST event=accepted "
            f"request_id={request_id} stream={bool(request.stream)} "
            f"external_overrides={sorted(external_overrides)} base_config={base_config}",
            flush=True,
        )

        # 优先使用 serving.py 在同一进程内计算的真实路由身份；它不会来自
        # HTTP JSON，因此不会扩张严格的公网参数面。直接调用控制器时再以
        # vLLM 服务对象回退解析，便于本地测试和旧补丁升级。
        route_identity = getattr(request, self.ROUTING_CONTEXT_ATTR, None)
        trace_headers = None
        engine_prompt_text = None
        reasoning_ended = None
        reasoning_parser_kwargs = None
        reasoning_parser = None
        response_parser = None
        chat_template_kwargs = None
        if isinstance(route_identity, dict):
            model_name = route_identity.get("model_name", model_name)
            lora_request = route_identity.get("lora_request", lora_request)
            data_parallel_rank = route_identity.get(
                "data_parallel_rank", data_parallel_rank)
            trace_headers = route_identity.get("trace_headers")
            engine_prompt_text = route_identity.get("prompt_text")
            reasoning_ended = route_identity.get("reasoning_ended")
            reasoning_parser_kwargs = route_identity.get("reasoning_parser_kwargs")
            reasoning_parser = route_identity.get("reasoning_parser")
            response_parser = route_identity.get("response_parser")
            chat_template_kwargs = route_identity.get("chat_template_kwargs")
        elif (isinstance(route_identity, tuple) and len(route_identity) == 3):
            # Compatibility with the previous serving patch during rolling
            # upgrades. New deployments always use the dict above.
            routed_model_name, routed_lora_request, routed_data_parallel_rank = route_identity
            if model_name is None:
                model_name = routed_model_name
            if lora_request is None:
                lora_request = routed_lora_request
            if data_parallel_rank is None:
                data_parallel_rank = routed_data_parallel_rank
        if lora_request is None:
            lora_request = serving_chat._maybe_get_adapters(
                request, supports_default_mm_loras=True)
        if model_name is None:
            model_name = serving_chat.models.model_name(lora_request)

        timing = TimingCollector(emit_phase_metrics=config.emit_phase_metrics)
        try:
            with timing.measure("decision.decode_prompt"):
                prompt_text = tokenizer.decode(
                    engine_inputs[0].get("prompt_token_ids", []), skip_special_tokens=True)
        except Exception:
            prompt_text = ""
            timing.durations_s["decision.decode_prompt"] += 0.0

        with timing.measure("decision.total"):
            decision_params = self._run_budget_decision_chain(
                prompt_text, config, external_overrides, timing)
        decision_wall_ms = (time.perf_counter() - timing.started_at) * 1000
        timing.record_phase(
            parent_request_id=request_id,
            child_request_id="",
            phase="decision",
            round_id=0,
            wall_ms=decision_wall_ms,
            controller_cpu_ms=decision_wall_ms,
            prompt_tokens=len(engine_inputs[0].get("prompt_token_ids", [])),
            generated_tokens=0,
            visible=False,
        )
        print(
            f"TB_DECISION request_id={request_id} params={decision_params} "
            f"timings={timing.snapshot()}",
            flush=True,
        )

        context = BudgetRequestContext(
            request=request,
            tokenizer=tokenizer,
            engine_prompt=engine_inputs[0],
            config=config,
            serving_chat=serving_chat,
            request_id=request_id,
            lora_request=lora_request,
            data_parallel_rank=data_parallel_rank,
            trace_headers=trace_headers,
            prompt_text=(engine_prompt_text or engine_inputs[0].get("prompt") or prompt_text),
            reasoning_ended=reasoning_ended,
            reasoning_parser_kwargs=reasoning_parser_kwargs,
            reasoning_parser=reasoning_parser,
            chat_template_kwargs=chat_template_kwargs,
            max_tokens_temp=0,
            result_queue=asyncio.Queue(),
            budget_start_time=time.time(),
            model_name=model_name,
            session_id=request_id,
            external_overrides=external_overrides,
            timing=timing,
        )
        context.update_budget_params(decision_params)
        context.max_tokens_temp = config.token_budget
        context.init_trial_evaluator()
        effective_config = {
            "initial_thought_limit": config.initial_thought_limit,
            "think_stride": config.think_stride,
            "trial_answers": config.trial_answers,
            "trial_answer_length": config.trial_answer_length,
            "token_budget": config.token_budget,
            "prob_th": config.prob_th,
            "min_think_tokens": config.min_think_tokens,
            "max_api_calls": config.max_api_calls,
        }
        print(
            "TB_REQUEST event=orchestrator_created "
            f"request_id={request_id} model={model_name} "
            f"effective_config={effective_config}",
            flush=True,
        )

        orchestrator = AsyncReasoningOrchestrator(context)
        response_generator = self._build_managed_generator(
            orchestrator, context, request)
        return await self._format_response(
            serving_chat, request, response_generator, request_id, model_name,
            conversation, tokenizer, request_metadata, reasoning_parser,
            response_parser, chat_template_kwargs)

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
                request, response_generator, request_id, model_name, conversation,
                tokenizer, request_metadata, reasoning_parser=reasoning_parser,
                chat_template_kwargs=chat_template_kwargs,
            )
        try:
            return await serving_chat.chat_completion_full_generator(
                request, response_generator, request_id, model_name, conversation,
                tokenizer, request_metadata, parser=response_parser,
            )
        except (ValueError, RuntimeError) as exc:
            return serving_chat.create_error_response(str(exc))
