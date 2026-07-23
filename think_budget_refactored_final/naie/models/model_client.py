"""模型客户端 — 统一封装 OpenAI-compatible Completions API 与 Chat API。

核心:
  model_call()        — Completions API (/v1/completions), budget_forcing 主路径 (同步)
  async_model_call()  — Completions API 异步版, call_model_async 使用 (异步)
  model_call_chat()   — Chat API (/v1/chat/completions), 仅 llm_judge 使用

连接管理:
  共享 httpx.Client + 连接池 (参数从 budget_config.yaml 加载)
  atexit 自动清理, 解决 CLOSE_WAIT 连接泄漏

重试:
  参数从 budget_config.yaml 加载 (max_retries, retry_delay)
  仅对网络错误和500错误重试, 其他异常直接抛出
"""
import httpx
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised only in incomplete environments
    OpenAI = None
import os
import json
import time
import asyncio
import atexit
import threading
import logging
from typing import Any, AsyncIterator

from naie.models.config import get_model_config
from naie.config.unified_config import get_model_client_config

os.environ['NO_PROXY'] = '*'

logger = logging.getLogger(__name__)

_shared_httpx_client = None
_httpx_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════
# 配置加载(从 YAML)
# ═══════════════════════════════════════════════════════════════

def _load_config():
    """从 budget_config.yaml 加载模型客户端配置。"""
    try:
        return get_model_client_config()
    except Exception:
        return {}


_CLIENT_CFG = _load_config()


def _resolve_api_key(config):
    """Return a non-empty key for OpenAI-compatible backends.

    The OpenAI SDK requires a non-empty api_key even when the target endpoint is
    a local or internal OpenAI-compatible service that does not authenticate.
    Real keys still come from model-specific config/env first; the fallback only
    prevents client construction from failing before the request reaches the
    configured backend.
    """
    return (
        config.get("token")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("OPENAI_ADMIN_KEY")
        or _CLIENT_CFG.get("dummy_api_key")
        or "EMPTY"
    )


def _verify_tls():
    """Keep certificate validation enabled unless an explicit local override is set."""
    return _CLIENT_CFG.get('verify_ssl', True)


def _get_timeout_config():
    """获取超时配置。"""
    t = _CLIENT_CFG.get('timeout', {})
    return httpx.Timeout(
        timeout=t.get('total', 180.0),
        connect=t.get('connect', 30.0),
        read=t.get('read', 240.0),
        write=t.get('write', 30.0),
        pool=t.get('pool', 30.0),
    )


def _get_sync_pool_config():
    """获取同步连接池配置。"""
    p = _CLIENT_CFG.get('connection_pool', {}).get('sync', {})
    return httpx.Limits(
        max_connections=p.get('max_connections', 16),
        max_keepalive_connections=p.get('max_keepalive_connections', 4),
        keepalive_expiry=p.get('keepalive_expiry', 30.0),
    )


def _get_async_pool_config():
    """获取异步连接池配置。"""
    p = _CLIENT_CFG.get('connection_pool', {}).get('async', {})
    return httpx.Limits(
        max_connections=p.get('max_connections', 32),
        max_keepalive_connections=p.get('max_keepalive_connections', 8),
        keepalive_expiry=p.get('keepalive_expiry', 30.0),
    )


def _get_retry_config():
    """获取重试配置。"""
    return {
        'max_retries': _CLIENT_CFG.get('max_retries', 3),
        'retry_delay': _CLIENT_CFG.get('retry_delay', 2.0),
    }


def _get_payload_defaults():
    """获取API请求默认参数。"""
    return _CLIENT_CFG.get('payload_defaults', {})


# ═══════════════════════════════════════════════════════════════
# 动态调整接口
# ═══════════════════════════════════════════════════════════════

_httpx_max_connections = None  # None = 使用 YAML 配置
_httpx_max_keepalive = None


def set_httpx_pool_size(max_connections: int = 64, max_keepalive_connections: int = 16):
    """动态调整 httpx 同步连接池大小。

    供 api_server 根据并发度调大连接池，避免连接池成为并发瓶颈。
    下次 _get_shared_httpx_client() 重建客户端时生效。
    """
    global _httpx_max_connections, _httpx_max_keepalive, _shared_httpx_client
    _httpx_max_connections = max_connections
    _httpx_max_keepalive = max_keepalive_connections
    with _httpx_lock:
        if _shared_httpx_client is not None and not _shared_httpx_client.is_closed:
            try:
                _shared_httpx_client.close()
            except Exception:
                pass
            _shared_httpx_client = None


# ═══════════════════════════════════════════════════════════════
# 共享客户端管理
# ═══════════════════════════════════════════════════════════════

