#!/usr/bin/env python3
"""
自动化测试 JSONL 中的 request_body，检测流式返回是否有 tool_call 调用，
并可选保存所有流式 chunk 到文件。

所有可配置参数集中在文件顶部的配置区，方便修改。
"""

import json
import argparse
import sys
from typing import Optional, Dict, Any, List
import httpx
import os
os.environ['NO_PROXY'] = 'api.openai.rnd.huawei.com'
os.environ['no_proxy'] = 'api.openai.rnd.huawei.com'

# ==================================================
# 配置区 (可在此修改默认值)
# ==================================================

FILE_PATH=r"task29-pass1-26.jsonl"

# --- 命令行参数的默认值 ---
DEFAULT_START = 4         # 默认起始行号,从1开始
DEFAULT_END = 4              # 结束行号
DEFAULT_URL = "http://71.77.153.223:41016/v1/chat/completions"              # API 地址（必须提供，无默认值）
DEFAULT_TIMEOUT = 600.0          # 请求超时秒数
DEFAULT_MODEL = "qwen3.6-27b"            # 覆盖请求体中的 model 字段
DEFAULT_SAVE_CHUNKS = None      # 保存 chunk 的文件名，None 表示不保存

# --- 追加到 request_body 的固定参数 ---
APPEND_BODY = {
    "max_tokens": 32000,
    "top_p": 0.95,
    "enable_think_budget": True,
    "fixed_budget_tokens": 30000,
    "fixed_check_length": 256,
    "fixed_think_stride": 128,
    "fixed_num_thinking": 3,
    "fixed_min_think_tokens": 20,
    "fixed_prob_th": 0.9,
    "temperature": 0,
}

# ==================================================
# 辅助函数
# ==================================================

def parse_sse_line(line: str) -> Optional[Dict]:
    """解析 SSE 格式的 data: {...} 行，返回解析后的 JSON 字典，或 None。"""
    if line.startswith("data:"):
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            return None
        try:
            return json.loads(data_str)
        except json.JSONDecodeError:
            return None
    return None


def check_tool_calls_in_chunks(chunks: List[Dict]) -> bool:
    """检查 chunks 中是否存在 tool_calls"""
    for chunk in chunks:
        choices = chunk.get("choices", [])
        for choice in choices:
            delta = choice.get("delta", {})
            tool_calls = delta.get("tool_calls")
            if tool_calls:
                return True
    return False


async def test_single_request(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    headers: dict,
    timeout: float,
    save_chunks_file: Optional[str],
    line_no: Optional[int],
) -> bool:
    """发送流式请求，返回是否有 tool_call，可选保存 chunk"""
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    req_headers.update(headers)

    try:
        async with client.stream("POST", url, json=body, headers=req_headers, timeout=timeout) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                print(f"  [错误] HTTP {response.status_code}: {error_text}")
                return False

            chunks = []
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                parsed = parse_sse_line(line)
                if parsed is not None:
                    chunks.append(parsed)

            # 保存 chunk（每个 chunk 单独一行，不同 request 之间插入分隔行）
            if save_chunks_file and line_no is not None:
                with open(save_chunks_file, "a", encoding="utf-8") as fout:
                    # 分隔标记行，用于区分不同 request
                    fout.write(json.dumps({"line_no": line_no, "separator": True}, ensure_ascii=False) + "\n")
                    for chunk in chunks:
                        record = {"line_no": line_no, "chunk": chunk}
                        fout.write(json.dumps(record, ensure_ascii=False) + "\n")

            return check_tool_calls_in_chunks(chunks)

    except httpx.RequestError as e:
        print(f"  [请求异常] {e}")
        return False


# ==================================================
# 主逻辑
# ==================================================

async def main():
    parser = argparse.ArgumentParser(
        description="从 JSONL 中提取 request_body，流式发送并检测 tool_call"
    )
    parser.add_argument("--file",default=FILE_PATH, help="输入的 JSONL 文件路径")
    parser.add_argument("--start", "-m", type=int, default=DEFAULT_START,
                        help=f"起始行号（默认: {DEFAULT_START}）")
    parser.add_argument("--end", "-n", type=int, default=DEFAULT_END,
                        help=f"结束行号（包含，默认: {DEFAULT_END}）")
    parser.add_argument("--url", "-u", default=DEFAULT_URL,
                        help=f"API 地址（默认: {DEFAULT_URL}）")
    parser.add_argument("--timeout", "-t", type=float, default=DEFAULT_TIMEOUT,
                        help=f"请求超时秒数（默认: {DEFAULT_TIMEOUT}）")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="覆盖请求体中的 model 字段")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""),
                        help="可选 Bearer token；默认读取 OPENAI_API_KEY")
    parser.add_argument("--save-chunks", "-s", type=str, nargs="?", const="chunks.jsonl",
                        default=DEFAULT_SAVE_CHUNKS,
                        help="保存流式 chunk 到文件（默认文件: chunks.jsonl），不指定路径则使用默认名，不启用则不保存")
    args = parser.parse_args()

    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    # 读取 JSONL
    rows = []
    with open(args.file, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            if i < args.start:
                continue
            if i > args.end:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                body = obj.get("request_body")
                if body is None:
                    print(f"  第 {i} 行缺少 request_body，跳过")
                    continue
                if isinstance(body, str):
                    body = json.loads(body)
                # 合并配置区中的固定参数
                body = {**body, **APPEND_BODY}
                rows.append((i, body))
            except json.JSONDecodeError as e:
                print(f"  第 {i} 行 JSON 解析失败: {e}")

    if not rows:
        print("没有找到有效数据，退出。")
        return

    print(f"开始测试第 {args.start} 到 {args.end} 行，共 {len(rows)} 条有效数据")
    print(f"目标 URL: {args.url}")
    if args.save_chunks:
        print(f"流式 chunk 将保存至: {args.save_chunks}")
        # 清空/创建文件
        with open(args.save_chunks, "w", encoding="utf-8") as f:
            pass
    print("-" * 50)

    tool_call_count = 0
    async with httpx.AsyncClient() as client:
        for line_no, body in rows:
            if args.model:
                body["model"] = args.model

            print(f"第 {line_no} 行: ", end="", flush=True)
            has_tool = await test_single_request(
                client, args.url, body, headers, args.timeout,
                save_chunks_file=args.save_chunks,
                line_no=line_no
            )
            if has_tool:
                print("✅ 有 tool_call")
                tool_call_count += 1
            else:
                print("❌ 无 tool_call")

    print("-" * 50)
    print(f"完成。共测试 {len(rows)} 条，其中 {tool_call_count} 条包含 tool_call。")


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(1)
