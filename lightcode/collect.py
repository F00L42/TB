import os
import json
import requests
import zipfile
import pandas as pd
import urllib3
from pathlib import Path
from datetime import datetime

# 禁用不安全请求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 保持与 run_eval.py 一致
API_URL = "https://beta.xingyun.rnd.huawei.com/gateway/AiAgentManageService/v1/lightcode/tasks"
API_KEY = "sk-c6461c64d939411d94188a15f0fc430a"


def query_task_status(task_id):
    """查询任务状态并获取完整信息"""
    url = f"{API_URL}/{task_id}"
    
    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        result_data = response.json()
        
        if response.status_code == 200 and result_data.get("success"):
            data = result_data.get("data", {})
            return {
                "success": True,
                "task_id": task_id,
                "status": data.get("status", ""),
                "session_history_url": data.get("sessionHistoryUrl", ""),
                "error_message": data.get("errorMessage", ""),
                "create_time": data.get("createTime", ""),
                "start_time": data.get("startTime", ""),
                "end_time": data.get("endTime", ""),
                "full_data": data
            }
        else:
            return {
                "success": False,
                "task_id": task_id,
                "error": result_data.get("error", "未知错误")
            }
    except Exception as e:
        return {
            "success": False,
            "task_id": task_id,
            "error": str(e)
        }


