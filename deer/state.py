from __future__ import annotations

import copy
import math
import torch

from abc import ABC
from collections.abc import AsyncGenerator
from typing import Optional, Tuple, Type, TYPE_CHECKING
from vllm.logger import init_logger

from deer.context import RequestStateContext

if TYPE_CHECKING:
    from vllm.outputs import RequestOutput
    from vllm.sequence import SampleLogprobs


logger = init_logger(__name__)


def _calculate_average_max_prob_from_logprobs(
    is_code: bool,
    logprobs_list,
    policy='avg2'
) -> float:
    """
    Calculate average max token probability from logprobs list in vLLM CompletionOutput.
    Compute from the second generated token to the second-to-last token.
    policy: min, avg1: arithmetic mean, avg2: geometric mean
    """
    num_tokens = len(logprobs_list)
    result = 0.0
    # Note: removed because of vllm verson updates
    # if not is_code and list(logprobs_list[-1].values())[-1].decoded_token != '</think>':
    #     return result
    if num_tokens <= 1:
        return result

    start_index = 1
    end_index = num_tokens
    total_prob_sum = 0.0
    log_prob_sum = 0.0  # For geometric mean
    count_for_average = 0
    min_prob = 1.0

    for i in range(start_index, end_index):
        # Ensure index is valid and corresponding logprobs entry is not empty
        if i < len(logprobs_list) and logprobs_list[i]:
            try:
                logprob_obj = list(logprobs_list[i].values())[0]
                # Ensure object has .logprob attribute
                if hasattr(logprob_obj, 'logprob'):
                    prob = torch.exp(torch.tensor(logprob_obj.logprob)).item()
                    if prob < min_prob:
                        min_prob = prob
                    total_prob_sum += prob
                    log_prob_sum += math.log(max(prob, 1e-10))
                    count_for_average += 1
                else:
                    raise RuntimeError(f"Object at logprobs_list[{i}] doesn't have '.logprob' attribute.")
            except (IndexError, KeyError, AttributeError) as e:
                raise RuntimeError(f"Unable to process logprobs at logprobs_list[{i}]") from e
        else:
            raise RuntimeError(f"logprobs_list[{i}] is empty or invalid.")

    # Calculate average
    if policy == 'min':
        result = min_prob
    elif policy == 'avg1':
        result = total_prob_sum / count_for_average
    elif policy == 'avg2':
        result = math.exp(log_prob_sum / count_for_average)
    return result


class _BaseState(ABC):
    """
    状态的抽象基类
    """
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type['_BaseState']]:
        """
        处理当前状态的逻辑，并返回下一个状态的类。
        如果返回 None，表示流程结束。
        """
        raise NotImplementedError

    @staticmethod
    async def consume_output(
        gen: AsyncGenerator, context: Optional[RequestStateContext]
    ) -> Tuple[list[int], Optional[RequestOutput]]:
        stride_token_ids = []
        last_chunk = None
        async for chunk in gen:
            last_chunk = chunk
            out = chunk.outputs[0]
            stride_token_ids.extend(out.token_ids)
            if chunk.outputs[0].finish_reason:  # 本轮推理正常结束，但我们需要过滤停止信号以进一步处理。
                chunk.outputs[0].finish_reason = None
            await context.stream_output(chunk)
        return stride_token_ids, last_chunk


class _InitialThinkState(_BaseState):
    """状态1: 执行首次长程思考"""
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.config.initial_thought_limit <= 0:
            return _IterativeThinkState
        threshold = 128 - len(context.engine_prompt['prompt_token_ids']) % 128
        threshold += context.config.initial_thought_limit

        params = context.request.to_sampling_params(threshold, context.serving_chat.default_sampling_params)
        params.stop = ["</think>"]

        gen = context.engine_client.generate(
            context.engine_prompt, params, f"{context.request_id}_initial_think"
        )

        stride_token_ids, last_chunk = await _BaseState.consume_output(gen, context)
        context.update_prompts(stride_token_ids, last_chunk)

        if last_chunk and last_chunk.outputs[0].stop_reason == "</think>":
            return _AnswerState  # 思考已结束，直接回答
        else:
            return _IterativeThinkState  # 进入迭代思考


