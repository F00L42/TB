import os
import json
import requests
import argparse
import time
import threading
import yaml
import urllib3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import random

# 禁用 requests 的不安全警告，保持终端输出整洁
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def load_config(config_path="config.yaml"):
    """加载 YAML 配置文件"""
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

def load_test_cases(test_cases_dir, limit=None):
    """加载评测用例"""
    test_cases = []
    for file_path in Path(test_cases_dir).glob("*.json"):
        with open(file_path, 'r', encoding='utf-8') as f:
            test_case = json.load(f)
            test_case['_file_name'] = file_path.name
            test_cases.append(test_case)
    
    if limit:
        test_cases = test_cases[:limit]
    return test_cases

def build_request_body(test_case, config):
    """构建创建任务的请求体"""
    params = {
        "skill": "code-production-evaluation",
        "git_url": test_case.get("git_url", ""),
        "branch": test_case.get("branch", ""),
        "description":  f"使用 code-production-evaluation 完成评测 \n {test_case.get('Question', '')}",
        "base_commit": test_case.get("base_commit", ""),
        "patch": test_case.get('Answer', ''),
        "ut_patch": test_case.get("ut_patch", ""),
        "build_command": test_case.get("build_command", ""),
        "settings": test_case.get("settings", "")
    }
    
    return {
        "task_info": {
            "type": "evaluation",
            "desc": "使用code-production-evaluation对以下用例完成评测.",
            "params": params
        },
        "auto_recycle": True,
        "agent_type": config.get("agent_type", "opencode"),
        "env": {
            "USER_ID": config.get("user_id", ""),
            "X_AUTH_TOKEN": config.get("x_auth_token")
        },
        # 生产环境使用template_id,beta环境直接使用以下参数,把template_id注释掉
        "template_id": config.get("template_id", 57),
        # skills按需配置,目前AR2code保持当前配置即可
        # "skills": [                          
        #     {
        #     "skill_id": "code-production-evaluation",            
        #     "version": "1.0.2"               
        #     }
        # ],
        # "model_config": {                     
        #     "model": config.get("model_id", "glm-5.2"),                 
        #     "provider_config": {               
        #         "base_url": config.get("provider_base_url", "http://oneapi.rnd.huawei.com/v1"),            
        #         "api_key": config.get("provider_api_key", "sk-1234"),
        #     "models": [                       
        #         {   
        #         "id": config.get("model_id", "glm-5.2"),             
        #         "name": config.get("model_id", "glm-5.2"),            
        #         }
        #       ]
        #     }
        # }
    }

def create_task(body, config):
    """发送请求创建任务"""
    headers = {
        "X-API-KEY": config.get("api_key", ""),
        "Content-Type": "application/json"
    }
    response = requests.post(config.get("api_url"), headers=headers, json=body, timeout=60, verify=False)
    return response

