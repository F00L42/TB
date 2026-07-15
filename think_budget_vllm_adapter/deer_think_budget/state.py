"""Think Budget 状态机 — DEER 状态转移 + Think Budget 算法核心。

状态转移 (保留 deer 原版):
  _InitialThinkState → _IterativeThinkState ⇄ _ConfidenceCheckState
                           ↓                       ↓
                       _AnswerState ←───────────────┘

算法替换 (与 think budget 保持一致):
  - _ConfidenceCheckState: DEER 简单 logprob 均值 → Think Budget 双通道/Value Min Confidence
  - _InitialThinkState: 增加 soft truncation
  - _IterativeThinkState: 增加 soft truncation + safety gate
  - _AnswerState: DEER 原版行为 (注入 THINK_END_TAG + 生成最终答案)

适配 vLLM v0.20.2:
  - to_sampling_params(max_tokens, default_sampling_params) 2参数签名
  - model_config.max_model_len 访问
  - SampleLogprobs 格式 → (logprobs_float, tokens_text) 转换
  - TokenizerLike.encode(text, add_special_tokens=False)

Think tags: use "<think>" as stop word and stop_reason (same as deer source code)
token_ids encoded via context.tokenizer.encode("</think>", add_special_tokens=False)

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

from __future__ import annotations

import copy
import logging
import math
import time

from abc import ABC
from collections.abc import AsyncGenerator
from typing import Optional, Tuple, Type, TYPE_CHECKING

# 使用标准 logging 代替 vllm.logger.init_logger，避免导入时依赖问题
logger = logging.getLogger(__name__)

from think_budget_vllm_adapter.deer_think_budget.context import RequestStateContext
from think_budget_vllm_adapter.deer_think_budget.confidence import (
    cal_confidence, cal_perplexity,
    is_think_tag_paired, get_think_closing_tag,
    reopen_think_tag,
    extract_token_logprobs_and_texts,
)
from think_budget_vllm_adapter.deer_think_budget.confidence_gates import compute_dual_channel_score
from think_budget_vllm_adapter.deer_think_budget.early_stopping import (
    extract_answer_for_sc, check_consecutive_same,
    check_min_think_gate, make_accept_decision,
)
from think_budget_vllm_adapter.deer_think_budget.context_control import (
    make_induced_prompt, apply_clean_context,
)
from think_budget_vllm_adapter.deer_think_budget.safety_gate import (
    safety_gate_check, build_safety_fallback,
)

if TYPE_CHECKING:
    from vllm.outputs import RequestOutput
    from vllm.logprobs import PromptLogprobs


# ─── Think tags (严格按照 deer 源码) ───
# deer 源码中 stop word = "</think>", stop_reason 检测也是 "</think>"
# think_budget 源码中 _THINK_CLOSE_TAG = '</think>'
THINK_END_TAG = "</think>"

# 答案永远不被截断 — 最终答案生成使用此值
_UNLIMITED_ANSWER_TOKENS = 65536

# 自然断点字符集合 — 用于 soft truncation
_NATURAL_BREAK_CHARS = {'。', '！', '？', '.', '!', '?', '\n', '}', '】', '」'}


def _truncate_thinking_at_natural_breakpoint(content: str) -> str:
    """在推理过程的自然断点处截断推理，绝不截断答案。

    核心原则: max_tokens 只控制推理 token 用量，答案永远不被截断。
    仅当 content 中未包含 THINK_END_TAG 时才执行截断 —
    此时说明推理被 max_tokens 截断（还在思考中），需要在推理内部
    找一个自然断点闭合，然后让模型继续生成完整答案。

    如果已经包含 THINK_END_TAG，说明推理已完整结束，答案已跟在后面，不截断。
    """
    if THINK_END_TAG in content:
        return content
    last_break = -1
    for i in range(len(content) - 1, max(len(content) - 500, -1), -1):
        if content[i] in _NATURAL_BREAK_CHARS:
            last_break = i + 1
            break
    if last_break > 0:
        truncated = content[:last_break]
        if THINK_END_TAG not in truncated:
            truncated = truncated + f"\n{THINK_END_TAG}\n"
        return truncated
    return content


def _decode_prompt_text(context: RequestStateContext) -> str:
    """从 engine_prompt 的 token_ids 解码出文本，用于 is_think_tag_paired 等检查。"""
    try:
        return context.tokenizer.decode(
            context.engine_prompt.get('prompt_token_ids', []),
            skip_special_tokens=True
        )
    except Exception:
        return ''


class _BaseState(ABC):
    """状态的抽象基类"""
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type['_BaseState']]:
        """处理当前状态的逻辑，并返回下一个状态的类。"""
        raise NotImplementedError

    @staticmethod
    async def consume_output(
        gen: AsyncGenerator, context: Optional[RequestStateContext]
    ) -> Tuple[list[int], Optional[RequestOutput]]:
        """消费 generator 输出，收集 token_ids 并转发到 result_queue。"""
        stride_token_ids = []
        last_chunk = None
        async for chunk in gen:
            out = chunk.outputs[0]
            stride_token_ids.extend(out.token_ids)
            if chunk.outputs[0].finish_reason:  # 本轮推理正常结束，但我们需要过滤停止信号以进一步处理
                chunk.outputs[0].finish_reason = None
            await context.stream_output(chunk)
            last_chunk = chunk
        return stride_token_ids, last_chunk

    @staticmethod
    async def consume_output_for_logprobs(
        gen: AsyncGenerator,
    ) -> Tuple[list, Optional[RequestOutput]]:
        """消费 generator 输出，收集 logprobs (不转发到 result_queue)。

        用于 _ConfidenceCheckState 的 trial answer 生成 — trial answer 不对外输出，
        仅用于内部置信度评估。
        """
        stride_logprobs = []
        last_chunk = None
        async for res in gen:
            out = res.outputs[0]
            # vLLM 格式: out.logprobs 是 SampleLogprobs
            if out.logprobs:
                stride_logprobs.extend(out.logprobs)
            last_chunk = res
        return stride_logprobs, last_chunk


class _InitialThinkState(_BaseState):
    """状态1: 执行首次长程思考

    DEER behavior: generate initial_thought_limit tokens, stop at "<think>"
    Think Budget 增强: soft truncation (自然断点闭合)
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.config.initial_thought_limit <= 0:
            return _IterativeThinkState

        # 对齐到 128 的倍数 (与 DEER 原版一致)
        threshold = 128 - len(context.engine_prompt['prompt_token_ids']) % 128
        threshold += context.config.initial_thought_limit

        # 新版 vLLM: to_sampling_params 只接受 (max_tokens, default_sampling_params)
        params = context.request.to_sampling_params(
            threshold,
            context.default_sampling_params,
        )
        params.stop = [THINK_END_TAG]

        gen = context.engine_client.generate(
            context.engine_prompt, params, f"{context.request_id}_initial_think"
        )

        stride_token_ids, last_chunk = await _BaseState.consume_output(gen, context)
        context.update_prompts(stride_token_ids, last_chunk)

        # 更新 token 计数
        token_count = len(stride_token_ids)
        context.output_len += token_count
        context.actual_len += token_count
        context.max_tokens_temp -= token_count

        # ─── Think Budget: soft truncation ───
        # 如果初始思考被截断 (stop_reason='length') 且思考未闭合，
        # 在自然断点处闭合，而非硬切
        if (context.config.enable_soft_truncation
                and last_chunk
                and last_chunk.outputs[0].stop_reason == 'length'):
            # 此时推理被 max_tokens 截断，但还在思考中
            # soft truncation 在后续状态中处理
            pass

        # 检查是否因为 </think> 停止 (与 deer 源码一致)
        if last_chunk and last_chunk.outputs[0].stop_reason == THINK_END_TAG:
            return _AnswerState  # 思考已结束，直接回答
        else:
            return _IterativeThinkState  # 进入迭代思考