class _IterativeThinkState(_BaseState):
    """状态2: 执行后续的短程、迭代思考"""
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.trial_count >= context.config.trial_answers:
            return _AnswerState

        context.trial_count += 1
        print(f">>>deer iter: {context.trial_count}", flush=True)
        max_new_tokens_for_thought = context.config.think_stride \
            if context.config.think_stride > 0 \
            else int(context.serving_chat.model_config.max_model_len * context.config.think_ratio) - len(context.engine_prompt['prompt_token_ids'])
        params = context.request.to_sampling_params(max_new_tokens_for_thought, context.serving_chat.default_sampling_params)
        params.stop = ["</think>"]

        gen = context.engine_client.generate(
            context.engine_prompt,
            params,
            f"{context.request_id}_rethink_{context.trial_count}"
        )
        stride_token_ids, last_chunk = await _BaseState.consume_output(gen, context)

        stop_word = last_chunk.outputs[0].stop_reason
        if stop_word is not None:  # 在下一轮续写中添加本次的截断词
            if isinstance(stop_word, str):
                stride_token_ids.extend(context.tokenizer.encode(stop_word, add_special_tokens=False))
            else:
                stride_token_ids.append(stop_word)
        context.update_prompts(stride_token_ids, last_chunk)

        if last_chunk and last_chunk.outputs[0].stop_reason == "</think>":
            return _AnswerState  # 思考已结束，直接回答
        else:
            return _ConfidenceCheckState  # 思考未结束，尝试输出并验证trial_answer


class _ConfidenceCheckState(_BaseState):
    """状态3: 生成trial_answer, 检查重复率和置信度，决定是否继续思考"""
    @staticmethod
    async def consume_output(
            gen: AsyncGenerator, context: Optional[RequestStateContext]
    ) -> Tuple[SampleLogprobs, Optional[RequestOutput]]:
        stride_logprobs = []
        async for res in gen:
            out = res.outputs[0]
            stride_logprobs.extend(out.logprobs)

        return stride_logprobs, None,

    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.trial_count >= context.config.trial_answers:
            return _AnswerState
        ans_params = context.request.to_sampling_params(context.config.trial_answer_length, context.serving_chat.default_sampling_params)
        ans_params.logprobs = 1  # 为置信度检查准备
        if context.config.is_code:
            ans_prompt = (
                "I have to give the answer based on the thinking "
                "directly without explanation now.</think>\n"
            )
        else:
            ans_prompt = (
                "I have to give the answer based on the thinking "
                "directly without explanation now.\n**Final Answer**\n"
            )
            ans_params.stop = "</think>"
        ans_prompt_tokens = context.tokenizer.encode(ans_prompt, add_special_tokens=False)
        ans_engine_prompt = copy.deepcopy(context.engine_prompt)
        ans_engine_prompt['prompt_token_ids'].extend(ans_prompt_tokens)

        gen = context.engine_client.generate(
            ans_engine_prompt,
            ans_params,
            f"{context.request_id}_trial_ans",
            priority=context.request.priority
        )

        logprobs, _ = await _ConfidenceCheckState.consume_output(gen, None)

        # 置信度检测
        confidence = _calculate_average_max_prob_from_logprobs(context.config.is_code, logprobs)
        if confidence >= context.config.prob_th:
            return _AnswerState

        return _IterativeThinkState


class _AnswerState(_BaseState):
    """状态4: 生成最终答案"""
    @staticmethod
    async def handle(context: RequestStateContext) -> Optional[Type[_BaseState]]:
        if context.last_chunk:
            end_think_res = copy.deepcopy(context.last_chunk)
            end_think_res.outputs[0].text = "</think>"
            end_think_res.outputs[0].token_ids = context.tokenizer.encode("</think>", add_special_tokens=False)
            await context.stream_output(end_think_res)  # 向输出中加入</think>

        if context.last_chunk.outputs[0].stop_reason is None:  # 上一轮思考因为超长退出，强制生成答案
            ans_prompt = (
                "Considering the limited time by the user, "
                "I have to give the solution based on the thinking directly now.\n</think>.\n\n"
            )
        else:  # 置信度足够高，可以直接结束思考
            ans_prompt = "</think>"
        ans_prompt_tokens = context.tokenizer.encode(ans_prompt, add_special_tokens=False)
        context.engine_prompt['prompt_token_ids'].extend(ans_prompt_tokens)

        params = context.request.to_sampling_params(context.serving_chat.model_config.max_model_len - len(context.engine_prompt['prompt_token_ids']), context.serving_chat.default_sampling_params)

        async for res in context.engine_client.generate(
            context.engine_prompt,
            params,
            f"{context.request_id}_answer"
        ):
            await context.stream_output(res)
        return None  # 状态转换流程结束


class AsyncReasoningOrchestrator:
    """流程编排器，负责驱动状态机运行。"""
    def __init__(self, context: RequestStateContext):
        self.context = context

    async def run(self):
        """执行状态机的主循环"""
        current_state_class: Optional[Type[_BaseState]] = _InitialThinkState
        try:
            while current_state_class is not None:
                print(f">>>deer state: {str(current_state_class)}", flush=True)
                current_state_class = await current_state_class.handle(self.context)
        except Exception as e:
            import traceback
            logger.error(f"Error during CoT orchestration for {self.context.request_id}: {e}")
            logger.error(traceback.format_exc())
        finally:
            await self.context.stream_output(None)  # 确保最后总是发送结束信号
