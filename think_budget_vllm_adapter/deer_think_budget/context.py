"""ThinkBudget 配置和请求上下文。

基于 DEER context.py 扩展，保留 DEER 原有参数，新增 Think Budget 全部超参。
适配 vLLM v0.20.2 接口 (TokenizerLike, EngineInput)。

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

from __future__ import annotations

import asyncio
import time

from typing import Any, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.outputs import RequestOutput
    from vllm.tokenizers import TokenizerLike


@dataclass
class ThinkBudgetConfig:
    """Think Budget 超参配置 — DEER 参数 + Think Budget 扩展参数。

    DEER 原有参数 (字段名保持兼容):
      is_code, think_ratio, token_budget, trial_answers, think_stride,
      initial_thought_limit, trial_answer_length, prob_th, rep_exit

    Think Budget 映射关系:
      initial_thought_limit = check_length (首次思考 token 数)
      think_stride          = think_stride (每轮思考增量)
      trial_answers         = num_thinking (最大迭代轮次)
      trial_answer_length   = check_answer_len (trial answer 长度)
      token_budget          = max_tokens (总思考 token 上限)
      prob_th               = prob_th (置信度阈值)
    """

    # ─── DEER 原有参数 ───
    is_code: bool = False                      # trial answer 行为
    think_ratio: float = 0.9                   # 最大思考比例
    token_budget: int = 2500                   # 总思考 token 预算 (= max_tokens)
    trial_answers: int = 10                    # 最大迭代轮次 (= num_thinking)
    think_stride: int = 128                    # 每轮思考增量 token
    initial_thought_limit: int = 512           # 首次思考阈值 (= check_length)
    trial_answer_length: int = 50              # trial answer 最大长度 (= check_answer_len)
    prob_th: float = 0.95                      # 置信度阈值
    rep_exit: int = 1                          # 重复提前退出 (保留兼容)

    # ─── P3.1 Value Min Confidence ───
    enable_value_min_confidence: bool = True   # Value 最低概率开关
    value_min_bottom_k: int = 5                # 取最低 K 个 token

    # ─── P3.4 完整思维链+答案置信度 ───
    enable_full_chain_confidence: bool = False  # 完整链置信度 AND 门
    full_chain_min_percentile: float = 0.1     # 思维链最差百分比

    # ─── 双通道评估 ───
    enable_dual_channel: bool = False          # 双通道混合评估
    reasoning_min_percentile: float = 0.1      # 推理区 Min-%

    # ─── 安全门 ───
    enable_safety_gate: bool = False           # 超时自动回退
    baseline_time_limit: float = 90.0          # 基线时间限制 (秒)

    # ─── 路由参数 ───
    min_think_tokens: int = 100                # 最低思考 token 数 (软门控)

    # ─── P2.2 连续相同答案早停 ───
    consecutive_same_stop: int = 2             # 连续 N 轮一致则提前接受 (0=禁用)

    # ─── 任务键名 (供 token_filter 使用) ───
    task_key_names: set[str] = field(default_factory=set)


@dataclass
class RequestStateContext:
    """封装单个请求的完整上下文，在状态之间传递。

    扩展自 DEER RequestStateContext，新增 Think Budget 运行时状态。
    """
    # 外部依赖
    request: ChatCompletionRequest
    tokenizer: TokenizerLike
    config: ThinkBudgetConfig
    serving_chat: OpenAIServingChat  # 与 vLLM 交互入口

    # 动态状态 — DEER 原有
    request_id: str
    engine_prompt: dict  # EngineInput (TokensInput TypedDict)
    # Budget 决策只发生一次；以下身份必须随每个 DEER child request
    # 一起传给 vLLM，确保 LoRA 与数据并行路由不在中途丢失。
    lora_request: Any = None
    data_parallel_rank: Optional[int] = None
    trial_count: int = 0
    result_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    last_chunk: Optional[RequestOutput] = None
    # 回答态与异常收口共用，保证可见的 </think> 至多补发一次。
    think_end_emitted: bool = False
    rep_count: int = 0
    last_stride_text: Optional[str] = None

    # ─── Think Budget 新增运行时状态 ───
    output_len: int = 0                       # 累计输出 token 数
    actual_len: int = 0                       # 实际思考 token 数
    max_tokens_temp: int = 0                  # 剩余可用 token 预算
    api_call_count: int = 0                   # API 调用计数
    thinking_rounds: int = 0                  # 思考轮次
    sc_rounds: int = 0                        # 置信度检查轮次
    budget_start_time: float = field(default_factory=time.time)

    # 答案追踪
    answer_list: list = field(default_factory=list)  # [(content, output_len, confidence, perplexity, stop_reason, decision_score, value_min_raw, ln_score, accept_reason)]
    prev_answer_text: Optional[str] = None
    consecutive_same_count: int = 0

    # 双通道: 跟踪最近一轮推理的 logprobs/tokens
    reasoning_logprobs: list | None = None    # list[float | None]
    reasoning_tokens: list | None = None      # list[str | None]

    @property
    def engine_client(self):
        return self.serving_chat.engine_client

    @property
    def default_sampling_params(self):
        return self.serving_chat.default_sampling_params

    def engine_request_kwargs(self) -> dict[str, Any]:
        """返回每个 child request 必须保持一致的 vLLM 调度参数。"""
        return {
            "lora_request": self.lora_request,
            "priority": getattr(self.request, "priority", None) or 0,
            "data_parallel_rank": self.data_parallel_rank,
        }

    async def stream_output(self, result):
        """透传推理结果到 result_queue。"""
        await self.result_queue.put(result)

    def update_prompts(self, token_ids: list[int], res: RequestOutput):
        """更新 token_id 和 last_chunk 历史。"""
        self.engine_prompt['prompt_token_ids'].extend(token_ids)
        self.last_chunk = res

    def elapsed(self) -> float:
        """返回从 budget 开始到现在的耗时 (秒)。"""
        return time.time() - self.budget_start_time
