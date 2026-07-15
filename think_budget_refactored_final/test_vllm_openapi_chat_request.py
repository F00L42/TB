# encoding: utf-8

import json
import time
import argparse
import requests

header = {'content-type': "application/json", "Inference-Type": "stream"}
BSZ = 1

def infer(question, history, stream=True):
    js = {
        "messages": [{"role": "user", "content": question}],
        "max_completion_tokens": 15536,
        "temperature": 0.7,
        "stream": stream,
        "enable_think_budget": True,
    }

    prev_time = time.perf_counter()
    infer_start_time = time.perf_counter()
    first_token_time = 0
    all_time = 0
    result = ''

    response = requests.post(url, json=js, headers=header, stream=stream, verify=False)
    incre_time_list = []

    if stream:
        if response.status_code == 200:
            print("stream model answer:")
            for chunk in response.iter_content(chunk_size=None):
                chunk = chunk.decode('utf-8')
                cur_time = time.perf_counter()
                if chunk.startswith('data:'):
                    chunk = chunk.lstrip('data:').strip()
                    if "DONE" in chunk:
                        break
                    try:
                        data = json.loads(chunk)['choices'][0]['delta']['content']
                    except (json.JSONDecodeError, KeyError):
                        continue
                    print(data, end='', flush=True)
                    result += data

                    if first_token_time == 0:
                        first_token_time = round((cur_time - prev_time) * 1000, 2)
                        prev_time = cur_time
                    else:
                        incre_time = cur_time - prev_time
                        prev_time = cur_time
                        incre_time_list.append(round(incre_time * 1000, 2))
                else:
                    continue
            # ---------- 改动点 1：补换行并打印完整结果 ----------
            print()  # 确保后续输出另起一行
            print(f"【完整结果】{result}")
        else:
            print(f'Failed to request, code: {response.status_code}')
            return result, 0
    else:
        try:
            res = response.json()['choices'][0]['message']['content']
        except (KeyError, json.JSONDecodeError):
            print("Invalid response format")
            return '', 0
        print(f"answer:\n{res}")
        result = res

    if incre_time_list:
        avg_incre_time = round(sum(incre_time_list) / len(incre_time_list), 2)
    else:
        avg_incre_time = 0.0

    all_time = round(time.perf_counter() - infer_start_time, 2)

    print(f"\n\n######################## 性能 ########################")
    print(f"incre_time_list: {incre_time_list}")
    print(f"首字时延: {first_token_time}ms, 平均增量时延: {avg_incre_time}ms, 推理总时间: {all_time}s")
    print(f"######################## -- ########################")

    return result, all_time


def interactive_answer(args):
    print("Start inference with interactive mode.")
    while True:
        raw_input_text = input("Input:\n")
        if len(raw_input_text.strip()) == 0 or raw_input_text.lower() == "quit":
            break
        # ------------------- 关键修改 -------------------
        content = raw_input_text   # 使用用户输入，不再硬编码
        # ----------------------------------------------
        result, _ = infer(content, [[]], args.stream)
        print(f"\n外层完整结果：{result}")
        print("\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="71.77.153.223")
    parser.add_argument("--port", default="41022")
    parser.add_argument("--model_name", default="")
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()

    url = f'http://{args.ip}:{args.port}/v1/chat/completions'
    print(f"url: {url}")
    interactive_answer(args)