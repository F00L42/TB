import os
import json
import requests
import argparse
from pathlib import Path
import pandas as pd
from datetime import datetime


API_URL = "https://beta.xingyun.rnd.huawei.com/gateway/AiAgentManageService/v1/lightcode/tasks"
DEFAULT_API_KEY = "sk-c6461c64d939411d94188a15f0fc430a"
DEFAULT_EXCEL_FILE = r"result.xlsx"


def query_task_status(task_id, api_key):
    url = f"{API_URL}/{task_id}"
    
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        result_data = response.json()
        
        if response.status_code == 200 and result_data.get("success"):
            return {
                "success": True,
                "task_id": task_id,
                "status": result_data.get("data", {}).get("status", ""),
                "result": result_data.get("data", {}),
                "error": ""
            }
        else:
            return {
                "success": False,
                "task_id": task_id,
                "status": "",
                "result": {},
                "error": result_data.get("error", "未知错误")
            }
    except Exception as e:
        return {
            "success": False,
            "task_id": task_id,
            "status": "",
            "result": {},
            "error": str(e)
        }


def load_task_ids_from_file(file_path):
    """从JSON文件读取任务ID和相关信息"""
    tasks_info = []
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "response" in item:
                    response_data = item["response"].get("data", {})
                    task_id = response_data.get("taskId", "")
                    if task_id:
                        tasks_info.append({
                            "file": item.get("file", ""),
                            "task_id": task_id,
                            "envId": response_data.get("envId", ""),
                            "status": response_data.get("status", ""),
                            "sessionId": response_data.get("sessionId", "")
                        })
    
    return tasks_info


def create_excel_from_json(json_file, excel_file):
    """从JSON文件创建Excel表格"""
    tasks_info = load_task_ids_from_file(json_file)
    
    if not tasks_info:
        print(f"警告：JSON文件中没有找到任务信息")
        return False
    
    df = pd.DataFrame(tasks_info)
    df.columns = ['用例名', '任务ID', 'envId', '状态', 'sessionId']
    
    # 确保目录存在
    Path(excel_file).parent.mkdir(parents=True, exist_ok=True)
    
    df.to_excel(excel_file, index=False, engine='openpyxl')
    print(f"✓ 已创建Excel表格: {excel_file}")
    print(f"  共写入 {len(tasks_info)} 个任务")
    return True


def update_excel_with_status(excel_file, task_id, new_status, session_id):
    """更新Excel中指定任务的状态"""
    if not Path(excel_file).exists():
        return False
    
    df = pd.read_excel(excel_file, engine='openpyxl')
    
    # 找到对应的任务行
    mask = df['任务ID'] == task_id
    if mask.any():
        df.loc[mask, '最新状态'] = new_status
        df.loc[mask, 'sessionId'] = session_id
        df.to_excel(excel_file, index=False, engine='openpyxl')
        return True
    
    return False


def main():
    parser = argparse.ArgumentParser(description="查询评测任务状态")
    parser.add_argument("--task-id", help="单个任务ID")
    parser.add_argument("--task-ids", help="多个任务ID，逗号分隔")
    parser.add_argument("--from-file", help="从JSON文件读取任务ID列表")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API Key（可选，默认使用脚本内置的key）")
    parser.add_argument("--output", default="task_status_results.json", help="结果输出文件")
    parser.add_argument("--excel", default=DEFAULT_EXCEL_FILE, help=f"Excel表格文件（默认: {DEFAULT_EXCEL_FILE}）")
    parser.add_argument("--json-source", help="分发脚本生成的JSON文件（如果不指定，自动在Excel同目录查找）")
    args = parser.parse_args()
    
    api_key = args.api_key
    excel_file = args.excel
    
    # 确定JSON源文件路径：如果用户指定了就用指定的，否则自动在Excel同目录查找
    if args.json_source:
        json_source = args.json_source
    else:
        # 自动在Excel同目录下查找evaluation_results.json
        json_source = str(Path(excel_file).parent / "evaluation_results.json")
    
    # 检查Excel是否存在，如果不存在则从JSON创建
    if not Path(excel_file).exists():
        print("=" * 60)
        print("Excel表格不存在，正在从JSON文件创建...")
        print("=" * 60)
        
        if Path(json_source).exists():
            if not create_excel_from_json(json_source, excel_file):
                print("错误：无法从JSON文件创建Excel")
                return
        else:
            print(f"错误：JSON文件不存在: {json_source}")
            print("请先运行分发脚本生成任务，或手动指定JSON文件路径(--json-source)")
            return
    
    # 从Excel读取任务ID
    df = pd.read_excel(excel_file, engine='openpyxl')
    task_ids = df['任务ID'].tolist()
    
    if not task_ids:
        print("错误：Excel中没有任务ID")
        return
    
    print("=" * 60)
    print(f"准备查询 {len(task_ids)} 个任务状态...")
    print(f"Excel文件: {excel_file}")
    print("=" * 60)
    
    results = []
    
    for idx, task_id in enumerate(task_ids, 1):
        print(f"\n[{idx}/{len(task_ids)}] 查询任务: {task_id}")
        
        result = query_task_status(task_id, api_key)
        
        if result["success"]:
            print(f"  ✓ 状态: {result['status']}")
            
            # 更新Excel
            session_id = result['result'].get('sessionId', '')
            if update_excel_with_status(excel_file, task_id, result['status'], session_id):
                print(f"  ✓ Excel已更新")
            else:
                print(f"  ⚠ Excel更新失败")
        else:
            print(f"  ✗ 错误: {result['error']}")
        
        results.append(result)
    
    print("\n" + "=" * 60)
    print("查询完成！结果汇总:")
    success_count = sum(1 for r in results if r["success"])
    failed_count = sum(1 for r in results if not r["success"])
    print(f"  成功: {success_count}")
    print(f"  失败: {failed_count}")
    
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {args.output}")
    print(f"Excel已更新: {excel_file}")


if __name__ == "__main__":
    main()
