"""模型客户端 — 统一封装 mg2hg Completions API 与 Chat API。

核心:
  model_call()        — Completions API (/v1/completions), budget_forcing 主路径 (同步)
  async_model_call()  — Completions API 异步版, call_model_async 使用 (异步)
  model_call_chat()   — Chat API (/v1/chat/completions), 仅 llm_judge 使用

连接管理:
  共享 httpx.Client + 连接池 (max_conn=8, keepalive=15s)
  atexit 自动清理, 解决 CLOSE_WAIT 连接泄漏

重试:
  3次重试, 指数退避 (2s, 4s, 6s)
  仅对网络错误和500错误重试, 其他异常直接抛出
"""
import httpx
from openai import OpenAI
import os
import json
import time
import asyncio
import atexit
import threading
import logging

from naie.models.config import get_model_config

os.environ['NO_PROXY'] = '*'

logger = logging.getLogger(__name__)

_shared_httpx_client = None
_httpx_lock = threading.Lock()

_MAX_RETRIES = 3
_RETRY_DELAY = 2.0


# 默认连接池参数 — 可通过 set_httpx_pool_size() 动态调整
_httpx_max_connections = 16
_httpx_max_keepalive = 4


def set_httpx_pool_size(max_connections: int = 64, max_keepalive_connections: int = 16):
    """动态调整 httpx 同步连接池大小。

    供 api_server 根据并发度调大连接池，避免连接池成为并发瓶颈。
    下次 _get_shared_httpx_client() 重建客户端时生效（已有客户端不受影响，
    但如果已关闭则会以新参数重建）。

    Args:
        max_connections: 最大连接数 (含活跃+空闲)
        max_keepalive_connections: 最大空闲长连接数
    """
    global _httpx_max_connections, _httpx_max_keepalive, _shared_httpx_client
    _httpx_max_connections = max_connections
    _httpx_max_keepalive = max_keepalive_connections
    # 关闭旧客户端，下次调用时以新参数重建
    with _httpx_lock:
        if _shared_httpx_client is not None and not _shared_httpx_client.is_closed:
            try:
                _shared_httpx_client.close()
            except Exception:
                pass
            _shared_httpx_client = None


def _get_shared_httpx_client():
    global _shared_httpx_client
    if _shared_httpx_client is None or _shared_httpx_client.is_closed:
        with _httpx_lock:
            if _shared_httpx_client is None or _shared_httpx_client.is_closed:
                _shared_httpx_client = httpx.Client(
                    verify=False,
                    timeout=httpx.Timeout(timeout=180.0, connect=30.0, read=240.0, write=30.0, pool=30.0),
                    limits=httpx.Limits(
                        max_connections=_httpx_max_connections,
                        max_keepalive_connections=_httpx_max_keepalive,
                        keepalive_expiry=30.0,
                    ),
                )
    return _shared_httpx_client


def _cleanup_client():
    global _shared_httpx_client
    if _shared_httpx_client is not None and not _shared_httpx_client.is_closed:
        try:
            _shared_httpx_client.close()
        except Exception:
            pass


atexit.register(_cleanup_client)