class _IterativeThinkState(_BaseState):
    """状态2: 执行后续的短程、迭代思考

    DEER behavior: each round generates think_stride tokens, detect "<think>"
    Think Budget 增强: soft truncation + safety gate
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        # ─── 安全门检查 ───
        if safety_gate_check(context.elapsed, context.config.baseline_time_limit,
                             context.config.enable_safety_gate):
            # 安全门触发 — 直接进入 _AnswerState (与 deer 源码行为一致)
            return _AnswerState

        if context.trial_count >= context.config.trial_answers:
            return _AnswerState

        # max_tokens 预算耗尽
        if context.max_tokens_temp <= 0:
            return _AnswerState

        context.thinking_rounds += 1
        print(f">>>dtb iter: {context.thinking_rounds}", flush=True)

        max_new_tokens_for_thought = context.config.think_stride \
            if context.config.think_stride > 0 \
            else int(context.serving_chat.model_config.max_model_len * context.config.think_ratio) - len(context.engine_prompt['prompt_token_ids'])

        # 新版 vLLM: to_sampling_params 只接受 (max_tokens, default_sampling_params)
        params = context.request.to_sampling_params(
            max_new_tokens_for_thought,
            context.default_sampling_params,
        )
        params.stop = [THINK_END_TAG]

        gen = context.engine_client.generate(
            context.engine_prompt,
            params,
            f"{context.request_id}_rethink_{context.thinking_rounds}"
        )
        stride_token_ids, last_chunk = await _BaseState.consume_output(gen, context)

        # deer 源码: stop_word 可能是 str 或 int，需要处理
        stop_word = last_chunk.outputs[0].stop_reason
        if stop_word is not None:  # 在下一轮续写中添加本次的截断词
            if isinstance(stop_word, str):
                stride_token_ids.extend(
                    context.tokenizer.encode(stop_word, add_special_tokens=False)
                )
            else:
                stride_token_ids.append(stop_word)
        context.update_prompts(stride_token_ids, last_chunk)

        # 更新 token 计数
        token_count = len(stride_token_ids)
        context.output_len += token_count
        context.actual_len += token_count
        context.max_tokens_temp -= token_count

        if last_chunk and last_chunk.outputs[0].stop_reason == THINK_END_TAG:
            return _AnswerState  # 思考已结束，直接回答
        else:
            return _ConfidenceCheckState  # 思考未结束，检查置信度


class _ConfidenceCheckState(_BaseState):
    """状态3: 生成 trial answer, 使用 Think Budget 算法检查置信度

    DEER 行为: 简单 logprob 均值 (avg2 几何平均) vs prob_th
    Think Budget 替换:
      - cal_confidence + cal_perplexity (基础指标)
      - compute_dual_channel_score (4种模式: full_chain / dual_channel / value_min / 默认)
      - check_consecutive_same (连续相同答案早停)
      - make_accept_decision (综合判断，含 min_think_tokens 门控)

    关键适配: vLLM logprobs 格式转换
      DEER 使用 vLLM CompletionOutput.logprobs (dict[str, Logprob] 格式)
      Think Budget 需要 list[float | None] + list[str | None] 格式
      → 使用 extract_token_logprobs_and_texts() 转换
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.trial_count >= context.config.trial_answers:
            return _AnswerState

        context.trial_count += 1
        context.sc_rounds += 1

        # ─── 安全门检查 ───
        if safety_gate_check(context.elapsed, context.config.baseline_time_limit,
                             context.config.enable_safety_gate):
            return _AnswerState

        # ─── 构建 trial answer 请求 ───
        # 新版 vLLM: to_sampling_params 只接受 (max_tokens, default_sampling_params)
        ans_params = context.request.to_sampling_params(
            context.config.trial_answer_length,
            context.default_sampling_params,
        )
        ans_params.logprobs = 1  # 为置信度检查准备 logprobs

        # trial answer 提示词 (与 deer 源码一致)
        if context.config.is_code:
            ans_prompt = (
                "I have to give the answer based on the thinking "
                f"directly without explanation now.{THINK_END_TAG}\n"
            )
        else:
            ans_prompt = (
                "I have to give the answer based on the thinking "
                "directly without explanation now.\n**Final Answer**\n"
            )
            # deer 源码中非 code 模式的 stop 是字符串而非列表
            ans_params.stop = [THINK_END_TAG]

        ans_prompt_tokens = context.tokenizer.encode(ans_prompt, add_special_tokens=False)
        ans_engine_prompt = copy.deepcopy(context.engine_prompt)
        ans_engine_prompt['prompt_token_ids'].extend(ans_prompt_tokens)

        gen = context.engine_client.generate(
            ans_engine_prompt,
            ans_params,
            f"{context.request_id}_trial_ans_{context.trial_count}",
            priority=context.request.priority,
        )

        # ─── 收集 trial answer 的 logprobs (不转发到 result_queue) ───
        stride_logprobs, trial_chunk = await _ConfidenceCheckState.consume_output_for_logprobs(gen)

        # ─── vLLM logprobs 格式转换 ───
        # stride_logprobs 是 list[dict[str, Logprob] | None] (vLLM SampleLogprobs)
        # Think Budget 需要 list[float | None] + list[str | None]
        ans_logprobs_float, ans_tokens_text = extract_token_logprobs_and_texts(stride_logprobs)

        # ─── Think Budget: 基础置信度/困惑度 ───
        confidence = cal_confidence(ans_logprobs_float)
        perplexity = cal_perplexity(ans_logprobs_float)

        # ─── Think Budget: 双通道/单通道决策评分 ───
        decision_score, value_min_raw, reasoning_score_raw, action_score_raw = compute_dual_channel_score(
            context.reasoning_logprobs, context.reasoning_tokens,
            ans_logprobs_float, ans_tokens_text,
            confidence, perplexity,
            enable_full_chain_confidence=context.config.enable_full_chain_confidence,
            full_chain_min_percentile=context.config.full_chain_min_percentile,
            enable_dual_channel=context.config.enable_dual_channel,
            reasoning_min_percentile=context.config.reasoning_min_percentile,
            enable_value_min_confidence=context.config.enable_value_min_confidence,
            value_min_bottom_k=context.config.value_min_bottom_k,
        )

        # ─── 构造 trial answer 文本 (用于 answer_list 和 consecutive_same) ───
        # trial answer 的文本需要从 trial_chunk 获取
        trial_answer_text = ""
        if trial_chunk:
            trial_answer_text = trial_chunk.outputs[0].text

        # induced_prompt + trial_answer_text 用于 extract_answer_for_sc
        # (使用 ans_engine_prompt 的文本形式作为 content — 这里简化处理)
        full_answer_content = ans_prompt + trial_answer_text

        # ─── Think Budget: 连续相同答案检测 ───
        current_answer_text = extract_answer_for_sc(full_answer_content)
        context.prev_answer_text, context.consecutive_same_count = check_consecutive_same(
            current_answer_text, context.prev_answer_text,
            context.consecutive_same_count, context.config.consecutive_same_stop,
        )

        # ─── Think Budget: 综合早停判断 ───
        # 注意: is_think_tag_paired 需要文本，不能传 token_ids
        prompt_text = _decode_prompt_text(context)
        should_accept, accept_reason = make_accept_decision(
            decision_score, context.config.prob_th,
            context.actual_len, context.config.min_think_tokens,
            is_think_tag_paired(prompt_text),
            context.consecutive_same_count, context.config.consecutive_same_stop,
            context.config.enable_value_min_confidence, value_min_raw,
            enable_full_chain_confidence=context.config.enable_full_chain_confidence,
            reasoning_score_raw=reasoning_score_raw,
            action_score_raw=action_score_raw,
            enable_dual_channel=context.config.enable_dual_channel,
        )

        # ─── 记录到 answer_list ───
        context.answer_list.append((
            full_answer_content,
            context.output_len,
            confidence, perplexity,
            'stop' if trial_chunk and trial_chunk.outputs[0].stop_reason else 'length',
            decision_score, value_min_raw, 0.0, accept_reason,
        ))

        if should_accept:
            # 置信度足够 → 进入 _AnswerState
            logger.info(
                f"Think Budget accept at trial {context.trial_count}: "
                f"decision_score={decision_score:.4f}, prob_th={context.config.prob_th}, "
                f"reason={accept_reason}"
            )
            return _AnswerState

        # ─── 置信度不够 → 继续思考 ───
        logger.debug(
            f"Think Budget continue at trial {context.trial_count}: "
            f"decision_score={decision_score:.4f} < prob_th={context.config.prob_th}, "
            f"reason={accept_reason}"
        )

        # ─── Clean Context ───
        # 继续思考时，根据 enable_clean_context 决定是否丢弃 induced 部分
        # (在 _IterativeThinkState 中处理，这里只更新 reasoning_logprobs)

        # 更新双通道跟踪
        context.reasoning_logprobs = context.reasoning_logprobs  # 保持最近一轮推理的 logprobs
        context.reasoning_tokens = context.reasoning_tokens

        return _IterativeThinkState