def download_and_extract_session_zip(session_url, download_dir):
    """下载并解压session历史zip文件"""
    try:
        print(f"  下载session zip: {session_url}")
        response = requests.get(session_url, timeout=60, verify=False)
        
        if response.status_code == 200:
            zip_path = os.path.join(download_dir, "session.zip")
            with open(zip_path, 'wb') as f:
                f.write(response.content)
            
            print(f"  解压到: {download_dir}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(download_dir)
            
            os.remove(zip_path)
            return True
        else:
            print(f"  下载失败: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"  下载异常: {str(e)}")
        return False


def parse_evaluation_report(log_dir):
    """从evaluation_report JSON文件中读取评测结果"""
    evaluation_result = {
        "task_status": "",
        "code_generation_status": "",
        "compilation_status": "",
        "dt_total_tests": 0,
        "dt_passed_tests": 0,
        "dt_failed_tests": 0,
        "dt_pass_rate": "N/A",
        "grade": "",
        "error_details": "",
        "files_count": 0,
        "files_generated": [],
        "summary": ""
    }
    
    # 查找evaluation_report JSON文件
    json_files = list(Path(log_dir).glob("**/evaluation_report_*.json"))
    
    if not json_files:
        print(f"  未找到evaluation_report JSON文件")
        return evaluation_result
    
    json_file = json_files[0]
    print(f"  找到评测报告: {json_file.name}")
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)
            
            # 解析代码生成状态
            code_gen = report_data.get("code_generation", {})
            evaluation_result["code_generation_status"] = code_gen.get("status", "")
            
            # 解析编译状态
            compilation = report_data.get("compilation", {})
            evaluation_result["compilation_status"] = compilation.get("status", "")
            evaluation_result["error_details"] = compilation.get("error_message", "")
            
            # 解析DT结果
            dt_results = report_data.get("dt_results", {})
            evaluation_result["dt_total_tests"] = dt_results.get("total_tests", 0)
            evaluation_result["dt_passed_tests"] = dt_results.get("passed_tests", 0)
            evaluation_result["dt_failed_tests"] = dt_results.get("failed_tests", 0)
            evaluation_result["dt_pass_rate"] = dt_results.get("pass_rate", "N/A")
            
            # 解析评测结论
            conclusion = report_data.get("evaluation_conclusion", {})
            evaluation_result["grade"] = conclusion.get("grade", "")
            evaluation_result["summary"] = conclusion.get("summary", "")
            
            # 解析需求信息
            requirement = report_data.get("requirement", {})
            evaluation_result["files_count"] = requirement.get("files_count", 0)
            evaluation_result["files_generated"] = requirement.get("files_generated", [])
            
    except Exception as e:
        print(f"  解析评测报告失败: {str(e)}")
    
    return evaluation_result


def collect_evaluation_results(task_ids, output_excel):
    """批量收集评测结果并生成Excel报告"""
    
    print("=" * 60)
    print(f"准备收集 {len(task_ids)} 个任务的评测结果...")
    print("=" * 60)
    
    results = []
    download_base_dir = Path("./session_logs")
    
    for idx, task_id in enumerate(task_ids, 1):
        print(f"\n[{idx}/{len(task_ids)}] 处理任务: {task_id}")
        
        # 查询任务状态
        task_info = query_task_status(task_id)
        
        if not task_info["success"]:
            print(f"  ✗ 查询失败: {task_info['error']}")
            results.append({
                "任务ID": task_id,
                "任务状态": "查询失败",
                "错误信息": task_info["error"]
            })
            continue
        
        print(f"  ✓ 任务状态: {task_info['status']}")
        try:
            time_diff = None
 
            start = datetime.strptime(task_info["create_time"].strip(),"%Y-%m-%dT%H:%M:%S.%f")
            end = datetime.strptime(task_info["end_time"].strip(),"%Y-%m-%dT%H:%M:%S.%f")
            delta = end - start
            total_seconds = delta.total_seconds()
        except:
            raise
        
        # 从 full_data 中的 taskParams 提取 branch 等信息
        full_data = task_info.get("full_data", {})
        task_params = full_data.get("taskParams", {}) if isinstance(full_data, dict) else {}
        branch = task_params.get("branch", "N/A") if isinstance(task_params, dict) else "N/A"
        
        result_row = {
            "任务ID": task_id,
            "分支": branch,
            "任务状态": task_info["status"],
            "创建时间": task_info["create_time"],
            "开始时间": task_info["start_time"],
            "结束时间": task_info["end_time"],
            "耗时":total_seconds,
            "S3链接": task_info["session_history_url"] or "无",
            "错误信息": task_info["error_message"] or "无"
        }
        
        
        # 如果任务已完成且有s3链接
        if task_info["status"] in ["FINISHED", "FAILED"] and task_info["session_history_url"]:
            # 创建下载目录
            download_dir = download_base_dir / task_id
            download_dir.mkdir(parents=True, exist_ok=True)
            
            # 下载并解压session zip
            if download_and_extract_session_zip(task_info["session_history_url"], str(download_dir)):
                # 解析评测报告JSON
                evaluation_result = parse_evaluation_report(str(download_dir))
                
                result_row.update({
                    "代码生成状态": evaluation_result["code_generation_status"],
                    "编译状态": evaluation_result["compilation_status"],
                    "生成文件数": evaluation_result["files_count"],
                    "DT总测试数": evaluation_result["dt_total_tests"],
                    "DT通过数": evaluation_result["dt_passed_tests"],
                    "DT失败数": evaluation_result["dt_failed_tests"],
                    "DT通过率": evaluation_result["dt_pass_rate"],
                    "评级": evaluation_result["grade"],
                    "详细错误": evaluation_result["error_details"],
                    "评测总结": evaluation_result["summary"]
                })
        
        results.append(result_row)
    
    # 生成Excel报告
    print("\n" + "=" * 60)
    print("生成Excel报告...")
    
    df = pd.DataFrame(results)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_path = Path(output_excel).parent / f"evaluation_report_{timestamp}.xlsx"
    
    df.to_excel(excel_path, index=False, engine='openpyxl')
    
    print(f"✓ 报告已保存到: {excel_path}")
    print("=" * 60)
    
    # 打印汇总
    success_count = sum(1 for r in results if r["任务状态"] == "FINISHED")
    failed_count = sum(1 for r in results if r["任务状态"] == "FAILED")
    other_count = len(results) - success_count - failed_count
    
    print(f"\n结果汇总:")
    print(f"  完成: {success_count}")
    print(f"  失败: {failed_count}")
    print(f"  其他: {other_count}")
    
    return excel_path


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="收集评测结果并生成Excel报告")
    parser.add_argument("--task-ids", required=False, help="任务ID列表，逗号分隔")
    parser.add_argument("--from-file", help="从JSON文件读取任务ID")
    parser.add_argument("--output", default="./evaluation_report.xlsx", help="输出Excel文件路径")
    
    args = parser.parse_args()
    
    task_ids = []
    
    if args.task_ids:
        task_ids.extend(args.task_ids.split(","))
    
    if args.from_file:
        with open(args.from_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        # 【修改2】兼容新版 JSON 结构，直接取顶层的 task_id
                        task_id = item.get("task_id")
                        
                        # 如果顶层没有（兼容旧格式），再尝试去 response 里找
                        if not task_id and "response" in item and isinstance(item["response"], dict):
                            task_id = item["response"].get("data", {}).get("taskId", "")
                            
                        if task_id:
                            task_ids.append(task_id)
    
    if not task_ids:
        print("错误：未找到有效的任务ID")
        return
    
    collect_evaluation_results(task_ids, args.output)


if __name__ == "__main__":
    main()