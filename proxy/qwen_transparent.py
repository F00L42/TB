import json
import logging
import asyncio
from datetime import datetime
from urllib.parse import urlparse, urlunparse
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx

import os
os.environ['NO_PROXY'] = 'api.openai.rnd.huawei.com'
os.environ['no_proxy'] = 'api.openai.rnd.huawei.com'


# ================= 配置区 =================
# 1. 修改 RESULTS_DIR 即可自动更新所有日志文件路径
#    路径格式: results/{模型}/{任务}/{接收方}/{dtb配置}/run-{序号}
RESULTS_DIR = "results/qwen/local-task29/local-apiserver/dtb/run-01"

# 2. 上游服务器配置
# TARGET_BASE_URL = "http://oneapi.rnd.huawei.com" # 替换为实际 Base URL (不带 /v1)
TARGET_BASE_URL = "http://10.44.160.79:9001"  
# TARGET_BASE_URL = "http://10.136.110.148:8000"  
LISTEN_PORT = 9000

# 3. 日志文件路径
os.makedirs(RESULTS_DIR, exist_ok=True)
TOKEN_RECORD_FILE = os.path.join(RESULTS_DIR, "token_usage.jsonl")
FULL_LOG_FILE = os.path.join(RESULTS_DIR, "full_log.jsonl")
CHUNKS_LOG_FILE = os.path.join(RESULTS_DIR, "chunks.jsonl")

# 4. 模型与请求参数
APPEND_BODY = {
    "model": "qwen3.6-27b",
    # "max_tokens": 32000,
    # "top_p": 1,
    # "enable_budget": True,
    # "fixed_budget_tokens": 30000,
    # "fixed_check_length": 100,
    # "fixed_think_stride": 50,
    # "fixed_num_thinking": 2,
    # "fixed_min_think_tokens": 20,
    # "fixed_prob_th": 0.9,
    # "temperature": 0,
}
# ==========================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("api_proxy")

app = FastAPI(title="API Proxy Server")

# 精细化超时控制：读取超时设为 600 秒，防止长文本生成被截断
timeout_config = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)

EXCLUDED_REQ_HEADERS = {"host", "content-length", "connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade"}
EXCLUDED_RESP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}

# =================  URL 智能拼接 =================
def build_upstream_url(base_url: str, request_path: str) -> str:
    parsed_base = urlparse(base_url)
    base_path = parsed_base.path.rstrip('/')
    req_path = '/' + request_path.lstrip('/')
    
    if base_path and req_path.startswith(base_path):
        req_path = req_path[len(base_path):]
        if not req_path.startswith('/'): req_path = '/' + req_path
            
    final_path = base_path + req_path
    return urlunparse((parsed_base.scheme, parsed_base.netloc, final_path, '', '', ''))

# =================  日志保存 (Token) =================
def save_token_record_sync(post_time, receive_time, usage, model_name):
    reasoning_token = usage.get("reasoning_tokens", 0) or usage.get("reasoning_token", 0)
    tool_token = usage.get("tool_tokens", 0) or usage.get("tool_token", 0)

    record = {
        "post_time": post_time,
        "recive_time": receive_time,
        "model_name": model_name,
        "intput_token": usage.get("prompt_tokens", 0),
        "output_token": usage.get("completion_tokens", 0),
        "reasoning_token": reasoning_token,
        "tool_token": tool_token,
        "usage": usage
    }
    try:
        with open(TOKEN_RECORD_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"保存 Token 记录失败: {e}")

async def async_save_token_record(post_time, usage, model_name):
    receive_time = datetime.now().isoformat()
    await asyncio.to_thread(save_token_record_sync, post_time, receive_time, usage, model_name)

# =================  日志保存 (完整 Request/Response) =================
def save_full_log_sync(timestamp, request_body, response_content,response_reasoning=None,response_tool_calls=None):
    record = {
        "timestamp": timestamp,
        "request_body": request_body,
        # "response_body": response_content,
        "response_data": {"content": response_content,"reasoning": response_reasoning,"tool_calls": response_tool_calls}
    }
    try:
        with open(FULL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"保存完整日志失败: {e}")

async def async_save_full_log(timestamp, request_body, response_content,response_reasoning=None,response_tool_calls=None):
    await asyncio.to_thread(save_full_log_sync, timestamp, request_body, response_content,response_reasoning,response_tool_calls)

# =================  日志保存 (流式 Chunk) =================
def save_chunk_sync(timestamp, chunk):
    record = {
        "timestamp": timestamp,
        "chunk": chunk
    }
    try:
        with open(CHUNKS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"保存 chunk 日志失败: {e}")

async def async_save_chunk(timestamp, chunk):
    await asyncio.to_thread(save_chunk_sync, timestamp, chunk)

