from __future__ import annotations

import asyncio

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Union, TYPE_CHECKING

from deer.context import DeerConfig, RequestStateContext
from deer.state import AsyncReasoningOrchestrator

if TYPE_CHECKING:
    from vllm.entrypoints.chat_utils import ConversationMessage
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.entrypoints.openai.chat_completion.protocol import (
        ChatCompletionRequest, ChatCompletionResponse,
        ErrorResponse, RequestResponseMetadata)
    from vllm.inputs import EngineInput
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


class CoTController:
    """
    Controller for Chain-of-Thought (CoT) reasoning orchestration.

    This class manages the full lifecycle of a reasoning request:
      - Building configuration from request parameters.
      - Initializing context and orchestrator.
      - Handling async streaming and cancellation control.
    """
    @staticmethod
    async def _queue_as_async_gen_stream(
        queue: asyncio.Queue[RequestOutput | None],
    ) -> AsyncIterator[RequestOutput]:
        """Yield queued outputs unchanged until the ``None`` sentinel."""
        while (item := await queue.get()) is not None:
            yield item

    @staticmethod
    async def _queue_as_async_gen(
        queue: asyncio.Queue[RequestOutput | None],
    ) -> AsyncIterator[RequestOutput]:
        """Aggregate phase outputs into the final non-streaming response."""
        aggregated = None
        async for item in CoTController._queue_as_async_gen_stream(queue):
            if aggregated is None:
                aggregated = item
            else:
                # Keep vLLM's native aggregation semantics for every choice,
                # including token logprobs and the terminal stop metadata.
                aggregated.add(item, aggregate=True)

        if aggregated is not None:
            yield aggregated

    @staticmethod
    def _build_deer_config(request: ChatCompletionRequest) -> DeerConfig:
        """
        Build DeerConfig dynamically based on request parameters.

        Args:
            request: Incoming chat completion request.

        Returns:
            DeerConfig: The runtime configuration for the reasoning process.
        """
        return DeerConfig(
            initial_thought_limit=(
                request.deer_initial_thought_limit
                if request.deer_initial_thought_limit is not None
                else DeerConfig.initial_thought_limit
            ),
            think_stride=(
                request.deer_think_stride
                if request.deer_think_stride is not None
                else DeerConfig.think_stride
            ),
            is_code=(
                request.deer_task_is_code
                if request.deer_task_is_code is not None
                else DeerConfig.is_code
            ),
        )

    @staticmethod
    def _build_managed_generator(
        orchestrator: AsyncReasoningOrchestrator,
        request_context: RequestStateContext,
        request: ChatCompletionRequest
    ) -> AsyncGenerator[RequestOutput, None]:
        """
        Build a managed generator with producer-consumer coordination.

        Ensures that when the client disconnects or cancels, the background
        reasoning task (producer) is properly cancelled to release resources.
        """
        async def generator() -> AsyncGenerator[RequestOutput, None]:
            producer_task = asyncio.create_task(orchestrator.run())  # 在生成器启动时，创建后台生产者任务，并持有对它的引用
            consumer_gen = (
                CoTController._queue_as_async_gen_stream(request_context.result_queue)
                if request.stream
                else CoTController._queue_as_async_gen(request_context.result_queue)
            )  # 流式or非流式消费者逻辑（规避方案）
            try:
                async for item in consumer_gen:  # 从队列中迭代并实时产生结果给外部调用（Web框架）
                    yield item
            finally:  # 当这个生成器被外部取消时 (例如客户端断开连接)，`async for` 循环会在此处抛出 CancelledError，于是执行 finally 块，主动取消生产者任务。
                if not producer_task.done():
                    producer_task.cancel()
                    try:
                        await producer_task  # 等待任务响应取消，这可以确保 vLLM 的 abort() 完成
                    except asyncio.CancelledError:
                        pass  # 接收取消是预期的行为

        return generator()

    async def handle_request(
        self,
        serving_chat: OpenAIServingChat,
        conversation: list[ConversationMessage],
        engine_inputs: list[EngineInput],  # encoded prompts
        request: ChatCompletionRequest,
        request_id: str,
        tokenizer: TokenizerLike,
        request_metadata: RequestResponseMetadata,
        model_name: str,
    ) -> Union[AsyncGenerator[str, None], ChatCompletionResponse,
               ErrorResponse]:
        """
        处理 CoT 请求的入口点。
        在这个初始的“透传”版本中，我们只记录状态，然后直接调用核心执行逻辑。
        """
        # 若用户声明则更新算法超参
        deer_config = self._build_deer_config(request)

        # 1. 创建请求上下文，将所有与请求相关的动态信息和依赖项封装起来
        request_context = RequestStateContext(
            request=request,
            tokenizer=tokenizer,
            engine_prompt=engine_inputs[0],
            config=deer_config,
            serving_chat=serving_chat,
            request_id=request_id
        )

        # 2. 创建并启动流程编排器
        orchestrator = AsyncReasoningOrchestrator(request_context)
        response_generator = self._build_managed_generator(  # 构建最终的响应，并且能够响应取消并释放推理资源
            orchestrator, request_context, request
        )

        # 3. 返回编排器处理后的结果
        if request.stream:
            return serving_chat.chat_completion_stream_generator(
                request, response_generator, request_id, model_name,
                conversation, tokenizer, request_metadata)
        try:
            return await serving_chat.chat_completion_full_generator(
                request, response_generator, request_id, model_name,
                conversation, tokenizer, request_metadata)
        except ValueError as e:
            return serving_chat.create_error_response(str(e))