def _call_with_retry(fn, *args, **kwargs):
    """带重试的 API 调用 — 对网络错误/500重试3次, 其他异常直接抛出。"""
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout,
                httpx.PoolTimeout, httpx.ReadError, ConnectionError, OSError,
                TimeoutError) as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY * (attempt + 1)
                logger.warning(f"API call failed (attempt {attempt+1}/{_MAX_RETRIES}): {type(e).__name__}: {e}, retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"API call failed after {_MAX_RETRIES} attempts: {type(e).__name__}: {e}")
        except Exception as e:
            err_str = str(e)
            if '500' in err_str or 'Internal Server Error' in err_str or 'Connection' in err_str:
                last_exc = e
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAY * (attempt + 1)
                    logger.warning(f"API error (attempt {attempt+1}/{_MAX_RETRIES}): {err_str[:100]}, retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"API error after {_MAX_RETRIES} attempts: {err_str[:200]}")
            else:
                raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unexpected retry loop exit")


class model_client:
    """mg2hg 模型客户端 — Completions API 为主路径。"""

    def __init__(self, model_name: str):
        self.messages = []
        self.model_name = model_name
        config = get_model_config(model_name)
        self.client = OpenAI(
            api_key=config["token"],
            base_url=config["url"],
            http_client=_get_shared_httpx_client(),
            max_retries=0,
        )

    def model_call(self, prompt, max_token, stop=None, timeout=None):
        """Completions API — budget_forcing 主路径, 返回 text + logprobs + tokens。

        Args:
            prompt: 完整 prompt (含 <|im_start|> 格式)
            max_token: 最大生成 token 数
            stop: 可选 stop 序列
            timeout: 可选 per-call 超时 (秒), 覆盖默认 240s read timeout
        """
        def _do_call():
            kwargs = dict(
                model=self.model_name, prompt=prompt, max_tokens=max_token,
                temperature=0, logprobs=True, stop=stop,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            if timeout is not None:
                kwargs["timeout"] = timeout
            return self.client.completions.create(**kwargs)

        chat_response = _call_with_retry(_do_call)
        response = json.loads(chat_response.json())
        usage = response.get("usage", {})
        choice = response.get("choices", [{}])[0]
        logprobs_data = choice.get("logprobs")
        return {
            'content': choice.get("text", ""),
            'token_num': usage.get("completion_tokens", 0),
            'logprobs': logprobs_data.get("token_logprobs") if logprobs_data else None,
            'tokens': logprobs_data.get("tokens") if logprobs_data else None,
            'stop_reason': choice.get("finish_reason"),
            'prompt_tokens': usage.get("prompt_tokens", 0),
        }

    def model_call_chat(self, prompt, max_token, enable_thinking=False):
        def _do_call():
            return self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_token, temperature=0,
                extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
            )

        chat_response = _call_with_retry(_do_call)
        response = json.loads(chat_response.json())
        usage = response.get("usage", {})
        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        completion_details = usage.get("completion_tokens_details")
        return {
            'content': message.get("content", ""),
            'token_num': usage.get("completion_tokens", 0),
            'prompt_tokens': usage.get("prompt_tokens", 0),
            'thinking_tokens': completion_details.get("reasoning_tokens") if completion_details else 0,
            'reasoning': message.get("reasoning", ""),
        }


# ─────────────────────────────────────────────────────
# 异步 Completions API — 供 call_model_async 使用
# ─────────────────────────────────────────────────────

_ASYNC_MAX_RETRIES = 3
_ASYNC_RETRY_DELAY = 2.0

_shared_async_client = None
_async_client_lock = None

# 全局 API 请求级信号量 — 限制同时进行的 API 请求数
# BudgetForcing 单题可发起 3~10 次 API 调用，用请求级信号量
# 比题目级 semaphore 更精确，避免过多并发请求压垮 API 服务器
_global_api_semaphore = None
_global_api_max_concurrency = 0  # 仅存储配置值，Semaphore 在事件循环内按需创建


def set_api_concurrency(max_concurrent_requests: int):
    """设置全局 API 并发请求数上限。

    可在 asyncio.run() 之前或之后调用 — 仅存储配置值，
    实际的 asyncio.Semaphore 会在首次 _acquire_api_permit() 时
    在运行中的事件循环内按需创建，避免 Python 3.12+ 的
    "no running event loop" 报错。
    默认 0 = 不限制。
    """
    global _global_api_max_concurrency, _global_api_semaphore
    _global_api_max_concurrency = max_concurrent_requests
    # 重置已有信号量，下次 acquire 时会用新值重建
    _global_api_semaphore = None


class _api_permit:
    """API 请求许可的异步上下文管理器 — 自动 acquire/release 信号量。

    用法: async with _api_permit(): ...
    Semaphore 在首次使用时懒创建，确保在运行中的事件循环内。
    如果未设置并发限制 (_global_api_max_concurrency == 0)，则直接通过。
    """
    async def __aenter__(self):
        global _global_api_semaphore
        if _global_api_max_concurrency > 0 and _global_api_semaphore is None:
            _global_api_semaphore = asyncio.Semaphore(_global_api_max_concurrency)
        if _global_api_semaphore is not None:
            await _global_api_semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if _global_api_semaphore is not None:
            _global_api_semaphore.release()
        return False  # 不吞异常


async def _get_shared_async_client():
    """获取或创建共享 httpx.AsyncClient (懒初始化)。"""
    global _shared_async_client
    if _shared_async_client is None or _shared_async_client.is_closed:
        _shared_async_client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(timeout=180.0, connect=30.0, read=240.0, write=30.0, pool=30.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8, keepalive_expiry=30.0),
        )
    return _shared_async_client


async def async_close_client():
    """关闭共享异步客户端 (可选, 一般由进程退出自动处理)。"""
    global _shared_async_client
    if _shared_async_client is not None and not _shared_async_client.is_closed:
        await _shared_async_client.aclose()
        _shared_async_client = None


async def async_model_call(model_name: str, prompt: str, max_token: int,
                           stop=None, timeout=None):
    """异步 Completions API — budget_forcing.call_model_async 的底层调用。

    绕过 OpenAI SDK, 直接用 httpx.AsyncClient POST /v1/completions。
    接口与 model_client.model_call() 返回格式一致, 方便 call_model_async 无缝替换。

    Args:
        model_name: 模型名称 (从 config.py 获取 token/url)
        prompt: 完整 prompt (含 <|im_start|> 格式)
        max_token: 最大生成 token 数
        stop: 可选 stop 序列
        timeout: 可选 per-call 超时 (秒)
    Returns:
        dict: {content, token_num, logprobs, tokens, stop_reason, prompt_tokens}
    """
    config = get_model_config(model_name)
    api_key = config["token"]
    api_base = config["url"]

    payload = {
        "model": model_name,
        "prompt": prompt,
        "max_tokens": max_token,
        "temperature": 0,
        "logprobs": True,
        "stop": stop,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # 带重试的异步调用 (每次 API 请求都经过全局信号量)
    last_exc = None
    for attempt in range(_ASYNC_MAX_RETRIES):
        try:
            async with _api_permit():
                client = await _get_shared_async_client()
                request_timeout = timeout if timeout is not None else 180.0
                resp = await client.post(
                    f"{api_base}/completions",
                    json=payload,
                    headers=headers,
                    timeout=request_timeout,
                )
                resp_data = resp.json()
                usage = resp_data.get("usage", {})
                choice = resp_data.get("choices", [{}])[0]
                logprobs_data = choice.get("logprobs")
                return {
                    'content': choice.get("text", ""),
                    'token_num': usage.get("completion_tokens", 0),
                    'logprobs': logprobs_data.get("token_logprobs") if logprobs_data else None,
                    'tokens': logprobs_data.get("tokens") if logprobs_data else None,
                    'stop_reason': choice.get("finish_reason"),
                    'prompt_tokens': usage.get("prompt_tokens", 0),
                }
        except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout,
                httpx.PoolTimeout, httpx.ReadError, ConnectionError, OSError,
                TimeoutError) as e:
            last_exc = e
            if attempt < _ASYNC_MAX_RETRIES - 1:
                delay = _ASYNC_RETRY_DELAY * (attempt + 1)
                logger.warning(f"Async API call failed (attempt {attempt+1}/{_ASYNC_MAX_RETRIES}): "
                               f"{type(e).__name__}: {e}, retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Async API call failed after {_ASYNC_MAX_RETRIES} attempts: "
                             f"{type(e).__name__}: {e}")
        except Exception as e:
            err_str = str(e)
            if '500' in err_str or 'Internal Server Error' in err_str or 'Connection' in err_str:
                last_exc = e
                if attempt < _ASYNC_MAX_RETRIES - 1:
                    delay = _ASYNC_RETRY_DELAY * (attempt + 1)
                    logger.warning(f"Async API error (attempt {attempt+1}/{_ASYNC_MAX_RETRIES}): "
                                   f"{err_str[:100]}, retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Async API error after {_ASYNC_MAX_RETRIES} attempts: {err_str[:200]}")
            else:
                raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unexpected async retry loop exit")