# =================  路由与透传 =================
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_request(request: Request, path: str):
    post_time = datetime.now().isoformat()
    body = await request.body()

    # 安全解析请求体
    request_body_to_log = {}
    try:
        if body:
            request_body_to_log = json.loads(body)
    except json.JSONDecodeError:
        request_body_to_log = {"raw_text": body.decode('utf-8', errors='ignore')[:500]}
        
    model_name = request_body_to_log.get("model", "")
    is_stream = request_body_to_log.get("stream", False)
    upstream_url = build_upstream_url(TARGET_BASE_URL, path)
    forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in EXCLUDED_REQ_HEADERS}
    
    if is_stream and "stream_options" not in request_body_to_log:
        request_body_to_log["stream_options"] = {"include_usage": True}

    # 将 APPEND_BODY 合并进请求体字典，再序列化为 bytes
    request_body_to_log = {**request_body_to_log, **APPEND_BODY}
    body = json.dumps(request_body_to_log).encode('utf-8')

    # 使用独立的 AsyncClient 实例，避免全局共享 client 导致的连接池冲突
    client = httpx.AsyncClient(timeout=timeout_config)
    stream_ctx = client.stream(
        method=request.method, url=upstream_url, headers=forward_headers,
        params=request.query_params, content=body
    )
    upstream_resp_obj = await stream_ctx.__aenter__()

    resp_headers = {k: v for k, v in upstream_resp_obj.headers.items() if k.lower() not in EXCLUDED_RESP_HEADERS}

    if is_stream and "text/event-stream" in upstream_resp_obj.headers.get("content-type", ""):
        return await handle_stream_response(upstream_resp_obj, client, stream_ctx, resp_headers, model_name, post_time, request_body_to_log)
    else:
        try:
            content = await upstream_resp_obj.aread()
            status_code = upstream_resp_obj.status_code
        finally:
            await stream_ctx.__aexit__(None, None, None)
            await client.aclose()
        return await handle_normal_response_from_content(content, status_code, resp_headers, model_name, post_time, request_body_to_log)

# =================  响应处理 =================
async def handle_normal_response_from_content(content: bytes, status_code: int, resp_headers, model_name, post_time, request_body):
    usage = {}
    response_content = ""
    response_reasoning = None
    response_tool_calls = None
    try:
        resp_json = json.loads(content)
        usage = resp_json.get("usage", {})
        choices = resp_json.get("choices", [])
        if choices:
            response_content = choices[0].get("message", {}).get("content", "")
            response_reasoning = choices[0].get("message", {}).get("reasoning", "")
            response_tool_calls = choices[0].get("message", {}).get("tool_calls", "")
    except json.JSONDecodeError:
        response_content = content.decode('utf-8', errors='ignore')

    await async_save_full_log(post_time, request_body, response_content,response_reasoning,response_tool_calls)
    await async_save_token_record(post_time, usage, model_name)
    
    return Response(content=content, status_code=status_code, headers=resp_headers)

async def handle_stream_response(upstream_resp, client, stream_ctx, resp_headers, model_name, post_time, request_body):
    usage = {}
    accumulated_content = ""
    accumulated_reasoning = ""
    accumulated_tool_call = ""
    async def stream_generator():
        nonlocal usage, accumulated_content, accumulated_reasoning, accumulated_tool_call
        # 流开始时写入分隔标记行，用于区分不同 request
        await async_save_chunk(post_time, {"separator": True})
        try:
            async for chunk in upstream_resp.aiter_lines():
                yield f"{chunk}\n".encode('utf-8')
                
                if chunk.startswith("data: "):
                    data_str = chunk[6:].strip()
                    if data_str == "[DONE]": continue
                    try:
                        data_json = json.loads(data_str)
                        # 保存每个流式 chunk
                        await async_save_chunk(post_time, data_json)
                        
                        # 累积流式输出的 content
                        choices = data_json.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if delta.get("content"):
                                accumulated_content += delta["content"]

                            if delta.get("reasoning"):
                                accumulated_reasoning += delta["reasoning"]

                            if delta.get("tool_calls"):
                                accumulated_tool_call += json.dumps(delta["tool_calls"], ensure_ascii=False)

                        
                        # 提取 usage (通常在最后一个 chunk)
                        if "usage" in data_json and data_json["usage"]:
                            usage = data_json["usage"]
                    except json.JSONDecodeError:
                        continue
        finally:
            # 确保上游流和 client 被正确关闭
            await stream_ctx.__aexit__(None, None, None)
            await client.aclose()

        # 流结束后，保存日志
        await async_save_full_log(post_time, request_body, accumulated_content,accumulated_reasoning,accumulated_tool_call)
        await async_save_token_record(post_time, usage, model_name)

    return StreamingResponse(stream_generator(), status_code=upstream_resp.status_code, headers=resp_headers)

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting Proxy Server on port {LISTEN_PORT}")
    logger.info(f"Full logs will be saved in: '{FULL_LOG_FILE}'")
    uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT)