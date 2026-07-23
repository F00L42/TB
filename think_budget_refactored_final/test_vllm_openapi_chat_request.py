# encoding: utf-8
"""OpenAI-compatible chat/completions streaming test client.

服务端 budget 流式遵循 reasoning/content 分离：
- delta.reasoning_content: 思考内容
- delta.content: 最终答案

如果客户端只拼接 delta.content，会只看到最终答案，这是符合当前 API 语义的。
"""

import argparse
import json
import time

import requests


HEADERS = {"content-type": "application/json", "Inference-Type": "stream"}


def _build_payload(question: str, stream: bool, model_name: str) -> dict:
    payload = {
        "messages": [{"role": "user", "content": question}],
        # 服务端同时兼容 max_completion_tokens；这里使用 canonical max_tokens。
        "max_tokens": 15536,
        "temperature": 0.7,
        "stream": stream,
        # 服务端同时兼容 enable_think_budget；这里使用 canonical enable_budget。
        "enable_budget": True,
        "fixed_prob_th": 0.001,
        "fixed_check_length": 512,
        "fixed_think_stride": 512,
        "fixed_num_thinking": 4,
        "fixed_min_think_tokens": 100,
        "fixed_budget_tokens": 5000,
    }
    if model_name:
        payload["model"] = model_name
    return payload


def infer(url: str, question: str, stream: bool = True, model_name: str = ""):
    payload = _build_payload(question, stream, model_name)
    infer_start_time = time.perf_counter()
    prev_time = infer_start_time
    first_token_time = 0
    incre_time_list = []
    reasoning_parts = []
    answer_parts = []

    response = requests.post(
        url,
        json=payload,
        headers=HEADERS,
        stream=stream,
        verify=False,
        timeout=600,
    )

    if response.status_code != 200:
        print(f"Failed to request, code: {response.status_code}")
        print(response.text)
        return "", 0

    if stream:
        print("stream model answer:")
        current_section = None
        # chunk_size=1 避免 requests 默认 512B 缓冲导致小 SSE 片段延迟打印。
        for line in response.iter_lines(chunk_size=1, decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue

            raw = line[len("data:"):].strip()
            if raw == "[DONE]":
                break

            cur_time = time.perf_counter()
            try:
                event = json.loads(raw)
                delta = event["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue

            reasoning_piece = delta.get("reasoning_content") or ""
            answer_piece = delta.get("content") or ""

            if reasoning_piece:
                if current_section != "reasoning":
                    print("\n【思考内容】")
                    current_section = "reasoning"
                print(reasoning_piece, end="", flush=True)
                reasoning_parts.append(reasoning_piece)

            if answer_piece:
                if current_section != "answer":
                    print("\n【最终正式答案】")
                    current_section = "answer"
                print(answer_piece, end="", flush=True)
                answer_parts.append(answer_piece)

            if reasoning_piece or answer_piece:
                if first_token_time == 0:
                    first_token_time = round((cur_time - prev_time) * 1000, 2)
                else:
                    incre_time_list.append(round((cur_time - prev_time) * 1000, 2))
                prev_time = cur_time
        print()
    else:
        try:
            message = response.json()["choices"][0]["message"]
        except (KeyError, json.JSONDecodeError, IndexError, TypeError):
            print("Invalid response format")
            print(response.text)
            return "", 0

        reasoning = message.get("reasoning_content") or ""
        answer = message.get("content") or ""
        reasoning_parts.append(reasoning)
        answer_parts.append(answer)
        print(f"【思考内容】\n{reasoning}")
        print(f"\n【最终正式答案】\n{answer}")

    reasoning = "".join(reasoning_parts)
    answer = "".join(answer_parts)
    result = reasoning + answer

    avg_incre_time = round(sum(incre_time_list) / len(incre_time_list), 2) if incre_time_list else 0.0
    all_time = round(time.perf_counter() - infer_start_time, 2)

    print(f"\n【统计】思考字符数:{len(reasoning)} 答案字符数:{len(answer)}")
    print("\n\n######################## 性能 ########################")
    print(f"incre_time_list: {incre_time_list}")
    print(f"首字时延: {first_token_time}ms, 平均增量时延: {avg_incre_time}ms, 推理总时间: {all_time}s")
    print("######################## -- ########################")

    return result, all_time


def interactive_answer(args):
    url = f"http://{args.ip}:{args.port}/v1/chat/completions"
    print(f"url: {url}")
    print("Start inference with interactive mode. Empty input or quit exits.")
    while True:
        raw_input_text = input("Input:\n")
        if not raw_input_text.strip() or raw_input_text.lower() == "quit":
            break
        result, _ = infer(url, raw_input_text, args.stream, args.model_name)
        print(f"\n外层完整结果：{result}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="75.255.57.109")
    parser.add_argument("--port", default="8000")
    parser.add_argument("--model_name", default="")
    parser.add_argument("--stream", dest="stream", action="store_true", default=True)
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    interactive_answer(parser.parse_args())
