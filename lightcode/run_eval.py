import os
import json
import requests
import argparse
import time
from pathlib import Path


API_URL = "https://xingyun.rnd.huawei.com/gateway/AiAgentManageService/v1/lightcode/tasks"
API_KEY = "sk-c6461c64d939411d94188a15f0fc430a"
USER_ID = "z00933250"
TEMPLATE_ID = 44
TEST_CASES_DIR = r"D:\评测\评测用例"


def load_test_cases(test_cases_dir):
    test_cases = []
    for file_path in Path(test_cases_dir).glob("*.json"):
        with open(file_path, 'r', encoding='utf-8') as f:
            test_case = json.load(f)
            test_case['_file_name'] = file_path.name
            test_cases.append(test_case)
    return test_cases


def build_request_body(test_case):
    params = {
        "skill": "code-production-evaluation",
        "git_url": test_case.get("git_url", ""),
        "branch": test_case.get("branch", ""),
        "description": test_case.get("description", ""),
        "base_commit": test_case.get("base_commit", ""),
        "patch": test_case.get("patch", ""),
        "ut_patch": test_case.get("ut_patch", ""),
        "build_command": test_case.get("build_command", ""),
        "settings": test_case.get("settings", "")
    }
    
    body = {
        "taskInfo": {
            "type": "evaluation",
            "desc": "请使用code-production-evaluation对以下用例完成评测",
            "params": params
        },
        "autoRecycle": True,
        "template_id": TEMPLATE_ID,
        "env": {
            "USER_ID": USER_ID,
            "X_AUTH_TOKEN": ""
        }
    }
    
    return body


def send_request(body, x_auth_token):
    body["env"]["X_AUTH_TOKEN"] = x_auth_token
    
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }
    
    response = requests.post(API_URL, headers=headers, json=body, timeout=60, verify=False)
    return response


def main():
    parser = argparse.ArgumentParser(description="批量运行评测任务")
    parser.add_argument("--token", required=True, help="X_AUTH_TOKEN值")
    parser.add_argument("--test-cases-dir", default=TEST_CASES_DIR, help=f"评测用例目录（默认: {TEST_CASES_DIR}）")
    parser.add_argument("--limit", type=int, default=None, help="限制运行的用例数量（可选）")
    parser.add_argument("--interval", type=int, default=10, help="任务发送间隔时间（秒，默认: 10）")
    args = parser.parse_args()
    
    print("=" * 60)
    print("加载评测用例...")
    test_cases = load_test_cases(args.test_cases_dir)
    print(f"共加载 {len(test_cases)} 个评测用例")
    
    if args.limit:
        test_cases = test_cases[:args.limit]
        print(f"限制运行前 {args.limit} 个用例")
    
    print("=" * 60)
    print("开始执行评测任务...")
    print("=" * 60)
    
    results = []
    
    for idx, test_case in enumerate(test_cases, 1):
        file_name = test_case.get('_file_name', 'unknown')
        print(f"\n[{idx}/{len(test_cases)}] 处理用例: {file_name}")
        
        body = build_request_body(test_case)
        
        try:
            print(f"  发送请求...")
            response = send_request(body, args.token)
            print(f"  状态码: {response.status_code}")
            
            if response.status_code == 200:
                result_data = response.json()
                print(f"  响应: {json.dumps(result_data, ensure_ascii=False)[:200]}...")
                results.append({
                    "file": file_name,
                    "status": "success",
                    "response": result_data
                })
            else:
                print(f"  错误: {response.text[:200]}")
                results.append({
                    "file": file_name,
                    "status": "failed",
                    "error": response.text
                })
        except Exception as e:
            print(f"  异常: {str(e)}")
            results.append({
                "file": file_name,
                "status": "error",
                "error": str(e)
            })
        
        # 添加间隔时间，避免对服务造成过大压力
        if idx < len(test_cases) and args.interval > 0:
            print(f"  等待 {args.interval} 秒后继续...")
            time.sleep(args.interval)
    
    print("\n" + "=" * 60)
    print("执行完成！结果汇总:")
    success_count = sum(1 for r in results if r["status"] == "success")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    error_count = sum(1 for r in results if r["status"] == "error")
    print(f"  成功: {success_count}")
    print(f"  失败: {failed_count}")
    print(f"  异常: {error_count}")
    
    output_file = Path(args.test_cases_dir).parent / "evaluation_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {output_file}")


if __name__ == "__main__":
    main()
