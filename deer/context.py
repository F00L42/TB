from __future__ import annotations

import asyncio

from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.inputs.engine import TokensInput
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


@dataclass
class DeerConfig:
    """DEER超参配置"""
    is_code: bool = False              # trial answer行为
    think_ratio: float = 0.9           # 最大思考比例
    token_budget: int = 2500           # 总思考长度
    trial_answers: int = 10            # 最多尝试生成多少次答案
    think_stride: int = 128            # 每生成思考多少token生成一次答案
    initial_thought_limit: int = 512   # 首次思考阈值
    trial_answer_length: int = 50      # trial answer最大长度
    prob_th = 0.95                     # 置信度阈值
    rep_exit = 1                       # 是否开启重复提前退出


@dataclass
class RequestStateContext:
    """封装单个请求的完整上下文，在状态之间传递。"""
    # 外部依赖
    request: ChatCompletionRequest
    tokenizer: TokenizerLike
    config: DeerConfig
    serving_chat: OpenAIServingChat  # 与vllm交互入口

    # 动态状态
    request_id: str
    engine_prompt: TokensInput
    trial_count: int = 0
    result_queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # 推理结果队列
    last_chunk: RequestOutput = None
    rep_count: int = 0
    last_stride_text: Optional[str] = None

    @property
    def engine_client(self):
        return self.serving_chat.engine_client

    async def stream_output(self, result):
        """透传推理结果"""
        await self.result_queue.put(result)

    def update_prompts(self, token_ids: list[int], res: RequestOutput):
        """更新token_id和last_chunk历史"""
        self.engine_prompt['prompt_token_ids'].extend(token_ids)
        self.last_chunk = res
