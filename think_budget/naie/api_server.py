"""Think Budget API Server — 兼容 OpenAI 格式的代理接口。

默认模型为 mg2hg；端点与凭据由 MG2HG_URL / MG2HG_KEY 环境变量提供。
模型字段仍映射为 mg2hg，除非调用方在外层服务修改该策略。

v3 改进:
  - 集成 scene_classifier 自动推断商用场景
  - 支持显式指定 scene_type (外部可覆盖自动推断)
  - 新增 difficulty_answer_len_base/max 参数
  - 历史查询集成 scene_type 过滤
  - 并发控制: asyncio.Semaphore 限制同时推理请求数
  - asyncio.to_thread 将同步推理卸载到线程池，不阻塞事件循环
  - httpx 连接池扩容匹配并发度

启动:
    python naie/api_server.py                         # 默认 port=8000, concurrency=8
    python naie/api_server.py --port 8001 --concurrency 4
    python naie/api_server.py --workers 2              # 多 worker 进程
    uvicorn naie.api_server:app --host 0.0.0.0 --port 8000

端点:
    POST /v1/chat/completions   — 主推理接口 (兼容 OpenAI 格式)
    GET  /health                — 健康检查
    GET  /v1/config             — 读取当前运行配置
    POST /v1/config             — 更新运行配置 (运行时覆盖)
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn
import os
import sys
import time
import asyncio
import argparse
import traceback
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('NO_PROXY', 'localhost,127.0.0.1')

from naie.inference.think_budget import call_model as budget_call_model
from naie.config.settings import THINK_BUDGET_CONFIG_47
from naie.models.model_client import model_client, set_httpx_pool_size
from naie.evaluation.runner import compute_thinking_tokens, parse_think_content
from naie.optimizations.scene_classifier import classify_scene, get_scene_typical_tokens
from naie.optimizations.task_type_budget import classify_task_type
from naie.optimizations.history_module import get_category_stats, get_scene_stats

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

app = FastAPI(title="Think Budget API", description="兼容OpenAI格式的BudgetForcing代理接口")

_INTERNAL_MODEL = 'mg2hg'

# ── 并发控制 ──
# 限制同时进行的推理请求数，避免压垮后端 API 服务器
# BudgetForcing 每个请求内部会发起 3~10 次 API 调用 (num_thinking 轮 + 1 次最终调用)
# concurrency=8 时, 最坏情况后端承受 ~80 并发 API 请求
_inference_semaphore: Optional[asyncio.Semaphore] = None
_server_concurrency = 4  # 默认值，可通过 --concurrency 覆盖


def _init_semaphore():
    """在 FastAPI startup 事件中初始化信号量 (此时事件循环已运行)。"""
    global _inference_semaphore
    _inference_semaphore = asyncio.Semaphore(_server_concurrency)
    logger.info(f"API Server 并发控制: max_concurrent_inferences={_server_concurrency}")


@app.on_event("startup")
async def startup_event():
    _init_semaphore()
    # 扩容 httpx 同步连接池以匹配并发度
    # 每个并发推理请求需要 1 个 httpx 连接，加上连接复用，池大小 = concurrency * 4
    pool_size = _server_concurrency * 4
    set_httpx_pool_size(max_connections=pool_size, max_keepalive_connections=_server_concurrency * 2)
    logger.info(f"httpx 连接池扩容: max_connections={pool_size}, max_keepalive={_server_concurrency * 2}")


class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = 'mg2hg'
    messages: List[Message]
    temperature: Optional[float] = 0
    max_tokens: Optional[int] = 8192
    enable_budget: Optional[bool] = True
    reference: Optional[str] = ''
    # 场景分类 (可选, 不指定则自动推断)
    scene_type: Optional[str] = None
    # 难度估计器参数
    enable_difficulty_estimator: Optional[bool] = None
    difficulty_check_length_base: Optional[int] = None
    difficulty_check_length_max: Optional[int] = None
    difficulty_min_think_base: Optional[int] = None
    difficulty_min_think_max: Optional[int] = None
    difficulty_stride_base: Optional[int] = None
    difficulty_stride_max: Optional[int] = None
    difficulty_num_thinking_base: Optional[int] = None
    difficulty_num_thinking_max: Optional[int] = None
    difficulty_answer_len_base: Optional[int] = None
    difficulty_answer_len_max: Optional[int] = None
    # 其他参数
    enable_deer_trial: Optional[bool] = None
    enable_soft_truncation: Optional[bool] = None
    check_length: Optional[int] = None
    think_stride: Optional[int] = None
    num_thinking: Optional[int] = None
    check_answer_len: Optional[int] = None
    prob_th: Optional[float] = None
    min_think_tokens: Optional[int] = None
    max_api_calls: Optional[int] = None
    enable_value_min_confidence: Optional[bool] = None
    enable_clean_context: Optional[bool] = None
    enable_safety_gate: Optional[bool] = None
    baseline_time_limit: Optional[float] = None
    # ─── 任务类型判断 ───
    enable_task_type_budget: Optional[bool] = None
    task_type: Optional[str] = None  # 手动覆盖任务类型 (retrieval/coding/math/reasoning/classification/...)
    default_task_type: Optional[str] = None  # 默认任务类型 — 无法判断时使用
    custom_task_types_path: Optional[str] = None  # 自定义任务类型配置路径
    # ─── 历史轨迹预算 ───
    enable_history_budget: Optional[bool] = None
    history_budget_multiplier: Optional[float] = None
    history_low_success_boost: Optional[float] = None
    history_stride_percentile: Optional[float] = None
    new_task_unlimited: Optional[bool] = None
    history_warmup_count: Optional[int] = None
    history_warmup_strategy: Optional[str] = None  # conservative/moderate/aggressive
    auto_cluster_threshold: Optional[int] = None  # 自动聚类新任务类型的历史条数阈值
    # ─── 人工可控超参 ───
    fixed_budget_tokens: Optional[int] = None  # 固定budget token消耗 (0=动态计算)
    fixed_num_thinking: Optional[int] = None   # 固定最大轮次 (0=动态计算)
    fixed_think_stride: Optional[int] = None   # 固定步长 (0=动态计算)
    fixed_check_length: Optional[int] = None   # 固定初始思考长度 (0=动态计算)
    fixed_prob_th: Optional[float] = None      # 固定置信度阈值 (0.0=使用prob_th)
    fixed_min_think_tokens: Optional[int] = None  # 固定最低思考token (0=动态计算)
    # ─── 置信度与高级参数 ───
    enable_full_chain_confidence: Optional[bool] = None  # 完整链置信度
    full_chain_min_percentile: Optional[float] = None     # 思维链最差百分比
    enable_dual_channel: Optional[bool] = None            # 混合双通道评估
    reasoning_min_percentile: Optional[float] = None      # 推理区 Min-% 百分位
    consecutive_same_stop: Optional[int] = None           # 连续相同次数阈值
    value_min_bottom_k: Optional[int] = None              # Value最低K个token取均值

    model_config = {"extra": "allow"}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages不能为空")

    user_message = request.messages[-1]
    if user_message.role != "user":
        raise HTTPException(status_code=400, detail="最后一条消息必须是user角色")

    # ── 并发控制: 限制同时推理请求数 ──
    # 超出并发上限的请求会排队等待，而非直接拒绝
    if _inference_semaphore is None:
        # 信号量未初始化 (不应该发生)，使用默认值创建
        _init_semaphore()

    async with _inference_semaphore:
        return await _do_chat_completions(request)


async def _do_chat_completions(request: ChatCompletionRequest):
    """实际推理逻辑 — 被 _inference_semaphore 保护。"""
    try:
        prompt = request.messages[-1].content
        max_tokens = request.max_tokens or 8192
        enable_budget = request.enable_budget if request.enable_budget is not None else True

        if not enable_budget:
            # 同步 model_call 通过 asyncio.to_thread 卸载到线程池，避免阻塞事件循环
            def _sync_call():
                client = model_client(_INTERNAL_MODEL)
                chat_prompt = "<|im_start|>user\n" + prompt + "<|im_end|>\n<|im_start|>assistant\n" + "<think>\n"
                return client.model_call(chat_prompt, max_tokens), chat_prompt

            response, chat_prompt = await asyncio.to_thread(_sync_call)

            raw_content = response.get('content', '')
            full_content = chat_prompt + raw_content
            thinking_content, answer_content = parse_think_content(full_content)
            reasoning_tokens = compute_thinking_tokens(thinking_content) if thinking_content else 0
            display_content = answer_content if answer_content else full_content
            return {
                "id": f"chatcmpl-{hash(prompt) % 1000000}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": _INTERNAL_MODEL,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": display_content,
                        "reasoning_content": thinking_content if thinking_content else None,
                    },
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": response.get('prompt_tokens', 0),
                    "completion_tokens": response.get('token_num', 0),
                    "reasoning_tokens": reasoning_tokens,
                },
            }

        # ─── 场景分类 ───
        # 外部显式指定 > 自动推断
        if request.scene_type:
            scene_info = {
                'scene_type': request.scene_type,
                'scene_confidence': 1.0,  # 显式指定视为高置信度
                'typical_tokens': get_scene_typical_tokens(request.scene_type),
            }
        else:
            scene_info = classify_scene(prompt)

        effective_scene_type = scene_info.get('scene_type', 'general')

        # ─── 任务类型推断 ───
        # 外部显式指定 task_type > 自动推断
        if request.task_type:
            classified_task_type = request.task_type
        else:
            task_info = classify_task_type(prompt, scene_info)
            classified_task_type = task_info.get('task_type', 'general')

        # ─── 历史统计 (按推断的 task_type 过滤, 而非硬编码 'naie') ───
        cat_stats = get_category_stats(
            prompt[:100], task_type=classified_task_type,
            scene_type=effective_scene_type,
        )
        scene_stats = get_scene_stats(effective_scene_type, task_type=classified_task_type)

        # 优先使用 category 级统计, 回退到 scene 级统计
        history_avg_tokens = cat_stats.get('avg_tokens', 0) or scene_stats.get('avg_tokens', 0)
        history_failure_rate = cat_stats.get('failure_rate', 0) or scene_stats.get('failure_rate', 0)
        history_count = cat_stats.get('count', 0) or scene_stats.get('count', 0)

        # ─── 构建 budget config ───
        cfg = dict(THINK_BUDGET_CONFIG_47)
        override_keys = [
            'check_length', 'think_stride', 'num_thinking', 'check_answer_len',
            'prob_th', 'min_think_tokens', 'max_api_calls',
            'enable_value_min_confidence', 'enable_clean_context',
            'enable_safety_gate', 'baseline_time_limit',
            'enable_difficulty_estimator', 'enable_deer_trial',
            'enable_soft_truncation',
            'difficulty_check_length_base', 'difficulty_check_length_max',
            'difficulty_min_think_base', 'difficulty_min_think_max',
            'difficulty_stride_base', 'difficulty_stride_max',
            'difficulty_num_thinking_base', 'difficulty_num_thinking_max',
            'difficulty_answer_len_base', 'difficulty_answer_len_max',
            # 任务类型判断
            'enable_task_type_budget', 'default_task_type', 'custom_task_types_path',
            # 历史轨迹预算
            'enable_history_budget', 'history_budget_multiplier',
            'history_low_success_boost', 'history_stride_percentile',
            'new_task_unlimited', 'history_warmup_count',
            'history_warmup_strategy', 'auto_cluster_threshold',
            # 人工可控超参
            'fixed_budget_tokens', 'fixed_num_thinking', 'fixed_think_stride',
            'fixed_check_length', 'fixed_prob_th', 'fixed_min_think_tokens',
            # 置信度与高级参数
            'enable_full_chain_confidence', 'full_chain_min_percentile',
            'enable_dual_channel', 'reasoning_min_percentile',
            'consecutive_same_stop', 'value_min_bottom_k',
        ]
        for k in override_keys:
            v = getattr(request, k, None)
            if v is not None:
                cfg[k] = v

        # task_type → task_type_override 映射
        if request.task_type:
            cfg['task_type_override'] = request.task_type

        # 注入历史统计和场景先验
        cfg['history_avg_tokens'] = history_avg_tokens
        cfg['history_failure_rate'] = history_failure_rate
        cfg['history_category_avg_tokens'] = cat_stats.get('avg_tokens', 0)
        cfg['history_category_failure_rate'] = cat_stats.get('failure_rate', 0)
        cfg['history_count'] = history_count

        start_time = time.time()
        # 同步 budget_call_model 通过 asyncio.to_thread 卸载到线程池，
        # 避免阻塞事件循环 (内部有同步 HTTP 请求 + time.sleep 重试)
        result = await asyncio.to_thread(
            budget_call_model,
            prompt=prompt,
            max_tokens=max_tokens,
            budget_config=cfg,
            reference=request.reference or '',
        )
        elapsed_time = time.time() - start_time

        content = result.get('content', '')
        thinking_content, answer_content = parse_think_content(content)

        reasoning_tokens = compute_thinking_tokens(thinking_content) if thinking_content else 0
        if thinking_content:
            display_content = f"<think>{thinking_content}</think>\n{answer_content}" if answer_content else f"<think>{thinking_content}</think>"
        else:
            display_content = answer_content if answer_content else content

        response = {
            "id": f"chatcmpl-{hash(prompt) % 1000000}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": _INTERNAL_MODEL,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": display_content,
                    "reasoning_content": thinking_content if thinking_content else None,
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": result.get('prompt_tokens', 0),
                "completion_tokens": result.get('token_num', 0),
                "reasoning_tokens": reasoning_tokens,
            },
            "budget_info": {
                "accept_reason": result.get('accept_reason', ''),
                "confidence": result.get('confidence'),
                "perplexity": result.get('perplexity'),
                "value_min_confidence": result.get('value_min_confidence'),
                "api_call_count": result.get('api_call_count', 0),
                "thinking_rounds": result.get('thinking_rounds', 0),
                "thinking_tokens": reasoning_tokens,
                "elapsed_time": round(elapsed_time, 2),
                "difficulty": result.get('complexity'),
                "scene_type": effective_scene_type,
                "scene_confidence": scene_info.get('scene_confidence', 0),
                "task_type": classified_task_type,
            },
        }
        logger.info(f"Budget: accept={result.get('accept_reason','')} think={reasoning_tokens}tok "
                    f"elapsed={elapsed_time:.1f}s scene={effective_scene_type}")
        return response

    except Exception as e:
        logger.error(f"chat_completions error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "concurrency": _server_concurrency,
        "semaphore_available": _inference_semaphore._value if _inference_semaphore else None,
    }


@app.get("/v1/config")
async def get_config():
    return {"config": THINK_BUDGET_CONFIG_47}


@app.post("/v1/config")
async def update_config(updates: Dict[str, Any]):
    from naie.config.settings import set_value
    for k, v in updates.items():
        set_value(k, v)
    return {"config": THINK_BUDGET_CONFIG_47}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Think Budget API Server')
    parser.add_argument('--port', type=int, default=8000, help='服务端口 (默认8000)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='监听地址')
    parser.add_argument('--concurrency', type=int, default=4,
                        help='最大并发推理请求数 (默认8)。BudgetForcing每请求内部3~10次API调用，'
                             'concurrency=8时后端最坏承受~80并发API请求')
    parser.add_argument('--workers', type=int, default=1,
                        help='uvicorn worker 进程数 (默认1)。多worker需确保后端API能承受 workers×concurrency 的总并发')
    args = parser.parse_args()

    # 设置并发度到模块级变量，startup 事件中读取
    _server_concurrency = args.concurrency

    print(f"[API Server] host={args.host} port={args.port} "
          f"concurrency={args.concurrency} workers={args.workers}")
    print(f"  最大并发推理请求: {args.concurrency}")
    print(f"  预估后端API最大并发: ~{args.concurrency * 10} (按num_thinking=8估算)")
    if args.workers > 1:
        print(f"  多 worker 总并发: {args.workers * args.concurrency} 推理请求")

    uvicorn.run(app, host=args.host, port=args.port, workers=args.workers)
