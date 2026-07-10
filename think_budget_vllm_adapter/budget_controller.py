"""Budget Controller — 带完整预算决策链的推理控制器。

基于 deer_think_budget 的 CoTController 扩展:
  - 在创建 context 之前执行 budget 决策链 (Step 0-5)
  - 使用 BudgetConfig/BudgetRequestContext 替代 ThinkBudgetConfig/RequestStateContext
  - 复用 deer_think_budget 的 AsyncReasoningOrchestrator 状态机
  - budget 决策链通过 config 参数传递给状态机

Budget 决策链:
  Step 0: BUDGET_ENABLED 总开关
  Step 0.5: FIXED_BUDGET_TOKEN 覆盖
  Step 1: 人工固定值覆盖
  Step 2: 任务类型判断 (scene_classifier -> task_type_budget)
  Step 3: 难度估计器 (difficulty_estimator -> budget映射)
  Step 4: 历史轨迹预算 (history_budget -> 三段式泛化)
  Step 5: 人工固定子参数覆盖
  Final: CONFIDENCE_THRESHOLD & PERCENTILE_STEP 覆盖

从 think_budget/budget_controller.py 迁移，调整导入路径:
  - deer/deer_think_budget → deer_think_budget
  - think_budget.* → think_budget_vllm_adapter.*
  - 适配 vLLM v0.20.2 接口
"""
from __future__ import annotations

import asyncio
import copy
import time

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Optional, Union, Dict, Any, TYPE_CHECKING

import logging
logger = logging.getLogger(__name__)

# ─── 从内嵌的 deer_think_budget 子模块导入基础状态机 ───
from think_budget_vllm_adapter.deer_think_budget.state import (
    _BaseState,
    _InitialThinkState,
    _IterativeThinkState,
    _ConfidenceCheckState,
    _AnswerState,
    AsyncReasoningOrchestrator,
)

# ─── 从 adapter 导入增强版 context ───
from think_budget_vllm_adapter.context import BudgetConfig, BudgetRequestContext

# ─── 从 adapter 导入兼容桥接层 ───
from think_budget_vllm_adapter.confidence_compat import (
    vllm_logprobs_to_flat,
    cal_confidence,
    is_think_tag_paired,
    get_think_closing_tag,
    reopen_think_tag,
)
from think_budget_vllm_adapter.optimizations.confidence_gates_compat import compute_dual_channel_score
from think_budget_vllm_adapter.optimizations.early_stopping_compat import (
    extract_answer_for_sc,
    check_consecutive_same,
    check_min_think_gate,
    make_accept_decision,
)
from think_budget_vllm_adapter.optimizations.context_control_compat import make_induced_prompt
from think_budget_vllm_adapter.optimizations.safety_gate_compat import safety_gate_check

# ─── 从 adapter 导入高级优化模块 ───
from think_budget_vllm_adapter.optimizations.complexity_analyzer import estimate_complexity
from think_budget_vllm_adapter.optimizations.difficulty_estimator import (
    estimate_difficulty,
    compute_budget_from_difficulty,
)
from think_budget_vllm_adapter.optimizations.scene_classifier import classify_scene
from think_budget_vllm_adapter.optimizations.task_type_budget import (
    classify_task_type,
    get_task_type_budget,
    register_new_task_type,
    is_known_task_type,
)
from think_budget_vllm_adapter.optimizations.token_filter_compat import set_task_key_names
from think_budget_vllm_adapter.history.history_budget import compute_history_budget

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.entrypoints.openai.chat_completion.protocol import (
        ChatCompletionRequest, ChatCompletionResponse,
        ChatMessage, ErrorResponse, RequestResponseMetadata)
    from vllm.inputs import EngineInput
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


# ─── 注入 token_filter 的 key names ───
_KEY_NAMES = {
    'tool', 'faultNumber', 'faultIMSI', 'faultPort', 'faultType',
    'fault', 'Number', 'IMSI', 'Url', 'Ip', 'APN', 'Time', 'Range',
    'Caller', 'result', 'answer', 'name', 'type', 'value',
    'faultUrl', 'faultIp', 'faultAPN', 'faultTimeRange',
    'isCaller', 'status', 'code', 'message', 'id', 'data',
    'error', 'success', 'query', 'response',
}
set_task_key_names(_KEY_NAMES)

# Think tags (must match deer_think_budget/state.py)
_THINK_CLOSE_TAG = get_think_closing_tag('')


# ═══════════════════════════════════════════════════════════════
# Budget Controller — 带决策链的推理控制器
# ═══════════════════════════════════════════════════════════════