def query_task_status(task_id, config):
    """查询任务当前状态"""
    url = f"{config.get('api_url')}/{task_id}"
    headers = {
        "x-api-key": config.get("api_key", ""),
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        result_data = response.json()
        
        if response.status_code == 200 and result_data.get("success"):
            data = result_data.get("data", {})
            return {
                "success": True,
                "status": data.get("status", "UNKNOWN"),
                "branch": data.get("taskParams", "").get("branch", ""),
                "session_history_url": data.get("sessionHistoryUrl", ""),
                "error_message": data.get("errorMessage", ""),
                "create_time": data.get("createTime", ""),
                "start_time": data.get("startTime", ""),
                "end_time": data.get("endTime", "")
            }
        else:
            return {"success": False, "status": "QUERY_ERROR", "error": result_data.get("error", "未知错误")}
    except Exception as e:
        return {"success": False, "status": "QUERY_EXCEPTION", "error": str(e)}

def save_results_thread_safe(results_dict, output_file, file_lock):
    """线程安全地将结果刷入磁盘"""
    with file_lock:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(list(results_dict.values()), f, ensure_ascii=False, indent=2)

def _run_attempt(test_case, config, file_name, result_entry, results_dict, output_file, file_lock):
    """执行一次完整的 创建任务 -> 轮询 -> 落盘 流程"""
    body = build_request_body(test_case, config)

    try:
        response = create_task(body, config)
        if response.status_code == 200:
            resp_data = response.json()
            task_id = resp_data.get("data", {}).get("taskId")
            
            if task_id:
                result_entry["task_id"] = task_id
                result_entry["status"] = "RUNNING"
                result_entry["response"] = resp_data
                print(f"[{file_name}] 任务已启动，TaskId: {task_id}")
                
                results_dict[file_name] = result_entry
                save_results_thread_safe(results_dict, output_file, file_lock)
                
                while True:
                    time.sleep(config.get("poll_interval", 10))
                    
                    status_info = query_task_status(task_id, config)
                    current_status = status_info.get("status", "UNKNOWN")
                    
                    result_entry["status"] = current_status
                    if status_info.get("success"):
                        result_entry["session_history_url"] = status_info.get("session_history_url")
                        result_entry["error_message"] = status_info.get("error_message")
                        result_entry["create_time"] = status_info.get("create_time")
                        result_entry["end_time"] = status_info.get("end_time")
                    else:
                        result_entry["error"] = status_info.get("error")
                    
                    results_dict[file_name] = result_entry
                    save_results_thread_safe(results_dict, output_file, file_lock)
                    
                    if current_status not in ["RUNNING", "DISPATCHING", "CREATING", "QUEUED"]:
                        print(f"[{file_name}] 任务结束，最终状态: {current_status}")
                        break
            else:
                result_entry["status"] = "FAILED"
                result_entry["error"] = "请求成功但未返回 taskId"
                print(f"[{file_name}] 创建失败: 未返回 taskId")
        else:
            result_entry["status"] = "FAILED"
            result_entry["error"] = response.text
            print(f"[{file_name}] 请求失败，状态码: {response.status_code}")
            
    except Exception as e:
        result_entry["status"] = "EXCEPTION"
        result_entry["error"] = str(e)
        print(f"[{file_name}] 发生异常: {str(e)}")

    return result_entry["status"]

def process_single_case(test_case, config, results_dict, file_lock, output_file):
    """单个测试用例的完整生命周期管理：创建 -> 轮询 -> 写入（带重试机制）"""
    file_name = test_case.get('_file_name', 'unknown')

    result_entry = {
        "file": file_name,
        "task_id": None,
        "status": "STARTING",
        "response": None,
        "error": None
    }

    wait_time = random.randint(0, 10)
    # try:
    #     wait_time += eval(file_name.split("_")[0]) * 2
    # except:
    #     pass
    time.sleep(wait_time)
    print("WAIT" , wait_time )

    max_retries = 3
    for attempt in range(max_retries + 1):
        status = _run_attempt(test_case, config, file_name, result_entry, results_dict, output_file, file_lock)

        if status != "FAILED" or attempt >= max_retries:
            break

        if attempt == 0:
            wait = random.randint(30, 60)
        elif attempt == 1:
            wait = 60
        else:
            wait = 120
        print(f"[{file_name}] 状态为 FAILED，第 {attempt + 1}/{max_retries} 次重试，等待 {wait}s ...")
        time.sleep(wait)

    results_dict[file_name] = result_entry
    save_results_thread_safe(results_dict, output_file, file_lock)

def main():
    parser = argparse.ArgumentParser(description="批量并发运行评测任务")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--token", help="覆盖 config.yaml 中的 X_AUTH_TOKEN")
    parser.add_argument("--test-cases-dir", help="覆盖 config.yaml 中的评测用例目录")
    parser.add_argument("--interval", type=int, help="覆盖 config.yaml 中的查询间隔")
    parser.add_argument("--concurrency", type=int, help="覆盖 config.yaml 中的并发数量")
    args = parser.parse_args()
    
    # 合并配置 (命令行参数优先级更高)
    config = load_config(args.config)
    if args.token: config["token"] = args.token
    if args.test_cases_dir: config["test_cases_dir"] = args.test_cases_dir
    if args.interval: config["poll_interval"] = args.interval
    if args.concurrency: config["concurrency"] = args.concurrency
    
    # 如果必填参数仍然缺失，则报错
    if not config.get("x_auth_token") :
        print("错误: 请在 config.yaml 或通过 --token 提供 X_AUTH_TOKEN")
        return
        
    test_cases_dir = config.get("test_cases_dir", "./")
    print("=" * 60)
    print("加载评测用例...")
    test_cases = load_test_cases(test_cases_dir, config.get("limit"))
    print(f"共加载 {len(test_cases)} 个评测用例。并发度: {config.get('concurrency', 5)}")
    print("=" * 60)
    
    output_file = Path(test_cases_dir).parent / "evaluation_results.json"
    results_dict = {}
    file_lock = threading.Lock()
    
    # 启动线程池执行任务
    with ThreadPoolExecutor(max_workers=config.get("concurrency", 5)) as executor:
        futures = [
            executor.submit(process_single_case, test_case, config, results_dict, file_lock, output_file)
            for test_case in test_cases
        ]
        
        # 阻塞等待所有任务完成
        for future in as_completed(futures):
            future.result() 

    print("\n" + "=" * 60)
    print("所有用例执行完成！结果已汇总至:", output_file)
    
    finished = sum(1 for r in results_dict.values() if r["status"] == "FINISHED")
    failed = sum(1 for r in results_dict.values() if r["status"] == "FAILED")
    others = len(results_dict) - finished - failed
    print(f"  FINISHED: {finished}")
    print(f"  FAILED: {failed}")
    print(f"  其他状态: {others}")

if __name__ == "__main__":
    main()