def _get_shared_httpx_client():
    """获取或创建共享 httpx 同步客户端(参数从 YAML 加载)。"""
    global _shared_httpx_client
    if _shared_httpx_client is None or _shared_httpx_client.is_closed:
        with _httpx_lock:
            if _shared_httpx_client is None or _shared_httpx_client.is_closed:
                # 动态覆盖优先于 YAML
                if _httpx_max_connections is not None:
                    limits = httpx.Limits(
                        max_connections=_httpx_max_connections,
                        max_keepalive_connections=_httpx_max_keepalive or 16,
                        keepalive_expiry=30.0,
                    )
                else:
                    limits = _get_sync_pool_config()
                _shared_httpx_client = httpx.Client(
                    verify=_verify_tls(),
                    timeout=_get_timeout_config(),
                    limits=limits,
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


# ═══════════════════════════════════════════════════════════════
# 重试逻辑(统一同步/异步)
# ═══════════════════════════════════════════════════════════════

_RETRYABLE_EXCEPTIONS = (
    httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout,
    httpx.PoolTimeout, httpx.ReadError, ConnectionError, OSError,
    TimeoutError,
)


def _is_retryable_error(err: Exception) -> bool:
    """判断异常是否可重试(网络错误/500)。"""
    if isinstance(err, _RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(err, httpx.HTTPStatusError):
        status_code = err.response.status_code
        return status_code == 408 or status_code == 429 or status_code >= 500
    err_str = str(err)
    if '500' in err_str or 'Internal Server Error' in err_str or 'Connection' in err_str:
        return True
    return False


def _call_with_retry(fn, *args, **kwargs):
    """带重试的同步 API 调用(参数从 YAML 加载)。"""
    retry_cfg = _get_retry_config()
    max_retries = retry_cfg['max_retries']
    retry_delay = retry_cfg['retry_delay']
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except _RETRYABLE_EXCEPTIONS as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = retry_delay * (attempt + 1)
                logger.warning(f"API call failed (attempt {attempt+1}/{max_retries}): {type(e).__name__}: {e}, retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"API call failed after {max_retries} attempts: {type(e).__name__}: {e}")
        except Exception as e:
            if _is_retryable_error(e):
                last_exc = e
                if attempt < max_retries - 1:
                    delay = retry_delay * (attempt + 1)
                    logger.warning(f"API error (attempt {attempt+1}/{max_retries}): {str(e)[:100]}, retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"API error after {max_retries} attempts: {str(e)[:200]}")
            else:
                raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unexpected retry loop exit")


async def _async_call_with_retry(fn, *args, **kwargs):
    """带重试的异步 API 调用(参数从 YAML 加载，复用 _is_retryable_error)。"""
    retry_cfg = _get_retry_config()
    max_retries = retry_cfg['max_retries']
    retry_delay = retry_cfg['retry_delay']
    last_exc = None
    for attempt in range(max_retries):
        try:
            return await fn(*args, **kwargs)
        except _RETRYABLE_EXCEPTIONS as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = retry_delay * (attempt + 1)
                logger.warning(f"Async API call failed (attempt {attempt+1}/{max_retries}): "
                               f"{type(e).__name__}: {e}, retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Async API call failed after {max_retries} attempts: "
                             f"{type(e).__name__}: {e}")
        except Exception as e:
            if _is_retryable_error(e):
                last_exc = e
                if attempt < max_retries - 1:
                    delay = retry_delay * (attempt + 1)
                    logger.warning(f"Async API error (attempt {attempt+1}/{max_retries}): "
                                   f"{str(e)[:100]}, retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Async API error after {max_retries} attempts: {str(e)[:200]}")
            else:
                raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unexpected async retry loop exit")


# ═══════════════════════════════════════════════════════════════
# 同步模型客户端
# ═══════════════════════════════════════════════════════════════

class model_client:
    """OpenAI-compatible model client; Completions API is the main path."""

    def __init__(self, model_name: str):
        self.messages = []
        self.model_name = model_name
        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is required for model_client. "
                "Install project dependencies with `pip install -r requirements.txt`."
            )
        config = get_model_config(model_name)
        self.client = OpenAI(
            api_key=_resolve_api_key(config),
            base_url=config["url"],
            http_client=_get_shared_httpx_client(),
            max_retries=0,
        )

    def model_call(self, prompt, max_token, stop=None, timeout=None):
        """Completions API — budget_forcing 主路径。"""
        payload_defaults = _get_payload_defaults()

        def _do_call():
            kwargs = dict(
                model=self.model_name, prompt=prompt, max_tokens=max_token,
                temperature=payload_defaults.get('temperature', 0),
                logprobs=payload_defaults.get('logprobs', True),
                stop=stop,
                extra_body=payload_defaults.get('extra_body', {"chat_template_kwargs": {"enable_thinking": False}}),
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

    def model_call_stream(self, prompt, max_token, stop=None, timeout=None):
        """Streaming Completions API path for non-budget API streaming."""
        payload_defaults = _get_payload_defaults()

        def _do_call():
            kwargs = dict(
                model=self.model_name,
                prompt=prompt,
                max_tokens=max_token,
                temperature=payload_defaults.get('temperature', 0),
                logprobs=False,
                stop=stop,
                stream=True,
                extra_body=payload_defaults.get('extra_body', {"chat_template_kwargs": {"enable_thinking": False}}),
            )
            if timeout is not None:
                kwargs["timeout"] = timeout
            return self.client.completions.create(**kwargs)

        stream = _call_with_retry(_do_call)
        for chunk in stream:
            if hasattr(chunk, "model_dump"):
                data = chunk.model_dump()
            else:
                try:
                    data = json.loads(chunk.json())
                except Exception:
                    data = chunk if isinstance(chunk, dict) else {}
            choice = data.get("choices", [{}])[0]
            text = choice.get("text", "")
            if text:
                yield {"content": text, "finish_reason": None}
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                yield {"content": "", "finish_reason": finish_reason}

    def model_call_chat(self, prompt, max_token, enable_thinking=False):
        payload_defaults = _get_payload_defaults()

        def _do_call():
            return self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_token,
                temperature=payload_defaults.get('temperature', 0),
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


# ═══════════════════════════════════════════════════════════════
# 异步 Completions API
# ═══════════════════════════════════════════════════════════════

_shared_async_client = None

# 全局 API 请求级信号量
_global_api_semaphore = None
_global_api_max_concurrency = 0


def set_api_concurrency(max_concurrent_requests: int):
    """设置全局 API 并发请求数上限。默认 0 = 不限制。"""
    global _global_api_max_concurrency, _global_api_semaphore
    _global_api_max_concurrency = max_concurrent_requests
    _global_api_semaphore = None


class _api_permit:
    """API 请求许可的异步上下文管理器。"""
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
        return False


async def _get_shared_async_client():
    """获取或创建共享 httpx.AsyncClient (参数从 YAML 加载)。"""
    global _shared_async_client
    if _shared_async_client is None or _shared_async_client.is_closed:
        _shared_async_client = httpx.AsyncClient(
            verify=_verify_tls(),
            timeout=_get_timeout_config(),
            limits=_get_async_pool_config(),
        )
    return _shared_async_client


async def async_close_client():
    """关闭共享异步客户端。"""
    global _shared_async_client
    if _shared_async_client is not None and not _shared_async_client.is_closed:
        await _shared_async_client.aclose()
        _shared_async_client = None


async def async_model_call(model_name: str, prompt: str, max_token: int,
                           stop=None, timeout=None):
    """异步 Completions API — 使用统一的 _async_call_with_retry。"""
    config = get_model_config(model_name)
    api_key = _resolve_api_key(config)
    api_base = config["url"]
    payload_defaults = _get_payload_defaults()

    payload = {
        "model": model_name,
        "prompt": prompt,
        "max_tokens": max_token,
        "temperature": payload_defaults.get('temperature', 0),
        "logprobs": payload_defaults.get('logprobs', True),
        "stop": stop,
        "extra_body": payload_defaults.get('extra_body', {"chat_template_kwargs": {"enable_thinking": False}}),
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    async def _do_async_call():
        async with _api_permit():
            client = await _get_shared_async_client()
            request_timeout = timeout if timeout is not None else _get_timeout_config()
            resp = await client.post(
                f"{api_base}/completions",
                json=payload,
                headers=headers,
                timeout=request_timeout,
            )
            resp.raise_for_status()
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

    return await _async_call_with_retry(_do_async_call)


async def async_model_stream_call(
    model_name: str,
    prompt: str,
    max_token: int,
    stop=None,
    timeout=None,
    *,
    logprobs: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Async streaming Completions API for the budget streaming state machine.

    This is intentionally separate from ``async_model_call``: streaming calls
    are used only for visible thinking/final-answer phases. Hidden trial
    answers still use non-streaming calls so confidence has complete logprobs.
    """
    config = get_model_config(model_name)
    api_key = _resolve_api_key(config)
    api_base = config["url"]
    payload_defaults = _get_payload_defaults()

    payload = {
        "model": model_name,
        "prompt": prompt,
        "max_tokens": max_token,
        "temperature": payload_defaults.get('temperature', 0),
        "logprobs": payload_defaults.get('logprobs', True) if logprobs else False,
        "stop": stop,
        "stream": True,
        "extra_body": payload_defaults.get('extra_body', {"chat_template_kwargs": {"enable_thinking": False}}),
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    async with _api_permit():
        client = await _get_shared_async_client()
        request_timeout = timeout if timeout is not None else _get_timeout_config()
        async with client.stream(
            "POST",
            f"{api_base}/completions",
            json=payload,
            headers=headers,
            timeout=request_timeout,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if raw == "[DONE]":
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choice = data.get("choices", [{}])[0]
                logprobs_data = choice.get("logprobs") or {}
                yield {
                    "content": choice.get("text", "") or "",
                    "logprobs": logprobs_data.get("token_logprobs"),
                    "tokens": logprobs_data.get("tokens"),
                    "finish_reason": choice.get("finish_reason"),
                }