class BudgetController:
    """Budget 推理控制器 — 替代 CoTController，新增预算决策链。

    与 CoTController 完全相同的 handle_request 接口，
    但在内部使用 budget 决策链动态计算推理参数。

    设计策略:
      1. 复用 deer_think_budget 的 AsyncReasoningOrchestrator 状态机
         (状态转移逻辑不变，通过 config 参数控制行为)
      2. 在创建 context 之前执行 budget 决策链
      3. 用决策结果更新 BudgetConfig，状态机自动读取正确参数

    与源码 think_budget/budget_controller.py 的区别:
      - 不定义 _BudgetConfidenceCheckState / _BudgetReasoningOrchestrator
        (复用 deer_think_budget 中已完善的 _ConfidenceCheckState + AsyncReasoningOrchestrator)
      - 决策链通过 config 参数传递给状态机，而非替换状态类
    """

    @staticmethod
    async def _queue_as_async_gen_stream(
            queue: asyncio.Queue
    ) -> AsyncIterator[RequestOutput]:
        """流式模式: 从队列迭代输出。"""
        while True:
            item = await queue.get()
            if item:
                yield item
            else:
                break

    @staticmethod
    async def _queue_as_async_gen(
            queue: asyncio.Queue
    ) -> AsyncIterator[RequestOutput]:
        """非流式模式: 聚合所有输出。"""
        item = await queue.get()
        if item:
            while True:
                item_inc = await queue.get()
                if item_inc:
                    item.outputs[0].text += item_inc.outputs[0].text
                    item.outputs[0].token_ids.extend(item_inc.outputs[0].token_ids)
                else:
                    break
            item.outputs[0].finish_reason = "stop"
            item.finished = "True"
        yield item

    @staticmethod
    def _build_budget_config(request: ChatCompletionRequest) -> BudgetConfig:
        """构建 BudgetConfig — 从请求参数读取用户指定的值。

        与 deer_think_budget CoTController._build_think_budget_config 类似，
        但额外读取 budget 专属字段 (budget_token_limit, budget_disabled 等)。
        """
        cfg = BudgetConfig(
            # DEER 原有参数 (与 CoTController 一致)
            initial_thought_limit=(
                getattr(request, 'tb_check_length', None)
                if getattr(request, 'tb_check_length', None) is not None
                else BudgetConfig.initial_thought_limit
            ),
            think_stride=(
                getattr(request, 'tb_think_stride', None)
                if getattr(request, 'tb_think_stride', None) is not None
                else BudgetConfig.think_stride
            ),
            trial_answers=(
                getattr(request, 'tb_num_thinking', None)
                if getattr(request, 'tb_num_thinking', None) is not None
                else BudgetConfig.trial_answers
            ),
            trial_answer_length=(
                getattr(request, 'tb_check_answer_len', None)
                if getattr(request, 'tb_check_answer_len', None) is not None
                else BudgetConfig.trial_answer_length
            ),
            prob_th=(
                getattr(request, 'tb_prob_th', None)
                if getattr(request, 'tb_prob_th', None) is not None
                else BudgetConfig.prob_th
            ),
            is_code=(
                getattr(request, 'tb_is_code', None)
                if getattr(request, 'tb_is_code', None) is not None
                else BudgetConfig.is_code
            ),
            min_think_tokens=(
                getattr(request, 'tb_min_think_tokens', None)
                if getattr(request, 'tb_min_think_tokens', None) is not None
                else BudgetConfig.min_think_tokens
            ),
            # Think Budget 基础参数 (与 CoTController 一致)
            enable_value_min_confidence=(
                getattr(request, 'tb_enable_value_min_confidence', None)
                if getattr(request, 'tb_enable_value_min_confidence', None) is not None
                else BudgetConfig.enable_value_min_confidence
            ),
            value_min_bottom_k=(
                getattr(request, 'tb_value_min_bottom_k', None)
                if getattr(request, 'tb_value_min_bottom_k', None) is not None
                else BudgetConfig.value_min_bottom_k
            ),
            enable_clean_context=(
                getattr(request, 'tb_enable_clean_context', None)
                if getattr(request, 'tb_enable_clean_context', None) is not None
                else BudgetConfig.enable_clean_context
            ),
            enable_soft_truncation=(
                getattr(request, 'tb_enable_soft_truncation', None)
                if getattr(request, 'tb_enable_soft_truncation', None) is not None
                else BudgetConfig.enable_soft_truncation
            ),
            consecutive_same_stop=(
                getattr(request, 'tb_consecutive_same_stop', None)
                if getattr(request, 'tb_consecutive_same_stop', None) is not None
                else BudgetConfig.consecutive_same_stop
            ),
            # Budget 决策链参数 (新增)
            fixed_budget_tokens=getattr(request, 'budget_token_limit', 0) or 0,
            BUDGET_ENABLED=not (getattr(request, 'budget_disabled', False) or False),
        )

        # budget_confidence_threshold 覆盖
        budget_conf_th = getattr(request, 'budget_confidence_threshold', None)
        if budget_conf_th is not None:
            cfg.prob_th = budget_conf_th
            cfg.prob_th_budget = budget_conf_th

        # budget_task_type_override 手动指定任务类型
        budget_task_override = getattr(request, 'budget_task_type_override', None)
        if budget_task_override:
            cfg.task_type = budget_task_override

        return cfg

    @staticmethod
    def _run_budget_decision_chain(
        prompt_text: str,
        config: BudgetConfig,
        reference: str = '',
    ) -> Dict[str, Any]:
        """执行 Budget 决策链 — 计算所有 budget 参数。

        与参考项目 budget_forcing.py 的 Step 0-5 对齐，
        但不执行模型调用，只计算参数。

        Args:
            prompt_text: 用户 prompt 文本 (用于任务类型/难度/场景判断)
            config: 初始 BudgetConfig (含用户指定参数)
            reference: 参考答案 (可选)

        Returns:
            dict: budget_params 字典，用于更新 BudgetRequestContext
        """
        params = {}

        # ─── Step 0: BUDGET_ENABLED 总开关 ───
        if not config.BUDGET_ENABLED:
            params['enable_confidence'] = False
            params['enable_task_type_budget'] = False
            params['enable_history_budget'] = False
            params['enable_difficulty_estimator'] = False
            return params

        # ─── Step 0.5: FIXED_BUDGET_TOKEN 覆盖 ───
        fixed_budget_tokens = config.fixed_budget_tokens

        # ─── Step 1: 人工固定值覆盖 (最高优先级) ───
        if fixed_budget_tokens > 0:
            params['check_length'] = min(fixed_budget_tokens, config.check_length)
            params['min_think_tokens'] = fixed_budget_tokens
            params['num_thinking'] = max(3, fixed_budget_tokens // config.check_length)
            params['think_stride'] = max(50, fixed_budget_tokens // params['num_thinking'])
            params['prob_th'] = config.prob_th
            return params

        # ─── Step 2: 任务类型判断 ───
        scene_info = classify_scene(prompt_text)
        scene_type = scene_info.get('scene_type', 'general')

        # 检查人工任务类型覆盖 (budget_task_type_override)
        task_type = config.task_type if config.task_type != 'general' else 'general'
        if config.enable_task_type_budget:
            if task_type == 'general':
                # 自动判断任务类型
                task_info = classify_task_type(prompt_text, scene_info)
                task_type = task_info.get('task_type', 'general')

            # 应用任务类型基础预算
            task_budget = get_task_type_budget(task_type)
            params['check_length'] = task_budget.get('check_length', config.check_length)
            params['think_stride'] = task_budget.get('think_stride', config.think_stride)
            params['num_thinking'] = task_budget.get('num_thinking', config.num_thinking)
            params['min_think_tokens'] = task_budget.get('min_think_tokens', config.min_think_tokens)
            params['check_answer_len'] = task_budget.get('check_answer_len', config.check_answer_len)
            params['prob_th'] = task_budget.get('prob_th', config.prob_th)

        params['task_type'] = task_type
        params['scene_type'] = scene_type

        # ─── Step 3: 难度估计器 ───
        if config.enable_difficulty_estimator:
            diff_info = estimate_difficulty(
                prompt_text, reference,
                scene_type=scene_type,
                scene_typical_tokens=scene_info.get('typical_tokens', 0),
                scene_confidence=scene_info.get('scene_confidence', 0.0),
            )
            diff_budget = compute_budget_from_difficulty(
                diff_info.get('difficulty', 0.5),
            )
            params['check_length'] = diff_budget['check_length']
            params['min_think_tokens'] = diff_budget['min_think_tokens']
            params['think_stride'] = diff_budget['think_stride']
            params['num_thinking'] = diff_budget['num_thinking']
            params['check_answer_len'] = diff_budget.get('check_answer_len', config.check_answer_len)
            params['difficulty_info'] = diff_info

        # ─── Step 4: 历史轨迹预算 ───
        if config.enable_history_budget:
            h_budget = compute_history_budget(
                prompt_text,
                task_type=task_type,
                scene_type=scene_type,
            )
            budget_mode = h_budget.get('budget_mode', 'cold_start')

            if budget_mode == 'unlimited':
                params['num_thinking'] = h_budget.get('num_thinking', 99)
                params['min_think_tokens'] = h_budget.get('min_think_tokens', 0)
            elif budget_mode in ('warm_start', 'hot_start'):
                if not config.enable_difficulty_estimator:
                    params['check_length'] = h_budget.get('check_length', params.get('check_length', config.check_length))
                    params['min_think_tokens'] = h_budget.get('min_think_tokens', params.get('min_think_tokens', config.min_think_tokens))
                    params['num_thinking'] = h_budget.get('num_thinking', params.get('num_thinking', config.num_thinking))
                h_stride = h_budget.get('think_stride', 0)
                if h_stride > 0:
                    if budget_mode == 'hot_start':
                        params['think_stride'] = h_stride
                    else:
                        params['think_stride'] = max(params.get('think_stride', config.think_stride), h_stride)

            params['history_info'] = h_budget

        # ─── Step 5: 人工固定子参数覆盖 ───
        # (目前仅通过 request 参数传入，暂无额外逻辑)

        # ─── 复杂度分析 ───
        params['complexity_info'] = estimate_complexity(prompt_text, reference)

        # ─── 开关参数 ───
        params['enable_confidence'] = True
        params['enable_value_min_confidence'] = config.enable_value_min_confidence
        params['enable_dual_channel'] = config.enable_dual_channel
        params['enable_full_chain_confidence'] = config.enable_full_chain_confidence
        params['enable_consecutive_same_stop'] = config.enable_consecutive_same_stop
        params['enable_clean_context'] = config.enable_clean_context
        params['enable_safety_gate'] = config.enable_safety_gate
        params['enable_deer_trial'] = config.enable_deer_trial

        return params

    @staticmethod
    def _build_managed_generator(
        orchestrator: AsyncReasoningOrchestrator,
        request_context: BudgetRequestContext,
        request: ChatCompletionRequest
    ) -> AsyncGenerator[RequestOutput, None]:
        """构建受管生成器 — 确保客户端断开时取消后台任务。"""
        async def generator() -> AsyncGenerator[RequestOutput, None]:
            producer_task = asyncio.create_task(orchestrator.run())
            consumer_gen = (
                BudgetController._queue_as_async_gen_stream(request_context.result_queue)
                if request.stream
                else BudgetController._queue_as_async_gen(request_context.result_queue)
            )
            try:
                async for item in consumer_gen:
                    yield item
            finally:
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
    ) -> Union[AsyncGenerator, object]:
        """处理 Budget 请求的入口点 — 与 CoTController.handle_request 相同接口。

        核心区别: 在创建 context 之前先执行 budget 决策链，
        然后通过 BudgetRequestContext 将决策结果传递给状态机。

        签名与 deer_think_budget CoTController.handle_request 一致，
        适配 vLLM v0.20.2。
        """
        # 1. 构建 BudgetConfig
        budget_config = self._build_budget_config(request)

        # 2. 从 prompt 文本提取预算决策
        # 尝试从 engine_inputs 的 token_ids 反推 prompt 文本
        prompt_text = ''
        try:
            if hasattr(tokenizer, 'decode'):
                prompt_text = tokenizer.decode(
                    engine_inputs[0].get('prompt_token_ids', []),
                    skip_special_tokens=True
                )
        except Exception:
            pass

        # 3. 执行 Budget 决策链
        budget_params = self._run_budget_decision_chain(
            prompt_text, budget_config
        )

        # 4. 计算初始 max_tokens_temp (总思考 token 预算)
        max_model_len = serving_chat.model_config.max_model_len
        prompt_token_len = len(engine_inputs[0].get('prompt_token_ids', []))
        initial_budget = budget_config.token_budget
        if initial_budget <= 0:
            initial_budget = int(max_model_len * budget_config.think_ratio) - prompt_token_len

        # 5. 创建增强版请求上下文
        request_context = BudgetRequestContext(
            request=request,
            tokenizer=tokenizer,
            engine_prompt=engine_inputs[0],
            config=budget_config,
            serving_chat=serving_chat,
            request_id=request_id,
            max_tokens_temp=initial_budget,
            budget_start_time=time.time(),
        )

        # 6. 用决策结果更新 context
        request_context.update_budget_params(budget_params)

        # 7. 初始化 trial evaluator (如果启用)
        request_context.init_trial_evaluator()

        # 8. 创建流程编排器 (复用 deer_think_budget 的状态机)
        orchestrator = AsyncReasoningOrchestrator(request_context)
        response_generator = self._build_managed_generator(
            orchestrator, request_context, request
        )

        # 9. 返回编排器处理后的结果
        model_name = serving_chat.models.model_name(None)

        if request.stream:
            return serving_chat.chat_completion_stream_generator(
                request, response_generator, request_id, model_name,
                conversation, tokenizer, request_metadata,
                reasoning_parser=None,
                chat_template_kwargs=None,
            )
        try:
            return await serving_chat.chat_completion_full_generator(
                request, response_generator, request_id, model_name,
                conversation, tokenizer, request_metadata,
                reasoning_parser=None,
            )
        except ValueError as e:
            return serving_chat.create_error_response(str(e))