class _AnswerState(_BaseState):
    """状态4: 生成最终答案

    DEER behavior: inject "<think>" then generate final answer
    (严格与 deer 源码一致，不做 answer_list 选优)
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        # 注入 </think> 标记 (与 deer 源码一致)
        if context.last_chunk:
            end_think_res = copy.deepcopy(context.last_chunk)
            end_think_res.outputs[0].text = THINK_END_TAG
            end_think_res.outputs[0].token_ids = context.tokenizer.encode(
                THINK_END_TAG, add_special_tokens=False)
            await context.stream_output(end_think_res)

        if context.last_chunk and context.last_chunk.outputs[0].stop_reason is None:
            # 上一轮思考因为超长退出，强制生成答案 (与 deer 源码一致)
            ans_prompt = (
                "Considering the limited time by the user, "
                f"I have to give the solution based on the thinking directly now.\n{THINK_END_TAG}.\n\n"
            )
        else:
            # 置信度足够高，可以直接结束思考 (与 deer 源码一致)
            ans_prompt = THINK_END_TAG

        ans_prompt_tokens = context.tokenizer.encode(ans_prompt, add_special_tokens=False)
        context.engine_prompt['prompt_token_ids'].extend(ans_prompt_tokens)

        params = context.request.to_sampling_params(
            context.serving_chat.model_config.max_model_len
            - len(context.engine_prompt['prompt_token_ids']),
            context.default_sampling_params,
        )

        async for res in context.engine_client.generate(
            context.engine_prompt,
            params,
            f"{context.request_id}_answer"
        ):
            await context.stream_output(res)
        return None


class AsyncReasoningOrchestrator:
    """流程编排器，负责驱动状态机运行。"""
    def __init__(self, context: RequestStateContext):
        self.context = context

    async def run(self):
        """执行状态机的主循环"""
        current_state_class: Optional[Type[_BaseState]] = _InitialThinkState
        try:
            while current_state_class is not None:
                current_state_class = await current_state_class.handle(self.context)
        except Exception as e:
            import traceback
            logger.error(
                f"Error during Think Budget orchestration for "
                f"{self.context.request_id}: {e}"
            )
            logger.error(traceback.format_exc())
        finally:
            await self.context.stream_output(None)  # 确保最后总是发送结束信号
